"""
Cortex's LangGraph. This is the one file that encodes the actual
control flow we designed:

  detect -> retrieve -> [precedent found?]
                            |-- yes -> diff vs stored snapshot
                            |            |-- no diff & prior success -> flag + full investigate (recurrence)
                            |            |-- no diff, no contradiction -> reuse fix instantly
                            |            `-- diff found -> seed investigate with what changed
                            `-- no  -> full investigate (cold start)
         -> propose_fix -> store (always) -> reflect -> [promote if it earns it]

Every node logs what it did. Run this with CORTEX_DEBUG=true (default)
and you'll see the whole decision trail in the console — that's the
point, this should be debuggable by reading logs, not by guessing.
"""
import io
from PIL import Image
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from Cortex import config
from Cortex.diff import compute_diff
from Cortex.llm import generate_fix
from Cortex.memory_episodic import EpisodicMemory
from Cortex.memory_semantic import DataHubClient
from Cortex.models import AssetSnapshot, Experience, Incident
from Cortex.procedure import load_procedure
from Cortex.reflection import check_recurrence_despite_no_diff, reflect

log = config.get_logger("Cortex.graph")
from IPython.display import display


class CortexState(TypedDict, total=False):
    incident: Incident
    procedure: dict

    current_snapshot: AssetSnapshot
    precedent: Optional[dict]           # best-matching prior experience, if any
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
    hitl_approve_fn: object   # optional callable(incident, fix) -> bool; not persisted, injected at invoke time

    experience: Experience
    should_promote: bool
    promote_reason: str


# --- Nodes --------------------------------------------------------------

def detect(state: CortexState) -> CortexState:
    incident = state["incident"]
    log.info(f"[DETECT] incident={incident.id} type={incident.incident_type} asset={incident.trigger_asset_urn}")
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
    """Only runs if a precedent was found. Pulls current DataHub state
    and diffs it against the snapshot stored with the precedent."""
    incident = state["incident"]
    precedent = state["precedent"]
    datahub = DataHubClient()

    current_snapshot = datahub.get_asset_snapshot(incident.trigger_asset_urn)

    old_snapshot_dict = precedent["snapshot"]
    old_snapshot = AssetSnapshot(**old_snapshot_dict) if old_snapshot_dict else AssetSnapshot()

    diff = compute_diff(old_snapshot, current_snapshot)

    same_asset = precedent["trigger_asset_urn"] == incident.trigger_asset_urn
    recurrence_flag, recurrence_reason = check_recurrence_despite_no_diff(precedent, diff.any_diff, same_asset)
    if recurrence_flag:
        log.warning(f"[VERIFY_DIFF] {recurrence_reason}")

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
    log.info(f"[REUSE_FIX] Reusing fix from {precedent['id']} — no diff since last time")
    return {
        "root_cause": precedent["root_cause"],
        "fix_proposed": precedent["fix_proposed"],
        "reused_fix": True,
        "nodes_visited": 1,  # just the asset itself — the whole point of reuse
    }


def investigate(state: CortexState) -> CortexState:
    """
    Cold path, or warm-but-changed path. If we have diff_details, use
    them to seed the search instead of starting blank — that's the
    'shortest path to the answer' behavior.
    """
    incident = state["incident"]
    datahub = DataHubClient()
    seed = state.get("diff_details")

    if seed:
        log.info(f"[INVESTIGATE] Seeded with known changes: {seed}")
    else:
        log.info("[INVESTIGATE] Cold start — no precedent or diff to seed with")

    current_snapshot = state.get("current_snapshot") or datahub.get_asset_snapshot(incident.trigger_asset_urn)
    nodes_visited = 1 + len(current_snapshot.upstream_urns) + len(current_snapshot.downstream_urns)

    # In mock mode, root cause is inferred directly from the fixture data.
    # A real implementation would have the LLM reason over `seed` +
    # `current_snapshot` + lineage to produce this.
    if seed and "schema" in seed:
        root_cause = f"Schema drift: {seed['schema']}"
    else:
        root_cause = "Schema drift: customer_id renamed to customer_uuid in raw_sales"

    log.info(f"[INVESTIGATE] Root cause: {root_cause} ({nodes_visited} nodes visited)")

    return {
        "root_cause": root_cause,
        "current_snapshot": current_snapshot,
        "nodes_visited": nodes_visited,
        "reused_fix": False,
    }


def propose_fix(state: CortexState) -> CortexState:
    if state.get("reused_fix"):
        return {}  # fix already set by reuse_fix
    fix = generate_fix(state["root_cause"], state.get("diff_details", {}))
    return {"fix_proposed": fix}


