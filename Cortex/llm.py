"""
One function, on purpose. If you ever need to swap models or providers,
this is the only place that changes.
"""
from Cortex import config

log = config.get_logger("Cortex.llm")


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

    # TODO: real call, e.g. Anthropic SDK:
    #   from anthropic import Anthropic
    #   client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    #   response = client.messages.create(model=config.LLM_MODEL, ...)
    raise NotImplementedError("Wire up a real LLM call once MOCK_MODE=false")
