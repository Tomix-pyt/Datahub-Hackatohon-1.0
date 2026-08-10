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
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TypedDict
import uuid
from langgraph.graph import END, StateGraph
from cortex import config
from cortex.diff import compute_diff,compute_lineage_diff, determine_incident_type
from cortex.llm import diagnose_root_cause, generate_fix
from cortex.memory_episodic import EpisodicMemory
from cortex.memory_semantic import DataHubClient
from cortex.models import AssetSnapshot, Experience, Incident
from cortex.procedure import load_procedure
from cortex.reflection import check_recurrence_despite_no_diff

log = config.get_logger("cortex.graph")

# --- State Definition ---------------------------------------------------

class CortexState(TypedDict, total=False):
    # Incident & Procedure Context
    incident: Incident
    procedure: dict

    # Snapshots & Lineage Traversal
    current_snapshot: AssetSnapshot
    lineage_graph: Dict[str, dict]      # asset_urn -> dict representation of snapshot
    lineage_evidence: dict             # NEW: Output from compute_lineage_diff() (mismatches & freshness)

    # Episodic Precedent Matching
    precedent: Optional[Experience]          # Updated: Strongly-typed Experience object
    matched_precedent: Optional[Experience]  # Alias used in retrieve node

    # Diffing & Verification
    diff_found: bool
    diff_details: dict
    recurrence_flag: bool
    recurrence_reason: str

    # Investigation & Fix Generation
    culprit_urn: str                    # Root cause asset URN discovered via graph traversal
    root_cause: str
    fix_proposed: str
    reused_fix: bool
    nodes_visited: int

    # Human-in-the-Loop & Execution
    fix_applied: bool
    outcome: str
    hitl_approve_fn: object             # Optional callable(incident, fix) -> bool

    # Episodic & Semantic Memory Promotion
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

def reflect(state: CortexState) -> CortexState:
    """Evaluates whether the resolved experience should be promoted to DataHub semantic memory."""
    experience = state["experience"]
    precedent = state.get("matched_precedent") or state.get("precedent")
    fix_applied = state.get("fix_applied", False)

    # Guardrail 1: Require verified human approval / application
    if not fix_applied:
        state["should_promote"] = False
        state["promote_reason"] = "fix was not applied — nothing proven yet"
        log.info(f"[REFLECT] Skipped — {state['promote_reason']}")
        return state

    # Guardrail 2: Only suppress promotion if the EXACT SAME URN already has this exact precedent documented
    if precedent:
        is_same_urn = (
            experience.trigger_asset_urn == precedent.trigger_asset_urn
        )
        similarity = getattr(precedent, "similarity_score", 0.0)

        if is_same_urn and similarity >= 0.88:
            state["should_promote"] = False
            state["promote_reason"] = (
                f"Asset URN '{experience.trigger_asset_urn}' already has active "
                f"documentation from precedent {precedent.id} (score: {similarity:.2f})."
            )
            log.info(f"[REFLECT] Skipped — {state['promote_reason']}")
            return state

    # Passed Gatekeeper — Enable Promotion for DataHub
    state["should_promote"] = True
    state["promote_reason"] = (
        f"Novel verified lesson ready for DataHub semantic memory on asset URN: {experience.trigger_asset_urn}"
    )
    log.info(f"[REFLECT] Gatekeeper Approved — {state['promote_reason']}")
    return state

def build_retrieval_query_fingerprint(
    incident, current_snapshot, seed_diff: Optional[dict] = None
) -> str:
    """Builds a matching query fingerprint at moment-zero using known structural context."""
    schema_fields = (
        current_snapshot.schema_fields
        if hasattr(current_snapshot, "schema_fields")
        else current_snapshot.get("schema_fields", [])
    )
    schema_sample = schema_fields[:10]

    freshness_age = (
        current_snapshot.freshness_age_hours
        if hasattr(current_snapshot, "freshness_age_hours")
        else current_snapshot.get("freshness_age_hours")
    )

    return (
        f"Asset URN: {incident.trigger_asset_urn}\n"
        f"Incident Type: {incident.incident_type}\n"
        f"Symptom Description: {incident.description}\n"
        f"Schema Signature: {schema_sample}\n"
        f"Staleness Age (Hours): {freshness_age}\n"
        f"Initial Diff Seed: {seed_diff or 'None'}"
    )