def human_review(state: CortexState) -> CortexState:
    """
    Real HITL gate. outcome/fix_applied only become 'success'/True after
    an actual approval — this is what makes the recurrence-contradiction
    check (see reflection.py) meaningful instead of vacuously always-true.
    """
    incident = state["incident"]
    fix = state["fix_proposed"]
    approve_fn = state.get("hitl_approve_fn")

    if approve_fn is None:
        log.warning(
            "[HUMAN_REVIEW] No hitl_approve_fn provided — auto-approving. "
            "This is a test/mock default, not a real review; pass a real "
            "callback (or your UI's confirm handler) in production."
        )
        approved = True
    else:
        approved = approve_fn(incident, fix)

    outcome = "success" if approved else "failed"
    log.info(f"[HUMAN_REVIEW] approved={approved} -> outcome={outcome}")
    return {"fix_applied": approved, "outcome": outcome}


def store(state: CortexState) -> CortexState:
    """Always runs. Every investigation gets logged to episodic memory,
    success or failure, reused or fresh."""
    incident = state["incident"]
    current_snapshot = state.get("current_snapshot") or AssetSnapshot(asset_urn=incident.trigger_asset_urn)

    experience = Experience(
        incident_id=incident.id,
        incident_type=incident.incident_type,
        trigger_asset_urn=incident.trigger_asset_urn,
        procedure_used=state["procedure"]["name"],
        snapshot=current_snapshot,
        root_cause=state["root_cause"],
        fix_proposed=state["fix_proposed"],
        fix_applied=state.get("fix_applied", False),
        outcome=state.get("outcome", "pending_review"),
        nodes_visited=state.get("nodes_visited", 0),
        matched_prior_experience_id=state["precedent"]["id"] if state.get("precedent") else None,
        novel=state.get("precedent") is None,
        # IMPORTANT: this must be built from the SAME fields retrieve()'s
        # query_text uses (incident_type + asset + description), not
        # root_cause — otherwise stored experiences and future queries
        # never share vocabulary and retrieval silently never matches.
        embedding_text=f"{incident.incident_type} on {incident.trigger_asset_urn}: {incident.description}",
    )

    episodic = EpisodicMemory()
    episodic.add(experience)
    log.info(f"[STORE] Experience {experience.id} logged to episodic memory (novel={experience.novel})")

    return {"experience": experience}


def reflect_node(state: CortexState) -> CortexState:
    should_promote, reason = reflect(state["experience"])
    log.info(f"[REFLECT] should_promote={should_promote} — {reason}")
    return {"should_promote": should_promote, "promote_reason": reason}


def promote(state: CortexState) -> CortexState:
    experience = state["experience"]
    datahub = DataHubClient()
    lesson = {
        "lesson": experience.root_cause,
        "fix": experience.fix_proposed,
        "observed_count": 1,
        "success_rate": "100%",
        "last_validated": experience.timestamp,
        "source_experience_ids": [experience.id],
    }
    datahub.write_lesson(experience.trigger_asset_urn, lesson)
    return {}


def skip_promote(state: CortexState) -> CortexState:
    log.info(f"[SKIP_PROMOTE] {state['promote_reason']}")
    return {}


# --- Routing --------------------------------------------------------------

def route_after_retrieve(state: CortexState) -> str:
    return "verify_and_diff" if state.get("precedent") else "investigate"


def route_after_diff(state: CortexState) -> str:
    precedent = state["precedent"]
    if state.get("recurrence_flag"):
        return "investigate"       # contradiction detected -> don't trust the memory, investigate fully
    if state.get("diff_found"):
        return "investigate"       # seeded investigation
    if precedent.get("outcome") != "success":
        # no diff, but the precedent itself was never a confirmed success
        # (rejected or still pending) — nothing proven to reuse
        log.info("[ROUTE] Precedent exists but was never confirmed successful — investigating fresh")
        return "investigate"
    return "reuse_fix"             # clean warm path: no diff, prior fix was confirmed working


def route_after_reflect(state: CortexState) -> str:
    return "promote" if state.get("should_promote") else "skip_promote"


# --- Build the graph --------------------------------------------------------

def build_graph():
    g = StateGraph(CortexState)

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

    g.set_entry_point("detect")
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
    graph_img = graph.get_graph().draw_mermaid_png()
    image = Image.open(io.BytesIO(graph_img))
    image.show()
    log.info(f"{'='*60}\nRunning Cortex for incident {incident.id}\n{'='*60}")
    initial_state = {"incident": incident}
    if hitl_approve_fn is not None:
        initial_state["hitl_approve_fn"] = hitl_approve_fn
    final_state = graph.invoke(initial_state)
    log.info(f"{'='*60}\nDone. nodes_visited={final_state.get('nodes_visited')}\n{'='*60}")
    return final_state

