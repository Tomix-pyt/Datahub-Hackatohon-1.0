"""Pure comparison functions used by Cortex's warm-path safety checks."""
from __future__ import annotations

from cortex import config
from cortex.models import AssetSnapshot, DiffResult

log = config.get_logger("cortex.diff")


def _snapshot_signature(snapshot: AssetSnapshot) -> dict:
    return {
        "upstream_urns": sorted(snapshot.upstream_urns),
        "downstream_urns": sorted(snapshot.downstream_urns),
        "schema_fields": sorted(snapshot.schema_fields),
        "upstream_schemas": {
            k: sorted(v) for k, v in sorted(snapshot.upstream_schemas.items())
        },
        "last_run_status": snapshot.last_run_status,
    }


def compute_diff(old: AssetSnapshot, current: AssetSnapshot) -> DiffResult:
    result = DiffResult()
    old_sig = _snapshot_signature(old)
    cur_sig = _snapshot_signature(current)

    for key in ("upstream_urns", "downstream_urns", "schema_fields", "upstream_schemas"):
        if old_sig[key] != cur_sig[key]:
            result.structural_diff = True
            result.details[key] = f"{old_sig[key]} -> {cur_sig[key]}"

    if old_sig["last_run_status"] != cur_sig["last_run_status"]:
        result.run_status_diff = True
        result.details["run_status"] = f"{old_sig['last_run_status']} -> {cur_sig['last_run_status']}"

    if result.any_diff:
        log.info("Diff found: %s", result.details)
    else:
        log.info("No diff found between stored snapshot and current state")
    return result


def compute_graph_state_diff(old_graph: dict, current_graph: dict) -> dict:
    """Compare the actual traversal footprint, not just the trigger asset.

    This is the key recurrence guardrail: if the same incident returns but an
    upstream node changed, Cortex must *not* call that a contradiction merely
    because the trigger asset itself looks unchanged.
    """
    old_urns = set(old_graph)
    current_urns = set(current_graph)
    added = sorted(current_urns - old_urns)
    removed = sorted(old_urns - current_urns)
    changed = {}

    for urn in sorted(old_urns & current_urns):
        old = old_graph[urn]
        cur = current_graph[urn]
        fields = {}
        for key in ("upstream_urns", "downstream_urns", "schema_fields", "upstream_schemas", "last_run_status"):
            old_value = old.get(key)
            cur_value = cur.get(key)
            if isinstance(old_value, list):
                old_value = sorted(old_value)
            if isinstance(cur_value, list):
                cur_value = sorted(cur_value)
            if old_value != cur_value:
                fields[key] = {"before": old_value, "after": cur_value}
        if fields:
            changed[urn] = fields

    return {
        "changed": bool(added or removed or changed),
        "added_nodes": added,
        "removed_nodes": removed,
        "changed_nodes": changed,
    }


def compute_lineage_diff(target_snapshot: dict, lineage_graph: dict) -> dict:
    """Compare target schema to immediate upstream schemas and check freshness."""
    target_fields = {
        f.split(":", 1)[0]: f.split(":", 1)[1] if ":" in f else "UNKNOWN"
        for f in target_snapshot.get("schema_fields", [])
    }
    mismatches = []
    upstream_summaries = {}
    upstream_urns = target_snapshot.get("upstream_urns", [])
    valid_upstreams = {urn: lineage_graph[urn] for urn in upstream_urns if urn in lineage_graph}

    for urn, up_snap in valid_upstreams.items():
        upstream_summaries[urn] = up_snap.get("schema_fields", [])[:10]

    target_name = target_snapshot.get("asset_urn", "").split(".")[-1]
    primary_parent_urn = next(
        (
            urn for urn in valid_upstreams
            if "dataPlatform:dbt" in urn or urn.split(".")[-1] == target_name
        ),
        None,
    )

    if primary_parent_urn:
        p_snap = valid_upstreams[primary_parent_urn]
        p_fields = {
            f.split(":", 1)[0]: f.split(":", 1)[1] if ":" in f else "UNKNOWN"
            for f in p_snap.get("schema_fields", [])
        }
        p_short = primary_parent_urn.split(",")[-1]
        for col, dtype in target_fields.items():
            if col not in p_fields:
                mismatches.append(f"Target column '{col}' missing from primary upstream model ({p_short})")
            elif p_fields[col] != dtype:
                mismatches.append(
                    f"Type mismatch on '{col}': target is {dtype}, primary upstream ({p_short}) is {p_fields[col]}"
                )
        for p_col in p_fields:
            if p_col not in target_fields:
                mismatches.append(f"Upstream column '{p_col}' in ({p_short}) is missing from target asset")
    else:
        combined_upstream = {}
        for urn, up_snap in valid_upstreams.items():
            for field in up_snap.get("schema_fields", []):
                name, _, dtype = field.partition(":")
                combined_upstream.setdefault(name, (dtype or "UNKNOWN", urn))
        for col, dtype in target_fields.items():
            if col not in combined_upstream:
                mismatches.append(f"Target column '{col}' does not exist in any upstream parent source")
            else:
                up_type, up_urn = combined_upstream[col]
                if up_type != dtype:
                    mismatches.append(
                        f"Type mismatch on '{col}': target is {dtype}, upstream ({up_urn.split(',')[-1]}) is {up_type}"
                    )

    target_age = target_snapshot.get("freshness_age_hours")
    target_age = float(target_age or 0)
    return {
        "lineage_schema_mismatches": mismatches,
        "upstream_schema_summaries": upstream_summaries,
        "freshness": {
            "last_modified": target_snapshot.get("last_modified"),
            "age_hours": target_age,
            "is_stale": target_age > config.FRESHNESS_SLA_HOURS,
        },
    }
