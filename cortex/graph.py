"""
Cortex's LangGraph Cognitive Engine.

Control Flow:
  detect -> retrieve -> [precedent found?]
                            |-- yes -> diff vs stored snapshot
                            |            |-- no diff & prior success -> flag + full investigate (recurrence)
                            |            |-- no diff, no contradiction -> reuse fix instantly
                            |            `-- diff found -> seed investigate with what changed
                            `-- no  -> full investigate (cold start + graph traversal)
         -> propose_fix -> human_review -> store (always) -> reflect -> [promote if earned]

Features:
  - Multi-hop upstream and downstream lineage graph traversal.
  - Triage & incident classification for generic/unclassified alerts.
  - Recurrence contradiction detection on warm paths.
  - Reflection gatekeeper for DataHub promotion.
"""

import dataclasses
from datetime import datetime
from time import timezone
from typing import Any, Dict, Optional, TypedDict
import uuid

from langgraph.graph import END, StateGraph

from cortex import config
from cortex import memory_episodic
from cortex.diff import compute_diff,compute_lineage_diff
from cortex.llm import diagnose_root_cause, generate_fix
from cortex.memory_episodic import EpisodicMemory
from cortex.memory_semantic import DataHubClient
from cortex.models import AssetSnapshot, Experience, Incident
from cortex.procedure import load_procedure
from cortex.reflection import check_recurrence_despite_no_diff, reflect

log = config.get_logger("cortex.graph")

# --- State Definition ---------------------------------------------------

class CortexState(TypedDict, total=False):
    incident: Incident
    procedure: dict

    current_snapshot: AssetSnapshot
    lineage_graph: Dict[str, dict]      # asset_urn -> dict representation of snapshot
    precedent: Optional[dict]           # best-matching prior experience, if any
    diff_found: bool
    diff_details: dict
    recurrence_flag: bool
    recurrence_reason: str

    culprit_urn: str                    # root cause asset URN discovered via graph traversal
    root_cause: str
    fix_proposed: str
    reused_fix: bool
    nodes_visited: int

    fix_applied: bool
    outcome: str
    hitl_approve_fn: object             # optional callable(incident, fix) -> bool

    experience: Experience
    should_promote: bool
    promote_reason: str


# --- Graph Traversal Helper ---------------------------------------------

def traverse_lineage_graph(start_urn: str, datahub: DataHubClient, max_depth: int = 2) -> Dict[str, AssetSnapshot]:
    """
    Breadth-First Search (BFS) traversal of upstream and downstream lineage
    assets up to max_depth. Collects snapshots across the entire graph.
    """
    visited: Dict[str, AssetSnapshot] = {}
    queue = [(start_urn, 0)]

    while queue:
        current_urn, depth = queue.pop(0)
        if current_urn in visited:
            continue

        snapshot = datahub.get_asset_snapshot(current_urn)
        visited[current_urn] = snapshot

        if depth < max_depth:
            # Graph walk both upstream (producers) and downstream (consumers)
            neighbors = snapshot.upstream_urns + snapshot.downstream_urns
            for neighbor_urn in neighbors:
                if neighbor_urn not in visited:
                    queue.append((neighbor_urn, depth + 1))

    return visited


# --- Nodes --------------------------------------------------------------

def detect(state: CortexState) -> CortexState:
    incident = state["incident"]
    log.info(f"[DETECT] Incident={incident.id} | Type={incident.incident_type} | Asset={incident.trigger_asset_urn}")
    procedure = load_procedure(incident.incident_type)
    return {"procedure": procedure}


def retrieve(state: CortexState) -> CortexState:
    incident = state["incident"]
    episodic = EpisodicMemory()

    query_text = f"{incident.incident_type} on {incident.trigger_asset_urn}: {incident.description}"
    matches = episodic.query(query_text, top_k=1)

    if matches and matches[0]["score"] >= config.EPISODIC_MATCH_THRESHOLD:
        precedent = matches[0]["experience"]
        log.info(f"[RETRIEVE] Precedent found: {precedent['id']} (score={matches[0]['score']:.3f})")
        return {"precedent": precedent}

    log.info("[RETRIEVE] No precedent found above threshold — cold path")
    return {"precedent": None}


