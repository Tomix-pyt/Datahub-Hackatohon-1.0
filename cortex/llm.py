"""
One function, on purpose. If you ever need to swap models or providers,
this is the only place that changes.
"""
from typing import Optional

from cortex import config

log = config.get_logger("cortex.llm")

from datetime import datetime, timezone
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage

llm = ChatGroq(model=config.GROQ_MODEL, api_key=config.GROQ_API_KEY, temperature=0)


def diagnose_root_cause(
    description: str,
    current_snapshot: dict,
    diff_details: Optional[dict] = None,
    lineage_graph: Optional[dict] = None,
) -> str:
    target_urn = current_snapshot.get("asset_urn", "")
    compact_graph_summary = _summarize_lineage_graph(lineage_graph, target_urn)
    
    # Get current UTC time as an explicit baseline for freshness math
    current_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    prompt = f"""
        You are Cortex, an automated Data Reliability Engineer.
        Diagnose the root cause of the incident using the grounded evidence below.

        CURRENT PLATFORM TIME: {current_time_str}

        TARGET ASSET SNAPSHOT:
        URN: {target_urn}
        Last Modified: {current_snapshot.get('last_modified')}
        Current Schema Fields ({len(current_snapshot.get('schema_fields', []))} total):
        {current_snapshot.get('schema_fields')}

        COMPUTED DIFF & ANOMALIES:
        {diff_details if diff_details else "No direct structural diff flags."}

        CONNECTED LINEAGE GRAPH SUMMARY:
        {compact_graph_summary}

        INSTRUCTIONS:
        1. Compare 'Last Modified' against 'CURRENT PLATFORM TIME'. If 'Last Modified' is > 24 hours behind CURRENT PLATFORM TIME, flag an SLA Freshness Breach!
        2. Check for schema column mismatches vs upstream models.
        3. State the root cause clearly and concisely in 2-3 sentences based strictly on this evidence.
        """

    response = llm.invoke([
        SystemMessage(content=prompt),
        HumanMessage(content='What is the root cause of the incident based on the evidence provided?')])
    root_cause = response.content
    log.info(f"[GROQ] Diagnosed root cause:\n{root_cause}")
    return root_cause


def diagnose_root_cause(description: str, current_snapshot: dict, diff_details: dict | None, lineage_graph: dict | None) -> str:
    """
    Actually reasons over real evidence to determine root cause — this is
    what was missing before: investigate() used to hardcode a schema-drift
    string regardless of the real incident. Now the LLM sees the real
    incident description, the real current asset state, and any known
    diff, and has to produce a cause consistent with THAT evidence.
    """
    if config.MOCK_MODE:
        if diff_details and "schema" in diff_details:
            return f"Schema drift: {diff_details['schema']}"
        return "Schema drift: customer_id renamed to customer_uuid in raw_sales"

    from langchain_groq import ChatGroq
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = ChatGroq(model=config.GROQ_MODEL, api_key=config.GROQ_API_KEY, temperature=0)

    system = (
        "You are a data engineering incident-response agent. Given an "
        "incident description and real evidence gathered from a metadata "
        "graph (DataHub) — including the asset's upstream/downstream "
        "lineage, schema fields, and last known run status — determine "
        "the most likely root cause. Base your answer ONLY on the evidence "
        "given. Be specific and concise, one to two sentences."
    )
    user = (
        f"Incident: {description}\n\n"
        f"Current asset state: {current_snapshot}\n\n"
        f"Lineage graph summary: {lineage_graph}\n\n"
        f"Known changes since last investigation: {diff_details or 'none detected — no prior investigation, or nothing changed'}\n\n"
        f"What is the root cause?"
    )

    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    root_cause = response.content
    log.info(f"[GROQ] Diagnosed root cause:\n{root_cause}")
    return root_cause


def generate_fix(root_cause: str, evidence: dict) -> str:
    """Ask the LLM to draft a fix given the investigation evidence."""
    if config.MOCK_MODE:
        fix = (
            f"-- MOCK FIX (no live LLM call)\n"
            f"-- Root cause: {root_cause}\n"
            f"-- Evidence: {evidence}\n"
            f"ALTER VIEW daily_metrics AS\n"
            f"SELECT customer_uuid AS customer_id, amount, created_at\n"
            f"FROM raw_sales;  -- updated for renamed column"
        )
        log.debug(f"[MOCK] Generated fix:\n{fix}")
        return fix

    from langchain_groq import ChatGroq
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = ChatGroq(model=config.GROQ_MODEL, api_key=config.GROQ_API_KEY, temperature=0)

    system = (
        "You are a data engineering incident-response agent. Given a root "
        "cause and evidence gathered from a metadata graph (DataHub), "
        "propose a concrete, minimal fix as SQL or a short explanation of "
        "the corrective action. Be specific and concise — this gets shown "
        "to a human reviewer for approval, not executed automatically." 
        "Coincise and avoid too much grandiose explanation. Focus on the actionable fix."

    )
    user = f"Root cause: {root_cause}\n\nEvidence: {evidence}\n\nPropose a fix."

    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    fix = response.content
    log.info(f"[GROQ] Generated fix:\n{fix}")
    return fix