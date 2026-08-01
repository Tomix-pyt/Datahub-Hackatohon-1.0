"""
Run with: pytest tests/test_graph.py -v

Locks in the three behaviors we verified by hand — if a future change
breaks any of these, you'll know immediately instead of discovering it
mid-demo.
"""
import shutil

import pytest

from cortex import config
from cortex.graph import run_incident
from cortex.memory_episodic import reset_client
from cortex.models import Incident


@pytest.fixture(autouse=True)
def clean_episodic_memory():
    """Every test starts with a fresh, in-memory Chroma store — avoids
    on-disk SQLite lock contention entirely and keeps tests fast and
    independent of each other."""
    config.CHROMA_PERSIST_DIR = ":memory:"
    reset_client()
    yield
    reset_client()


def approve(incident, fix):
    return True


def reject(incident, fix):
    return False


def test_cold_start_investigates_fully():
    incident = Incident(
        incident_type="schema_drift",
        trigger_asset_urn="urn:dashboard:revenue_dashboard",
        description="Revenue Dashboard showing $0",
    )
    result = run_incident(incident, hitl_approve_fn=approve)
    assert result["reused_fix"] is False
    assert result["experience"].novel is True
    assert result["should_promote"] is True


def test_clean_reuse_on_different_asset():
    incident_1 = Incident(
        incident_type="schema_drift",
        trigger_asset_urn="urn:dashboard:revenue_dashboard",
        description="Revenue Dashboard showing $0",
    )
    run_incident(incident_1, hitl_approve_fn=approve)

    incident_2 = Incident(
        incident_type="schema_drift",
        trigger_asset_urn="urn:dashboard:regional_revenue_dashboard",
        description="Regional Revenue Dashboard showing $0",
    )
    result_2 = run_incident(incident_2, hitl_approve_fn=approve)

    assert result_2["reused_fix"] is True
    assert result_2["nodes_visited"] == 1
    assert result_2["experience"].matched_prior_experience_id is not None


def test_same_asset_recurrence_is_flagged_not_reused():
    incident_1 = Incident(
        incident_type="schema_drift",
        trigger_asset_urn="urn:dashboard:revenue_dashboard",
        description="Revenue Dashboard showing $0",
    )
    run_incident(incident_1, hitl_approve_fn=approve)

    incident_2 = Incident(
        incident_type="schema_drift",
        trigger_asset_urn="urn:dashboard:revenue_dashboard",
        description="Revenue Dashboard showing $0",
    )
    result_2 = run_incident(incident_2, hitl_approve_fn=approve)

    assert result_2["reused_fix"] is False, "same-asset recurrence with no diff must NOT be blindly reused"
    assert result_2["recurrence_flag"] is True


def test_rejected_fix_is_not_reused_later():
    incident_1 = Incident(
        incident_type="schema_drift",
        trigger_asset_urn="urn:dashboard:revenue_dashboard",
        description="Revenue Dashboard showing $0",
    )
    result_1 = run_incident(incident_1, hitl_approve_fn=reject)
    assert result_1["outcome"] == "failed"
    assert result_1["should_promote"] is False

    incident_2 = Incident(
        incident_type="schema_drift",
        trigger_asset_urn="urn:dashboard:regional_revenue_dashboard",
        description="Regional Revenue Dashboard showing $0",
    )
    result_2 = run_incident(incident_2, hitl_approve_fn=approve)
    assert result_2["reused_fix"] is False, "a rejected precedent must never be reused"
