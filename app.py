"""
Run this first, before touching anything else:

    python app.py

Demonstrates all three real paths in one run, with zero API keys:
  1. COLD START      — brand new incident, full investigation
  2. CLEAN REUSE      — same failure pattern on a DIFFERENT asset, no
                         diff against that asset's own history -> instant reuse
  3. CONTRADICTION    — the SAME asset breaks again with nothing changed
                         -> Cortex refuses to blindly reuse, flags for review

Each run uses an auto-approve HITL callback so this runs unattended;
swap `always_approve` for a real input()-based prompt (or your UI's
confirm handler) when you want to click through it interactively.
"""
from cortex.graph import run_incident
from cortex.models import Incident


def always_approve(incident, fix) -> bool:
    print(f"\n  [HITL] Auto-approving fix for {incident.trigger_asset_urn}")
    return True


def summarize(label: str, result: dict):
    print(f"\n--- {label} ---")
    print(f"  root_cause:    {result['root_cause']}")
    print(f"  reused_fix:    {result.get('reused_fix', False)}")
    print(f"  nodes_visited: {result.get('nodes_visited')}")
    print(f"  outcome:       {result.get('outcome')}")
    print(f"  promoted:      {result.get('should_promote')}")
    exp = result.get("experience")
    if exp:
        print(f"  matched_prior: {exp.matched_prior_experience_id}")


def main():
    # --- Run 1: COLD START ---
    print("\n\n########## RUN 1: COLD START ##########")
    incident_1 = Incident(
        incident_type="schema_drift",
        trigger_asset_urn="urn:dashboard:revenue_dashboard",
        description="Revenue Dashboard showing $0, customer join appears broken",
    )
    result_1 = run_incident(incident_1, hitl_approve_fn=always_approve)
    summarize("RUN 1 (cold start)", result_1)

    # --- Run 2: CLEAN REUSE on a different but structurally identical asset ---
    print("\n\n########## RUN 2: CLEAN REUSE (different asset, same pattern) ##########")
    incident_2 = Incident(
        incident_type="schema_drift",
        trigger_asset_urn="urn:dashboard:regional_revenue_dashboard",
        description="Regional Revenue Dashboard showing $0, customer join appears broken",
    )
    result_2 = run_incident(incident_2, hitl_approve_fn=always_approve)
    summarize("RUN 2 (clean reuse)", result_2)

    # --- Run 3: CONTRADICTION — the SAME asset from run 1 breaks again, nothing changed ---
    print("\n\n########## RUN 3: CONTRADICTION (same asset, no diff, breaks again) ##########")
    incident_3 = Incident(
        incident_type="schema_drift",
        trigger_asset_urn="urn:dashboard:revenue_dashboard",
        description="Revenue Dashboard showing $0, customer join appears broken",
    )
    result_3 = run_incident(incident_3, hitl_approve_fn=always_approve)
    summarize("RUN 3 (contradiction)", result_3)

    print(f"\n{'='*70}")
    print("nodes_visited across runs:", result_1.get("nodes_visited"), "->",
          result_2.get("nodes_visited"), "->", result_3.get("nodes_visited"))
    print("Run 2 should be the cheapest (clean reuse). Run 3 should be flagged,")
    print("not instantly reused, even though it also found a precedent.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