def verify_and_diff(state: CortexState) -> CortexState:
    """Pull current trigger state and compare against precedent snapshot."""
    incident = state["incident"]
    precedent = state["precedent"]
    datahub = DataHubClient()

    current_snapshot = datahub.get_asset_snapshot(incident.trigger_asset_urn)

    old_snapshot_dict = precedent.get("snapshot") if precedent else None
    old_snapshot = AssetSnapshot(**old_snapshot_dict) if old_snapshot_dict else AssetSnapshot()

    diff = compute_diff(old_snapshot, current_snapshot)

    same_asset = precedent["trigger_asset_urn"] == incident.trigger_asset_urn if precedent else False
    recurrence_flag, recurrence_reason = check_recurrence_despite_no_diff(precedent, diff.any_diff, same_asset)
    
    if recurrence_flag:
        log.warning(f"[VERIFY_DIFF] Recurrence Contradiction: {recurrence_reason}")

    return {
        "current_snapshot": current_snapshot,
        "diff_found": diff.any_diff,
        "diff_details": diff.details,
        "recurrence_flag": recurrence_flag,
        "recurrence_reason": recurrence_reason,
    }


def reuse_fix(state: CortexState) -> CortexState:
    """Warm path: no diff, no contradiction — reuse the precedent's fix instantly."""
    precedent = state["precedent"]
    log.info(f"[REUSE_FIX] Reusing fix from {precedent['id']} — zero diff detected")
    return {
        "root_cause": precedent["root_cause"],
        "fix_proposed": precedent["fix_proposed"],
        "reused_fix": True,
        "nodes_visited": 1,
    }

def investigate(state: CortexState) -> CortexState:
    """
    Performs multi-hop graph traversal (upstream and downstream), gathers evidence
    across all connected nodes, computes lineage diffs/anomalies, isolates the culprit,
    and diagnoses root cause.
    """
    incident = state["incident"]
    datahub = DataHubClient()
    seed = state.get("diff_details")

    log.info(f"[INVESTIGATE] Initiating multi-hop graph walk from {incident.trigger_asset_urn}")
    
    # 1. Traversal: Walk upstream and downstream lineage graph (max_depth=2)
    lineage_snapshots = traverse_lineage_graph(incident.trigger_asset_urn, datahub, max_depth=2)
    nodes_visited = len(lineage_snapshots)
    log.info(f"[INVESTIGATE] Lineage traversal complete — visited {nodes_visited} graph nodes")

    # 2. Bundle evidence for diagnosis
    current_snapshot = lineage_snapshots.get(incident.trigger_asset_urn) or datahub.get_asset_snapshot(incident.trigger_asset_urn)
    
    # Safely convert snapshots to dicts (supporting .to_dict() or dataclasses.asdict)
    target_dict = current_snapshot.to_dict() if hasattr(current_snapshot, "to_dict") else dataclasses.asdict(current_snapshot)
    lineage_dict = {
        urn: (snap.to_dict() if hasattr(snap, "to_dict") else dataclasses.asdict(snap))
        for urn, snap in lineage_snapshots.items()
    }

    # 3. Compute Lineage Diff & Freshness Anomalies across graph nodes
    lineage_evidence = compute_lineage_diff(target_dict, lineage_dict)

    # Combine initial seed diff with computed graph evidence
    combined_diff = {
        "seed_diff": seed,
        "lineage_mismatches": lineage_evidence.get("lineage_schema_mismatches", []),
        "freshness_status": lineage_evidence.get("freshness", {}),
    }

    # 4. LLM Diagnosis grounded in computed graph evidence
    root_cause = diagnose_root_cause(
        description=incident.description,
        current_snapshot=target_dict,
        diff_details=combined_diff,
        lineage_graph=lineage_dict,
    )

    # 5. Triage Re-classification based on computed evidence
    updated_procedure = state.get("procedure")
    if incident.incident_type == "unclassified":
        has_schema_mismatch = (
            bool(seed and seed.get("schema")) or 
            bool(lineage_evidence.get("lineage_schema_mismatches"))
        )
        is_freshness_issue = (
            lineage_evidence.get("freshness", {}).get("is_stale") or
            "SLA" in root_cause or 
            "freshness" in root_cause.lower()
        )

        if has_schema_mismatch:
            incident.incident_type = "schema_drift"
        elif is_freshness_issue:
            incident.incident_type = "freshness"
        
        log.info(f"[TRIAGE] Incident auto-classified as: {incident.incident_type}")
        updated_procedure = load_procedure(incident.incident_type)

    log.info(f"[INVESTIGATE] Root cause identified: {root_cause}")

    # 6. Return state with lineage_evidence included for the store/promote nodes
    return {
        "incident": incident,
        "procedure": updated_procedure,
        "current_snapshot": current_snapshot,
        "lineage_graph": lineage_dict,
        "lineage_evidence": lineage_evidence,  # <--- Essential for store() & video demo!
        "root_cause": root_cause,
        "nodes_visited": nodes_visited,
        "reused_fix": False,
    }
