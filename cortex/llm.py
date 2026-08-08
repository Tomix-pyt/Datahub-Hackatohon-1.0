"""LLM boundary.

Mock mode is deterministic and dependency-light; live mode is isolated to
this module so the graph can be tested without a provider key.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from cortex import config

log = config.get_logger("cortex.llm")


def _summarize_lineage_graph(lineage_graph: Optional[dict], target_urn: str) -> str:
    if not lineage_graph:
        return "No connected lineage graph context."
    lines = []
    for urn, snap in lineage_graph.items():
        if urn == target_urn:
            continue
        fields = snap.get("schema_fields") or []
        sample = fields[:3]
        short_urn = urn.split(",")[-1] if "," in urn else urn
        lines.append(
            f"- Asset: {short_urn} | Last Modified: {snap.get('last_modified') or 'N/A'} "
            f"| Fields: {len(fields)} | Sample: {sample}"
        )
    return "\n".join(lines[:8]) or "No connected lineage graph context."


def diagnose_root_cause(
    description: str,
    current_snapshot: dict,
    diff_details: Optional[dict] = None,
    lineage_graph: Optional[dict] = None,
) -> str:
    if config.MOCK_MODE:
        mismatches = (diff_details or {}).get("lineage_mismatches", [])
        freshness = (diff_details or {}).get("freshness_status", {})
        seed = (diff_details or {}).get("seed_diff") or {}
        if mismatches or "schema" in seed:
            return "Schema drift detected: the current asset schema is inconsistent with its upstream lineage."
        if freshness.get("is_stale"):
            return "Freshness breach detected: the affected asset has exceeded the configured freshness SLA."
        if seed:
            return f"The previous fix no longer explains the incident; current structural evidence changed: {seed}"
        return "The incident pattern requires fresh lineage investigation because no prior structural evidence explains it."

    from langchain_groq import ChatGroq
    from langchain_core.messages import HumanMessage, SystemMessage

    target_urn = current_snapshot.get("asset_urn", "")
    target_fields = current_snapshot.get("schema_fields", [])
    compact_graph = _summarize_lineage_graph(lineage_graph, target_urn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    prompt = f"""
You are Cortex, an automated Data Reliability Engineer.
Diagnose the most likely root cause using ONLY the supplied evidence.
Do not invent logs, schema changes, pipeline runs, or relationships.

TIME: {now}
INCIDENT: {description}
TARGET: {target_urn}
LAST MODIFIED: {current_snapshot.get('last_modified')}
AGE HOURS: {current_snapshot.get('freshness_age_hours')}
TARGET SCHEMA ({len(target_fields)} fields): {target_fields[:20]}
COMPUTED EVIDENCE: {diff_details or 'none'}
LINEAGE SUMMARY:
{compact_graph}

Return 2-3 concise sentences naming the most likely root cause and the evidence that supports it.
"""
    llm = ChatGroq(model=config.GROQ_MODEL, api_key=config.GROQ_API_KEY, temperature=0)
    response = llm.invoke([
        SystemMessage(content="You are Cortex, an expert Data Reliability Engineer."),
        HumanMessage(content=prompt),
    ])
    return response.content


def generate_fix(root_cause: str, evidence: dict) -> str:
    if config.MOCK_MODE:
        return (
            "-- MOCK FIX (human approval required)\n"
            f"-- Root cause: {root_cause}\n"
            f"-- Evidence: {evidence}\n"
            "-- Corrective action: restore the affected schema/lineage contract "
            "or update the dependent transformation to the new column name."
        )

    from langchain_groq import ChatGroq
    from langchain_core.messages import HumanMessage, SystemMessage

    system = (
        "You are a data engineering incident-response agent. Given a root cause "
        "and grounded DataHub evidence, propose the smallest concrete corrective "
        "action. This is shown to a human reviewer and must never be described as "
        "already executed."
    )
    user = f"Root cause: {root_cause}\n\nEvidence: {evidence}\n\nPropose the minimal safe fix."
    llm = ChatGroq(model=config.GROQ_MODEL, api_key=config.GROQ_API_KEY, temperature=0)
    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return response.content