def retrieve(state: CortexState) -> CortexState:
    incident = state["incident"]
    datahub = DataHubClient()

    current_snapshot = datahub.get_asset_snapshot(incident.trigger_asset_urn)
    state["current_snapshot"] = current_snapshot

    query_fingerprint = build_retrieval_query_fingerprint(
        incident=incident,
        current_snapshot=current_snapshot,
        seed_diff=state.get("diff_details"),
    )

    # Search can return a list of Experience matches
    raw_precedents = EpisodicMemory().search(query_fingerprint, threshold=0.70)

    # 1. Normalize response to handle both list and single object returns safely
    if isinstance(raw_precedents, list):
        precedents_list = raw_precedents
        top_precedent = precedents_list[0] if precedents_list else None
    else:
        top_precedent = raw_precedents
        precedents_list = [raw_precedents] if raw_precedents else []

    # Store full list of retrieved matches in state for multi-precedent context
    state["retrieved_experiences"] = precedents_list

    # 2. Evaluate the top matching precedent
    if top_precedent:
        is_same_asset = (top_precedent.trigger_asset_urn == incident.trigger_asset_urn)
        similarity = getattr(top_precedent, "similarity_score", 0.0)

        if is_same_asset:
            log.info(f"[RETRIEVE] Exact Asset Precedent Match ({top_precedent.id}, score: {similarity:.2f})")
            state["matched_precedent"] = top_precedent
        elif similarity >= 0.88:
            # High confidence cross-asset pattern reuse
            log.info(f"[RETRIEVE] High-confidence Cross-Asset Pattern Match from {top_precedent.trigger_asset_urn}")
            state["matched_precedent"] = top_precedent
        else:
            # Low/mid confidence from a DIFFERENT asset -> Force full investigation
            log.info(f"[RETRIEVE] Cross-asset match score {similarity:.2f} below reuse bar — forcing deep investigation.")
            state["matched_precedent"] = None
    else:
        state["matched_precedent"] = None

    return state