def propose_fix(state: CortexState) -> CortexState:
    if state.get("reused_fix"):
        return {}
    fix = generate_fix(state["root_cause"], state.get("diff_details", {}))
    return {"fix_proposed": fix}


def human_review(state: CortexState) -> CortexState:
    incident = state["incident"]
    fix = state["fix_proposed"]
    approve_fn = state.get("hitl_approve_fn")

    if approve_fn is None:
        log.warning("[HUMAN_REVIEW] No hitl_approve_fn provided — defaulting to auto-approve (mock mode).")
        approved = True
    else:
        approved = approve_fn(incident, fix)

    outcome = "success" if approved else "rejected"
    log.info(f"[HUMAN_REVIEW] Human Decision: approved={approved} -> outcome={outcome}")
    return {"fix_applied": approved, "outcome": outcome}


def store(state: CortexState) -> CortexState:
    incident = state["incident"]
    root_cause = state["root_cause"]
    fix_proposed = state["fix_proposed"]
    lineage_evidence = state.get("lineage_evidence", {})

    experience = Experience(
        id=f"exp_{uuid.uuid4().hex[:8]}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        trigger_asset_urn=incident.trigger_asset_urn,
        incident_type=state.get("incident_type", "unclassified"),
        root_cause=root_cause,
        fix_proposed=fix_proposed,
        # Store full evidence context for episodic memory retrieval
        evidence_context={
            "schema_mismatches": lineage_evidence.get("lineage_schema_mismatches", []),
            "freshness": lineage_evidence.get("freshness", {}),
            "target_schema_sample": state["current_snapshot"].get(
                "schema_fields", []
            )[:5],
            "last_run_status": state["current_snapshot"].get("last_run_status"),
        },)

    EpisodicMemory().add(experience)
    state["experience"] = experience
    return state

def reflect_node(state: CortexState) -> CortexState:
    should_promote, reason = reflect(state["experience"])
    log.info(f"[REFLECT] Gatekeeper Result: should_promote={should_promote} — Reason: {reason}")
    return {"should_promote": should_promote, "promote_reason": reason}


# In cortex/graph.py -> promote() node

