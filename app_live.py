"""
Runs Cortex against your REAL DataHub instance and REAL Groq LLM —
separate from app.py's polished mock demo, so that stays untouched and
reliable as your video source.

Requires in .env:
    CORTEX_MOCK_MODE=false
    GROQ_API_KEY=<your key>

Before running, plant the incident for real:
    python scripts/simulate_incident.py break

Then:
    python app_live.py

Note on scope: this uses the Snowflake table itself as the incident's
trigger_asset_urn (not the downstream PowerBI dashboard) — the current
investigate()/verify_and_diff() nodes snapshot only the trigger asset
itself, not a multi-hop walk upstream from it. Using the actual asset
whose last_modified we're mutating means the diff logic sees the real
change. A "symptom is downstream, cause is upstream" version would need
investigate() extended to walk one more hop — noted as a real next step,
not pretended to already exist.
"""
from cortex.graph import run_incident
from cortex.models import Incident

TARGET_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)"


def approve_in_terminal(incident, fix) -> bool:
    print(f"\n{'='*60}")
    print(f"HUMAN REVIEW — Incident: {incident.description}")
    print(f"Asset: {incident.trigger_asset_urn}")
    print(f"Proposed fix:\n{fix}")
    print(f"{'='*60}")
    answer = input("Approve this fix? [y/N] ").strip().lower()
    return answer == "y"


def main():
    incident = Incident(
        incident_type="freshness",
        trigger_asset_urn=TARGET_URN,
        description="order_details analytics table has not refreshed recently — downstream PowerBI report may show stale totals",
    )

    result = run_incident(incident, hitl_approve_fn=approve_in_terminal)

    print(f"\n{'='*60}")
    print(f"root_cause:    {result['root_cause']}")
    print(f"reused_fix:    {result.get('reused_fix', False)}")
    print(f"nodes_visited: {result.get('nodes_visited')}")
    print(f"outcome:       {result.get('outcome')}")
    print(f"promoted:      {result.get('should_promote')}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
