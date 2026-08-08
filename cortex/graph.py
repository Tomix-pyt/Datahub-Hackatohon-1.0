"""Cortex LangGraph cognitive engine.

The important routing rule is:

* same asset + successful precedent -> verify current state against the prior
  snapshot/traversal before trusting memory;
* different asset + high-confidence successful precedent -> reuse the learned
  fix pattern without pretending that two unrelated assets have identical
  snapshots;
* same asset + no detected change after a successful fix -> contradiction,
  therefore investigate again, carrying the previous traversal as context.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from cortex import config
from cortex.diff import compute_diff, compute_graph_state_diff, compute_lineage_diff
from cortex.llm import diagnose_root_cause, generate_fix
from cortex.memory_episodic import get_client as get_episodic
from cortex.memory_semantic import DataHubClient
from cortex.models import AssetSnapshot, Experience, Incident
from cortex.procedure import load_procedure
from cortex.reflection import check_recurrence_despite_no_diff, reflect

log = config.get_logger("cortex.graph")


class CortexState(TypedDict, total=False):
    incident: Incident
    procedure: dict
    current_snapshot: AssetSnapshot
    lineage_graph: dict[str, dict]
    lineage_evidence: dict
    precedent: Optional[Experience]
    matched_precedent: Optional[Experience]
    diff_found: bool
    diff_details: dict
    recurrence_flag: bool
    recurrence_reason: str
    root_cause: str
    fix_proposed: str
    reused_fix: bool
    nodes_visited: int
    fix_applied: bool
    outcome: str
    hitl_approve_fn: object
    experience: Experience
    should_promote: bool
    promote_reason: str


def _snapshot_dict(snapshot: AssetSnapshot) -> dict:
    return dataclasses.asdict(snapshot)


def traverse_lineage_graph(start_urn: str, datahub: DataHubClient, max_depth: int | None = None) -> dict[str, AssetSnapshot]:
    max_depth = config.LINEAGE_MAX_DEPTH if max_depth is None else max_depth
    visited: dict[str, AssetSnapshot] = {}
    queue: list[tuple[str, int]] = [(start_urn, 0)]
    while queue:
        current_urn, depth = queue.pop(0)
        if current_urn in visited:
            continue
        snapshot = datahub.get_asset_snapshot(current_urn)
        visited[current_urn] = snapshot
        if depth < max_depth:
            for neighbor in snapshot.upstream_urns + snapshot.downstream_urns:
                if neighbor not in visited:
                    queue.append((neighbor, depth + 1))
    return visited


def detect(state: CortexState) -> CortexState:
    incident = state["incident"]
    procedure = load_procedure(incident.incident_type)
    log.info("[DETECT] incident=%s type=%s asset=%s", incident.id, incident.incident_type, incident.trigger_asset_urn)
    return {"procedure": procedure}


def _retrieval_fingerprint(incident: Incident, snapshot: AssetSnapshot) -> str:
    # Deliberately excludes the literal asset URN. Asset identity is handled by
    # Chroma metadata filtering; embeddings represent the reusable failure pattern.
    return (
        f"Incident Type: {incident.incident_type}\n"
        f"Symptom: {incident.description}\n"
        f"Schema: {snapshot.schema_fields[:15]}\n"
        f"Upstream Schema: {snapshot.upstream_schemas}\n"
        f"Freshness Age: {snapshot.freshness_age_hours}\n"
    )


def retrieve(state: CortexState) -> CortexState:
    incident = state["incident"]
    datahub = DataHubClient()
    snapshot = datahub.get_asset_snapshot(incident.trigger_asset_urn)
    state["current_snapshot"] = snapshot
    query = _retrieval_fingerprint(incident, snapshot)
    episodic = get_episodic()

    # First ask: has THIS asset already had a successful incident like this?
    precedent = episodic.search(query, asset_urn=incident.trigger_asset_urn, n_results=5)
    if precedent:
        log.info("[RETRIEVE] Exact-asset precedent %s score=%.3f", precedent.id, precedent.similarity_score or 0)
        return {"precedent": precedent, "matched_precedent": precedent}

    # Otherwise ask: have we solved the same failure pattern elsewhere?
    precedent = episodic.search(query, threshold=config.CROSS_ASSET_REUSE_THRESHOLD, n_results=5)
    if precedent:
        log.info(
            "[RETRIEVE] Cross-asset precedent %s from %s score=%.3f",
            precedent.id,
            precedent.trigger_asset_urn,
            precedent.similarity_score or 0,
        )
    else:
        log.info("[RETRIEVE] No trusted precedent — cold path")
    return {"precedent": precedent, "matched_precedent": precedent}


def verify_and_diff(state: CortexState) -> CortexState:
    incident = state["incident"]
    precedent = state.get("precedent")
    if precedent is None or precedent.snapshot is None:
        return {"diff_found": True, "diff_details": {"snapshot": "missing prior snapshot"}}

    datahub = DataHubClient()
    current_graph = traverse_lineage_graph(incident.trigger_asset_urn, datahub)
    current_snapshot = current_graph[incident.trigger_asset_urn]
    old_snapshot = precedent.snapshot
    target_diff = compute_diff(old_snapshot, current_snapshot)

    old_graph = (precedent.evidence_context or {}).get("lineage_graph", {})
    graph_diff = compute_graph_state_diff(old_graph, {u: _snapshot_dict(s) for u, s in current_graph.items()})
    combined_details = dict(target_diff.details)
    if graph_diff["changed"]:
        combined_details["lineage_graph"] = graph_diff

    same_asset = precedent.trigger_asset_urn == incident.trigger_asset_urn
    diff_found = target_diff.any_diff or graph_diff["changed"]
    recurrence_flag, recurrence_reason = check_recurrence_despite_no_diff(precedent, diff_found, same_asset)

    return {
        "current_snapshot": current_snapshot,
        "lineage_graph": {u: _snapshot_dict(s) for u, s in current_graph.items()},
        "diff_found": diff_found,
        "diff_details": combined_details,
        "recurrence_flag": recurrence_flag,
        "recurrence_reason": recurrence_reason,
        "nodes_visited": len(current_graph),
    }


def reuse_fix(state: CortexState) -> CortexState:
    precedent = state["precedent"]
    if precedent is None:
        raise RuntimeError("reuse_fix reached without a precedent")
    log.info("[REUSE_FIX] Reusing successful fix from %s", precedent.id)
    return {
        "root_cause": precedent.root_cause,
        "fix_proposed": precedent.fix_proposed,
        "reused_fix": True,
        "nodes_visited": 1,
    }


def investigate(state: CortexState) -> CortexState:
    incident = state["incident"]
    datahub = DataHubClient()
    prior = state.get("precedent")
    seed = state.get("diff_details", {})

    lineage_snapshots = traverse_lineage_graph(incident.trigger_asset_urn, datahub)
    nodes_visited = len(lineage_snapshots)
    current_snapshot = lineage_snapshots[incident.trigger_asset_urn]
    lineage_dict = {u: _snapshot_dict(s) for u, s in lineage_snapshots.items()}
    lineage_evidence = compute_lineage_diff(_snapshot_dict(current_snapshot), lineage_dict)

    prior_context = None
    if prior is not None:
        prior_context = {
            "experience_id": prior.id,
            "root_cause": prior.root_cause,
            "fix_proposed": prior.fix_proposed,
            "nodes_visited": prior.nodes_visited,
            "previous_lineage_graph": (prior.evidence_context or {}).get("lineage_graph", {}),
        }

    combined_diff = {
        "seed_diff": seed,
        "lineage_mismatches": lineage_evidence.get("lineage_schema_mismatches", []),
        "freshness_status": lineage_evidence.get("freshness", {}),
        "prior_experience_context": prior_context,
    }

    root_cause = diagnose_root_cause(
        description=incident.description,
        current_snapshot=_snapshot_dict(current_snapshot),
        diff_details=combined_diff,
        lineage_graph=lineage_dict,
    )

    # Generic incidents classify themselves from grounded evidence.
    if incident.incident_type == "unclassified":
        if lineage_evidence.get("lineage_schema_mismatches") or seed.get("schema"):
            incident.incident_type = "schema_drift"
        elif lineage_evidence.get("freshness", {}).get("is_stale"):
            incident.incident_type = "freshness"
        else:
            text = root_cause.lower()
            if "schema" in text or "column" in text:
                incident.incident_type = "schema_drift"
            elif "fresh" in text or "stale" in text or "sla" in text:
                incident.incident_type = "freshness"
        procedure = load_procedure(incident.incident_type)
    else:
        procedure = state.get("procedure") or load_procedure(incident.incident_type)

    return {
        "incident": incident,
        "procedure": procedure,
        "current_snapshot": current_snapshot,
        "lineage_graph": lineage_dict,
        "lineage_evidence": lineage_evidence,
        "root_cause": root_cause,
        "nodes_visited": nodes_visited,
        "reused_fix": False,
    }


def propose_fix(state: CortexState) -> CortexState:
    if state.get("reused_fix"):
        return {}
    return {"fix_proposed": generate_fix(state["root_cause"], state.get("diff_details", {}))}


def human_review(state: CortexState) -> CortexState:
    incident = state["incident"]
    fix = state["fix_proposed"]
    approve_fn = state.get("hitl_approve_fn")
    approved = True if approve_fn is None else bool(approve_fn(incident, fix))
    outcome = "success" if approved else "rejected"
    log.info("[HUMAN_REVIEW] approved=%s outcome=%s", approved, outcome)
    return {"fix_applied": approved, "outcome": outcome}


def store(state: CortexState) -> CortexState:
    incident = state["incident"]
    current_snapshot = state.get("current_snapshot") or AssetSnapshot(asset_urn=incident.trigger_asset_urn)
    precedent = state.get("precedent")
    lineage_evidence = state.get("lineage_evidence", {})
    lineage_graph = state.get("lineage_graph", {})
    experience = Experience(
        incident_id=incident.id,
        incident_type=incident.incident_type,
        trigger_asset_urn=incident.trigger_asset_urn,
        procedure_used=(state.get("procedure") or {}).get("name", "default"),
        snapshot=current_snapshot,
        root_cause=state.get("root_cause", ""),
        fix_proposed=state.get("fix_proposed", ""),
        fix_applied=state.get("fix_applied", False),
        outcome=state.get("outcome", "pending"),
        nodes_visited=state.get("nodes_visited", 0),
        matched_prior_experience_id=precedent.id if precedent else None,
        similarity_score=precedent.similarity_score if precedent else None,
        novel=precedent is None,
        evidence_context={
            "incident_description": incident.description,
            "schema_mismatches": lineage_evidence.get("lineage_schema_mismatches", []),
            "freshness": lineage_evidence.get("freshness", {}),
            "target_schema_sample": current_snapshot.schema_fields[:10],
            "lineage_graph": lineage_graph,
            "diff_details": state.get("diff_details", {}),
            "recurrence_flag": state.get("recurrence_flag", False),
        },
    )
    get_episodic().save(experience)
    return {"experience": experience}


def reflect_node(state: CortexState) -> CortexState:
    should_promote, reason = reflect(state["experience"])
    return {"should_promote": should_promote, "promote_reason": reason}


def _lesson_id(experience: Experience) -> str:
    import hashlib
    key = f"{experience.trigger_asset_urn}|{experience.incident_type}|{experience.root_cause}".encode()
    return "cortex-lesson-" + hashlib.sha256(key).hexdigest()[:16]


def promote(state: CortexState) -> CortexState:
    experience = state["experience"]
    if not state.get("fix_applied") or state.get("outcome") != "success":
        return {"should_promote": False, "promote_reason": "experience is not a validated success"}

    prior_successes = get_episodic().get_successful_for_pattern(
        experience.trigger_asset_urn,
        experience.root_cause,
    )
    count = len(prior_successes)
    success_rate = "100%"  # this list contains only validated successes
    lesson = {
        "lesson_id": _lesson_id(experience),
        "title": f"{experience.incident_type} — {experience.root_cause[:80]}",
        "lesson": experience.root_cause,
        "fix": experience.fix_proposed,
        "observed_count": count,
        "success_rate": success_rate,
        "last_validated": experience.timestamp,
        "source_experience_ids": [e.id for e in prior_successes],
    }
    try:
        DataHubClient().write_lesson(experience.trigger_asset_urn, lesson)
        experience.promoted = True
        get_episodic().save(experience)
        return {"should_promote": True, "promote_reason": f"promoted/updated lesson after {count} validated occurrence(s)"}
    except Exception as exc:
        # Promotion failure must not erase the already-completed incident.
        log.exception("[PROMOTE] DataHub writeback failed: %s", exc)
        return {"should_promote": False, "promote_reason": f"promotion failed: {exc}"}


def skip_promote(state: CortexState) -> CortexState:
    log.info("[SKIP_PROMOTE] %s", state.get("promote_reason", "not eligible"))
    return {}


def route_after_retrieve(state: CortexState) -> str:
    precedent = state.get("precedent")
    if not precedent:
        return "investigate"
    # Only an exact-asset precedent needs a snapshot diff. A cross-asset
    # precedent is a reusable pattern, not a snapshot to compare literally.
    return "verify_and_diff" if precedent.trigger_asset_urn == state["incident"].trigger_asset_urn else "reuse_fix"


def route_after_diff(state: CortexState) -> str:
    precedent = state.get("precedent")
    if state.get("recurrence_flag") or state.get("diff_found"):
        return "investigate"
    if not precedent or precedent.outcome != "success" or not precedent.fix_applied:
        return "investigate"
    return "reuse_fix"


def route_after_reflect(state: CortexState) -> str:
    return "promote" if state.get("should_promote") else "skip_promote"


def build_graph():
    g = StateGraph(CortexState)
    for name, node in [
        ("detect", detect), ("retrieve", retrieve), ("verify_and_diff", verify_and_diff),
        ("reuse_fix", reuse_fix), ("investigate", investigate), ("propose_fix", propose_fix),
        ("human_review", human_review), ("store", store), ("reflect", reflect_node),
        ("promote", promote), ("skip_promote", skip_promote),
    ]:
        g.add_node(name, node)

    g.set_entry_point("detect")
    g.add_edge("detect", "retrieve")
    g.add_conditional_edges("retrieve", route_after_retrieve, {
        "verify_and_diff": "verify_and_diff", "investigate": "investigate", "reuse_fix": "reuse_fix",
    })
    g.add_conditional_edges("verify_and_diff", route_after_diff, {
        "investigate": "investigate", "reuse_fix": "reuse_fix",
    })
    g.add_edge("reuse_fix", "human_review")
    g.add_edge("investigate", "propose_fix")
    g.add_edge("propose_fix", "human_review")
    g.add_edge("human_review", "store")
    g.add_edge("store", "reflect")
    g.add_conditional_edges("reflect", route_after_reflect, {
        "promote": "promote", "skip_promote": "skip_promote",
    })
    g.add_edge("promote", END)
    g.add_edge("skip_promote", END)
    return g.compile()


def run_incident(incident: Incident, hitl_approve_fn=None) -> CortexState:
    initial_state: CortexState = {"incident": incident}
    if hitl_approve_fn is not None:
        initial_state["hitl_approve_fn"] = hitl_approve_fn
    return build_graph().invoke(initial_state)
