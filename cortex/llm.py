"""
One function, on purpose. If you ever need to swap models or providers,
this is the only place that changes.
"""
from cortex import config

log = config.get_logger("cortex.llm")


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

    llm = ChatGroq(model=config.GROQ_MODEL, api_key= config.GROQ_API_KEY, temperature=0)

    system = (
        "You are a data engineering incident-response agent. Given a root "
        "cause and evidence gathered from a metadata graph (DataHub), "
        "propose a concrete, minimal fix as SQL or a short explanation of "
        "the corrective action. Be specific and concise — this gets shown "
        "to a human reviewer for approval, not executed automatically."
    )
    user = f"Root cause: {root_cause}\n\nEvidence: {evidence}\n\nPropose a fix."

    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    fix = response.content
    log.info(f"[GROQ] Generated fix:\n{fix}")
    return fix