def verify_and_diff(state: CortexState) -> CortexState:
    """Pull current trigger state and compare against precedent snapshot."""
    incident = state["incident"]
    precedent = state["precedent"]
    datahub = DataHubClient()

    current_snapshot = datahub.get_asset_snapshot(incident.trigger_asset_urn)

    old_snapshot_dict = precedent.get("snapshot") if precedent else None
    old_snapshot = AssetSnapshot(**old_snapshot_dict) if old_snapshot_dict else AssetSnapshot()

    diff = compute_diff(old_snapshot, current_snapshot)

    same_asset = precedent["trigger_asset_urn"] == incident.trigger_asset_urn
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
    incident = state["incident"]
    datahub = DataHubClient()
    seed = state.get("diff_details")

    log.info(f"[INVESTIGATE] Multi-hop graph walk from {incident.trigger_asset_urn}")

    # 1. Traverse lineage (returns dict[urn, AssetSnapshot])
    lineage_snapshots = traverse_lineage_graph(incident.trigger_asset_urn, datahub, max_depth=2)
    nodes_visited = len(lineage_snapshots)

    # 2. Get current snapshot (target) as dict
    current_snapshot = lineage_snapshots.get(incident.trigger_asset_urn) or datahub.get_asset_snapshot(incident.trigger_asset_urn)
    target_dict = current_snapshot.to_dict() if hasattr(current_snapshot, "to_dict") else dataclasses.asdict(current_snapshot)

    # 3. Convert ALL snapshots to dicts for diffing and diagnosis
    lineage_dict = {
        urn: (snap.to_dict() if hasattr(snap, "to_dict") else dataclasses.asdict(snap))
        for urn, snap in lineage_snapshots.items()
    }

    # 4. Compute lineage diff using dictionaries
    lineage_evidence = compute_lineage_diff(target_dict, lineage_dict)

    # 5. Classify incident type
    incident_type = determine_incident_type(seed, lineage_evidence)
    incident.incident_type = incident_type
    log.info(f"[TRIAGE] Classified as: {incident_type}")

    # 6. Load procedure
    updated_procedure = load_procedure(incident_type)

    # 7. LLM diagnosis (pass lineage_dict)
    root_cause = diagnose_root_cause(
        description=incident.description,
        current_snapshot=target_dict,
        diff_details=lineage_evidence,
        lineage_graph=lineage_dict,  # now a dict of dicts
        incident_type=incident_type,
    )

    return {
        "incident": incident,
        "procedure": updated_procedure,
        "current_snapshot": current_snapshot,          # keep original object if needed
        "lineage_graph": lineage_dict,                 # store dict version for later nodes
        "lineage_evidence": lineage_evidence,
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
    """Stores the experience into ChromaDB episodic memory."""
    incident = state["incident"]
    root_cause = state["root_cause"]
    fix_proposed = state["fix_proposed"]
    lineage_evidence = state.get("lineage_evidence", {})
    fix_applied = state.get("fix_applied", False)  # <--- Read fix_applied state

    current_snapshot = state.get("current_snapshot")
    if hasattr(current_snapshot, "schema_fields"):
        schema_fields = current_snapshot.schema_fields or []
    elif isinstance(current_snapshot, dict):
        schema_fields = current_snapshot.get("schema_fields", [])
    else:
        schema_fields = []

    current_utc_timestamp = datetime.now(timezone.utc).isoformat()

    experience = Experience(
        id=f"exp_{uuid.uuid4().hex[:8]}",
        incident_id=incident.id,
        timestamp=current_utc_timestamp,
        trigger_asset_urn=incident.trigger_asset_urn,
        incident_type=state.get("incident_type", "unclassified"),
        root_cause=root_cause,
        fix_proposed=fix_proposed,
        fix_applied=fix_applied,  # <--- Pass fix_applied here!
        outcome=state.get("outcome", "success" if fix_applied else "rejected"),
        nodes_visited=state.get("nodes_visited", 0),
        snapshot=current_snapshot
        if hasattr(current_snapshot, "to_dict")
        else None,
        evidence_context={
            "schema_mismatches": lineage_evidence.get(
                "lineage_schema_mismatches", []
            ),
            "freshness": lineage_evidence.get("freshness", {}),
            "target_schema_sample": schema_fields[:10],
        },
    )

    EpisodicMemory().save(experience)
    state["experience"] = experience
    log.info(f"[STORE] Experience {experience.id} saved to Episodic Memory")
    return state


def reflect_node(state: CortexState) -> CortexState:
    final = reflect(state) 
    log.info(f"[REFLECT] Gatekeeper Result: should_promote={final.get('should_promote')} — Reason: {final.get('promote_reason') }")
    return final
    

def promote(state: CortexState) -> CortexState:
    """Promotes experience to DataHub semantic memory for the affected target URN."""
    experience = state["experience"]
    incident =  state["incident"]
    precedent = state.get("matched_precedent") or state.get("precedent")
    fix_applied = state.get("fix_applied", False)

    if not fix_applied:
        state["should_promote"] = False
        state["promote_reason"] = "Fix was not approved/applied."
        log.info(f"[PROMOTE] Skipped — {state['promote_reason']}")
        return state

    # CHECK: Has THIS SPECIFIC URN already been documented for this exact incident?
    is_same_urn = False
    if precedent:
        is_same_urn = (experience.trigger_asset_urn == precedent.trigger_asset_urn)

    # RULE: Suppress promotion ONLY if it's a direct duplicate on the EXACT SAME URN
    if is_same_urn and getattr(precedent, "similarity_score", 0.0) >= 0.80:
        state["should_promote"] = False
        state["promote_reason"] = f"URN '{experience.trigger_asset_urn}' already has active documentation from {precedent.id}."
        log.info(f"[PROMOTE] Skipped — {state['promote_reason']}")
        return state

    # ALWAYS Promote for New Asset URNs or Novel Incidents
    state["should_promote"] = True
    state["promote_reason"] = (
        f"Promoting lesson to DataHub for URN: {experience.trigger_asset_urn} "
        + ("(First incident recorded on this asset)" if not is_same_urn else "(Novel pattern)")
    )

    try:

        lesson_payload = {
            "root_cause": getattr(incident, "incident_type", "unknown"),
            "proposed_fix": getattr(experience, "fix_proposed", state.get("proposed_fix", "")),
            "observed_count": getattr(experience, "nodes_visited", 1),  # or tracking count
            "success_rate": "100%",
            "source_experience_ids": [experience.id],}

        datahub = DataHubClient()
        datahub.write_lesson(
            asset_urn=experience.trigger_asset_urn,
            lesson=lesson_payload,
            fix=lesson_payload["proposed_fix"],
        )
        log.info(f"[PROMOTE] Successfully wrote lesson to DataHub URN: {experience.trigger_asset_urn}")
    except Exception as e:
        log.error(f"[PROMOTE] DataHub writeback failed: {e}")

    return state

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