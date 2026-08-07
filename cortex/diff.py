"""
The comparison logic behind 'what changed since the last time this
worked'. Kept as one small pure function — easy to unit test in
isolation, no I/O, no side effects.
"""
from dataclasses import dataclass, field
from typing import Any, Dict

from cortex import config
from cortex.models import AssetSnapshot, DiffResult

log = config.get_logger("cortex.diff")


def compute_diff(old: AssetSnapshot, current: AssetSnapshot) -> DiffResult:
    result = DiffResult()

    if sorted(old.upstream_urns) != sorted(current.upstream_urns):
        result.structural_diff = True
        result.details["upstream"] = f"{old.upstream_urns} -> {current.upstream_urns}"

    if sorted(old.downstream_urns) != sorted(current.downstream_urns):
        result.structural_diff = True
        result.details["downstream"] = f"{old.downstream_urns} -> {current.downstream_urns}"

    if sorted(old.schema_fields) != sorted(current.schema_fields):
        result.structural_diff = True
        result.details["schema"] = f"{old.schema_fields} -> {current.schema_fields}"

    if old.model_logic_hash != current.model_logic_hash:
        result.logic_diff = True
        result.details["logic_hash"] = f"{old.model_logic_hash} -> {current.model_logic_hash}"

    if old.last_run_status != current.last_run_status:
        result.run_status_diff = True
        result.details["run_status"] = f"{old.last_run_status} -> {current.last_run_status}"

    if result.any_diff:
        log.info(f"Diff found: {result.details}")
    else:
        log.info("No diff found between stored snapshot and current state")

    return result

def compute_lineage_diff(
    target_snapshot: dict, lineage_graph: dict) -> dict:
    """Computes schema mismatches and freshness delays between target asset and upstream parents."""
    target_fields = {
        f.split(":")[0]: f.split(":")[1] if ":" in f else "UNKNOWN"
        for f in target_snapshot.get("schema_fields", [])
    }
    mismatches = []
    upstream_summaries = {}

    # 1. Compare target against immediate upstream producers
    for upstream_urn in target_snapshot.get("upstream_urns", []):
        if upstream_urn in lineage_graph:
            up_snap = lineage_graph[upstream_urn]
            up_fields = {
                f.split(":")[0]: f.split(":")[1] if ":" in f else "UNKNOWN"
                for f in up_snap.get("schema_fields", [])
            }

            # Store compact upstream field list (top 10 sample)
            upstream_summaries[upstream_urn] = up_snap.get("schema_fields", [])[
                :10
            ]

            # Detect missing or type-mismatched columns
            for col, dtype in target_fields.items():
                if col not in up_fields:
                    mismatches.append(
                        f"Target column '{col}' missing from upstream parent ({upstream_urn.split(',')[-1]})"
                    )
                elif up_fields[col] != dtype:
                    mismatches.append(
                        f"Type mismatch on '{col}': target is {dtype}, upstream parent is {up_fields[col]}"
                    )

    # 2. Freshness check
    target_age = target_snapshot.get("freshness_age_hours", 0) or 0
    is_stale = target_age > 24.0

    return {
        "lineage_schema_mismatches": mismatches,
        "upstream_schema_summaries": upstream_summaries,
        "freshness": {
            "last_modified": target_snapshot.get("last_modified"),
            "age_hours": target_age,
            "is_stale": is_stale,}}