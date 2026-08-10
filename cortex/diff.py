"""
The comparison logic behind 'what changed since the last time this
worked'. Kept as one small pure function — easy to unit test in
isolation, no I/O, no side effects.
"""
from dataclasses import dataclass, field
from typing import Optional
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

    if old.last_run_status != current.last_run_status:
        result.run_status_diff = True
        result.details["run_status"] = f"{old.last_run_status} -> {current.last_run_status}"

    if result.any_diff:
        log.info(f"Diff found: {result.details}")
    else:
        log.info("No diff found between stored snapshot and current state")

    return result

def compute_lineage_diff(target_snapshot: dict, lineage_graph: dict) -> dict:
    """Computes schema mismatches and freshness delays between target asset and upstream parents.

    Eliminates false positives on joined analytical tables by prioritizing direct
    transformation parents (e.g. dbt) or aggregating fields across all source parents.
    """
    target_fields = {
        f.split(":")[0]: f.split(":")[1] if ":" in f else "UNKNOWN"
        for f in target_snapshot.get("schema_fields", [])
    }
    mismatches = []
    upstream_summaries = {}

    # 1. Gather immediate upstream snapshots present in the lineage graph
    upstream_urns = target_snapshot.get("upstream_urns", [])
    valid_upstreams = {
        urn: lineage_graph[urn] for urn in upstream_urns if urn in lineage_graph
    }

    # Store compact upstream field list (top 10 sample) for metadata tracking
    for urn, up_snap in valid_upstreams.items():
        upstream_summaries[urn] = up_snap.get("schema_fields", [])[:10]

    # 2. Identify if there is a primary direct transformation parent (e.g., dbt model or matching table name)
    target_name = target_snapshot.get("asset_urn", "").split(".")[-1]
    primary_parent_urn = None

    for urn in valid_upstreams:
        if "dataPlatform:dbt" in urn or urn.split(".")[-1] == target_name:
            primary_parent_urn = urn
            break

    # 3. Perform Schema Diffing
    if primary_parent_urn:
        # --- Mode A: 1:1 Check against Primary Transformation Model ---
        p_snap = valid_upstreams[primary_parent_urn]
        p_fields = {
            f.split(":")[0]: f.split(":")[1] if ":" in f else "UNKNOWN"
            for f in p_snap.get("schema_fields", [])
        }
        p_short = primary_parent_urn.split(",")[-1]

        # Target columns missing or type-mismatched relative to primary parent
        for col, dtype in target_fields.items():
            if col not in p_fields:
                mismatches.append(
                    f"Target column '{col}' missing from primary upstream model ({p_short})"
                )
            elif p_fields[col] != dtype:
                mismatches.append(
                    f"Type mismatch on '{col}': target is {dtype}, primary upstream ({p_short}) is {p_fields[col]}"
                )

        # Primary parent columns dropped in target asset
        for p_col in p_fields:
            if p_col not in target_fields:
                mismatches.append(
                    f"Upstream column '{p_col}' in ({p_short}) is missing from target asset"
                )

    else:
        # --- Mode B: Aggregated Union Check Across Raw Upstream Sources ---
        combined_upstream = {}  # {col_name: (dtype, source_urn)}
        for urn, up_snap in valid_upstreams.items():
            for f in up_snap.get("schema_fields", []):
                col_name = f.split(":")[0]
                col_type = f.split(":")[1] if ":" in f else "UNKNOWN"
                if col_name not in combined_upstream:
                    combined_upstream[col_name] = (col_type, urn)

        # Flag target columns that exist in NO upstream source
        for col, dtype in target_fields.items():
            if col not in combined_upstream:
                mismatches.append(
                    f"Target column '{col}' does not exist in any upstream parent source"
                )
            else:
                up_type, up_urn = combined_upstream[col]
                if up_type != dtype:
                    up_short = up_urn.split(",")[-1]
                    mismatches.append(
                        f"Type mismatch on '{col}': target is {dtype}, upstream ({up_short}) is {up_type}"
                    )

    # 4. Freshness Check
    target_age = target_snapshot.get("freshness_age_hours", 0) or 0
    is_stale = target_age > 24.0

    return {
        "lineage_schema_mismatches": mismatches,
        "upstream_schema_summaries": upstream_summaries,
        "freshness": {
            "last_modified": target_snapshot.get("last_modified"),
            "age_hours": target_age,
            "is_stale": is_stale,
        },
    }
def determine_incident_type( seed_diff: Optional[dict],lineage_evidence: dict) -> str:
    """
    Layered classification using the evidence from compute_lineage_diff.
    Hierarchy: Schema Drift → Freshness → Unknown.
    """
    has_schema_mismatch = bool(
        (seed_diff and seed_diff.get("schema")) or
        lineage_evidence.get("lineage_schema_mismatches")
    )
    is_stale = lineage_evidence.get("freshness", {}).get("is_stale", False)
    age_hours = lineage_evidence.get("freshness", {}).get("age_hours", 0)

    if  not config.FRESHNESS_OVERRIDE_DISABLED and age_hours > 60 and is_stale:
        return "freshness"

    # LAYER 1: Schema Drift (highest priority)
    if has_schema_mismatch and age_hours > 60:
        return "schema_drift"

    # LAYER 2: Freshness Violation (data stale but schema intact)
    if is_stale:
        return "freshness"

    # LAYER 3: Unknown / fallback
    return "unclassified"