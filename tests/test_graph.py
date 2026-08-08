"""Regression tests for Cortex's three core memory decisions."""
from pathlib import Path

import pytest

from cortex import config
from cortex.graph import run_incident
from cortex.memory_episodic import reset_client
from cortex.models import Incident


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setattr(config, "MOCK_MODE", True)
    monkeypatch.setenv("CORTEX_MOCK_VERSION", "v1")
    reset_client()
    yield
    reset_client()


def approve(incident, fix):
    return True


def reject(incident, fix):
    return False


def incident(asset="urn:dashboard:revenue_dashboard", description="Revenue Dashboard showing $0"):
    return Incident(
        incident_type="schema_drift",
        trigger_asset_urn=asset,
        description=description,
    )


def test_cold_start_investigates_and_promotes():
    result = run_incident(incident(), hitl_approve_fn=approve)
    assert result["reused_fix"] is False
    assert result["experience"].novel is True
    assert result["experience"].fix_applied is True
    assert result["outcome"] == "success"
    assert result["should_promote"] is True


def test_cross_asset_successful_pattern_is_reused_without_snapshot_diff():
    run_incident(incident(), hitl_approve_fn=approve)
    result = run_incident(
        incident("urn:dashboard:regional_revenue_dashboard", "Regional Revenue Dashboard showing $0"),
        hitl_approve_fn=approve,
    )
    assert result["reused_fix"] is True
    assert result["nodes_visited"] == 1
    assert result["experience"].matched_prior_experience_id is not None


def test_same_asset_no_change_is_a_contradiction_and_reinvestigates():
    run_incident(incident(), hitl_approve_fn=approve)
    result = run_incident(incident(), hitl_approve_fn=approve)
    assert result["reused_fix"] is False
    assert result["recurrence_flag"] is True
    assert result["experience"].matched_prior_experience_id is not None
    assert result["experience"].evidence_context["recurrence_flag"] is True
    assert result["experience"].evidence_context["diff_details"]["prior_experience_context"] is not None


def test_same_asset_upstream_change_is_not_misclassified_as_no_diff(monkeypatch):
    run_incident(incident(), hitl_approve_fn=approve)
    monkeypatch.setenv("CORTEX_MOCK_VERSION", "v2")
    result = run_incident(incident(), hitl_approve_fn=approve)
    assert result["reused_fix"] is False
    assert result["recurrence_flag"] is False
    assert result["diff_found"] is True
    assert result["experience"].matched_prior_experience_id is not None


def test_rejected_fix_is_not_reused_later():
    first = run_incident(incident(), hitl_approve_fn=reject)
    assert first["outcome"] == "rejected"
    assert first["should_promote"] is False

    second = run_incident(
        incident("urn:dashboard:regional_revenue_dashboard", "Revenue Dashboard showing $0"),
        hitl_approve_fn=approve,
    )
    assert second["reused_fix"] is False