def promote(state: CortexState) -> CortexState:
    experience = state["experience"]
    datahub = DataHubClient()

    # 1. Read existing lesson aspect from DataHub (if present)
    existing_lesson = datahub.get_promoted_lessons(experience.trigger_asset_urn) or {}
    prev_count = existing_lesson.get("observed_count", 0)
    prev_ids = existing_lesson.get("source_experience_ids", [])

    # 2. Dynamically increment observation counter
    new_count = prev_count + 1

    lesson = {
        "lesson": experience.root_cause,
        "fix": experience.fix_proposed,
        "observed_count": new_count,  # <--- Dynamic increment!
        "success_rate": "100%",
        "last_validated": experience.timestamp,
        "source_experience_ids": list(set(prev_ids + [experience.id])),
    }

    datahub.write_lesson(experience.trigger_asset_urn, lesson)
    log.info(f"[PROMOTE] Promoted lesson to DataHub (Observed: {new_count}x) for asset: {experience.trigger_asset_urn}")
    return {}


def skip_promote(state: CortexState) -> CortexState:
    log.info(f"[SKIP_PROMOTE] Skipped DataHub metadata promotion: {state['promote_reason']}")
    return {}


# --- Routing Logic ------------------------------------------------------

def route_after_retrieve(state: CortexState) -> str:
    return "verify_and_diff" if state.get("precedent") else "investigate"


def route_after_diff(state: CortexState) -> str:
    precedent = state["precedent"]
    if state.get("recurrence_flag"):
        return "investigate"  # Recurrence contradiction -> force re-investigation
    if state.get("diff_found"):
        return "investigate"  # Structural change detected -> seeded investigation
    if precedent and precedent.get("outcome") != "success":
        log.info("[ROUTE] Precedent was never confirmed successful — investigating fresh")
        return "investigate"
    return "reuse_fix"         # Clean warm match -> instant fix reuse


def route_after_reflect(state: CortexState) -> str:
    return "promote" if state.get("should_promote") else "skip_promote"


# --- Graph Construction -------------------------------------------------

def build_graph():
    g = StateGraph(CortexState)

    # Register Nodes
    g.add_node("detect", detect)
    g.add_node("retrieve", retrieve)
    g.add_node("verify_and_diff", verify_and_diff)
    g.add_node("reuse_fix", reuse_fix)
    g.add_node("investigate", investigate)
    g.add_node("propose_fix", propose_fix)
    g.add_node("human_review", human_review)
    g.add_node("store", store)
    g.add_node("reflect", reflect_node)
    g.add_node("promote", promote)
    g.add_node("skip_promote", skip_promote)

    # Correct Entry Point
    g.set_entry_point("detect")

    # Connect Edges
    g.add_edge("detect", "retrieve")
    
    g.add_conditional_edges("retrieve", route_after_retrieve, {
        "verify_and_diff": "verify_and_diff",
        "investigate": "investigate",
    })
    
    g.add_conditional_edges("verify_and_diff", route_after_diff, {
        "investigate": "investigate",
        "reuse_fix": "reuse_fix",
    })
    
    g.add_edge("reuse_fix", "human_review")
    g.add_edge("investigate", "propose_fix")
    g.add_edge("propose_fix", "human_review")
    g.add_edge("human_review", "store")
    g.add_edge("store", "reflect")
    
    g.add_conditional_edges("reflect", route_after_reflect, {
        "promote": "promote",
        "skip_promote": "skip_promote",
    })
    
    g.add_edge("promote", END)
    g.add_edge("skip_promote", END)

    return g.compile()


def run_incident(incident: Incident, hitl_approve_fn=None) -> CortexState:
    graph = build_graph()
    log.info(f"{'='*60}\nRunning Cortex Engine for Incident: {incident.id}\n{'='*60}")
    initial_state = {"incident": incident}
    if hitl_approve_fn is not None:
        initial_state["hitl_approve_fn"] = hitl_approve_fn
    final_state = graph.invoke(initial_state)
    log.info(f"{'='*60}\nRun Complete. Nodes Visited: {final_state.get('nodes_visited')}\n{'='*60}")
    return final_state