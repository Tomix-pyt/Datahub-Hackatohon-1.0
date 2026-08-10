import json
from typing import Optional
from cortex import config
log = config.get_logger("cortex.llm")
from datetime import datetime, timezone
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage


llm = ChatGroq(model=config.GROQ_MODEL, api_key=config.GROQ_API_KEY, temperature=0)

def _summarize_lineage_graph(lineage_graph: Optional[dict], target_urn: str) -> str:
    """Compresses 20+ graph node snapshots into a token-efficient summary (<600 tokens)."""
    if not lineage_graph:
        log.warning("No lineage graph provided for summarization.")
        return "No connected lineage graph context."

    summary_lines = []
    for urn, snap in lineage_graph.items():
        if urn == target_urn:
            continue  # Target URN is already rendered in full detail

        last_mod = snap.get("last_modified") or "N/A"
        fields = snap.get("schema_fields") or []
        field_count = len(fields)

        sample_fields = fields[:3]
        sample_str = f"{sample_fields}..." if field_count > 3 else str(sample_fields)

        # Short URN alias for readability
        short_urn = urn.split(",")[-1] if "," in urn else urn
        summary_lines.append(
            f"- Asset: {short_urn}\n"
            f"  Last Modified: {last_mod} | Total Fields: {field_count} | Sample: {sample_str}"
        )

    # Cap summary at top 8 relevant nodes to guarantee token safety
    return "\n".join(summary_lines[:8])

def diagnose_root_cause(
    description: str,
    current_snapshot: dict,
    diff_details: dict,
    lineage_graph: Optional[dict] = None,
    incident_type: str = "unclassified",
) -> str:
    target_urn = current_snapshot.get("asset_urn", "")
    compact_graph = _summarize_lineage_graph(lineage_graph, target_urn) if lineage_graph else "No graph available."
    current_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    target_fields = current_snapshot.get("schema_fields", [])
    target_fields_sample = target_fields[:15] if len(target_fields) > 15 else target_fields

    # Extract relevant evidence based on incident type
    mismatches = diff_details.get("lineage_schema_mismatches", [])
    freshness = diff_details.get("freshness", {})

    prompt = f"""
        You are Cortex, an automated Data Reliability Engineer.
        Diagnose the root cause using the grounded evidence below.

        CURRENT PLATFORM TIME: {current_time_str}
        CLASSIFIED INCIDENT TYPE: {incident_type}

        INCIDENT DESCRIPTION:
        {description}

        TARGET ASSET SNAPSHOT:
        URN: {target_urn}
        Last Modified: {current_snapshot.get('last_modified')}
        Age (Hours): {freshness.get('age_hours', 'N/A')}
        Schema Fields Sample ({len(target_fields)} total):
        {target_fields_sample}

        SCHEMA MISMATCHES (if any):
        {chr(10).join(mismatches) if mismatches else 'No schema mismatches detected.'}

        FRESHNESS STATUS:
        {json.dumps(freshness, indent=2)}

        CONNECTED LINEAGE GRAPH SUMMARY:
        {compact_graph}

        INSTRUCTIONS:
        1. If SCHEMA DRIFT: focus on the mismatches list. State which column changed, where, and what the fix should be.
        2. If FRESHNESS: focus on the age. State how stale it is and which upstream source is delaying.
        3. If UNCLASSIFIED: default to the most likely cause based on evidence.
        4. State the root cause in 2-3 concise sentences based strictly on the evidence.
        5. If evidence is insufficient, state that explicitly.
        """
    response = llm.invoke([
                SystemMessage(content="You are Cortex, an expert Data Reliability Engineer."),
                HumanMessage(content=prompt)])
    return response.content


def generate_fix(root_cause: str, evidence: dict) -> str:
    """
    Generate a concrete fix based on root cause and evidence.
    Evidence contains 'lineage_schema_mismatches' and 'freshness'.
    """
    if config.MOCK_MODE:
        fix = ( f"-- MOCK FIX (no live LLM call)\n"
            f"-- Root cause: {root_cause}\n"
            f"-- Evidence: {evidence}\n"
            f"ALTER VIEW daily_metrics AS\n"
            f"SELECT customer_uuid AS customer_id, amount, created_at\n"
            f"FROM raw_sales;  -- updated for renamed column"
        )
        log.debug(f"[MOCK] Generated fix:\n{fix}")
        return fix
    target_asset = evidence.get("asset_urn", "target_table")
    if ":" in target_asset:
        target_asset = target_asset.split(":")[-1]

    mismatches = evidence.get("lineage_schema_mismatches", [])
    freshness = evidence.get("freshness", {})

    # Build specific evidence section
    evidence_text = ""
    if mismatches:
        evidence_text += "CONCRETE MISMATCHES FOUND:\n"
        for m in mismatches[:5]:  # limit to avoid token bloat
            evidence_text += f"  - {m}\n"
    else:
        evidence_text += "No schema mismatches detected.\n"

    if freshness.get("is_stale"):
        evidence_text += f"\nFRESHNESS: data is {freshness.get('age_hours', 0):.1f} hours old (stale).\n"

    # Determine if this is a schema drift or freshness issue
    is_schema_drift = bool(mismatches)
    is_freshness = freshness.get("is_stale", False)

    if is_schema_drift and not is_freshness:
        # Schema drift specific prompt
        system = (
            "You are a data engineering incident-response agent. "
            "Based on the evidence below, propose a concrete, minimal SQL fix "
            "that resolves the schema mismatches. Use the actual column names "
            "and the target table name provided. Output only the SQL statement "
            "and a one-line summary."
        )
        user = (
            f"Target asset: {target_asset}\n"
            f"Root cause: {root_cause}\n\n"
            f"{evidence_text}\n\n"
            f"Propose a SQL fix (e.g., ALTER TABLE ... ADD COLUMN, RENAME COLUMN, ALTER COLUMN TYPE, etc.) "
            f"that makes the target schema compatible with upstream sources. "
            f"Be specific about which columns need to change."
        )
    elif is_freshness and not is_schema_drift:
        # Freshness specific prompt
        system = (
            "You are a data engineering incident-response agent. "
            "The data is stale because the pipeline failed. Propose an action "
            "to restart the pipeline or check ingestion, not a schema change."
        )
        user = (
            f"Target asset: {target_asset}\n"
            f"Root cause: {root_cause}\n\n"
            f"{evidence_text}\n\n"
            f"Propose a fix (e.g., restart the pipeline, check upstream job status). "
            f"Provide a clear step-by-step action plan."
        )
    else:
        # Fallback generic
        system = (
            "You are a data engineering incident-response agent. "
            "Based on the evidence, propose a concrete fix."
        )
        user = (
            f"Target asset: {target_asset}\n"
            f"Root cause: {root_cause}\n\n"
            f"{evidence_text}\n\n"
            f"Propose a fix."
        )

    try:
        response = llm.invoke([
            SystemMessage(content=system),
            HumanMessage(content=user)
        ])
        fix = str(response.content).strip()
        log.info(f"[GROQ] Generated fix:\n{fix}")
        return fix
    except Exception as e:
        log.error(f"[GROQ] Failed to generate fix: {e}")
        return f'LLm failed to generate fix for root cause: {root_cause}'