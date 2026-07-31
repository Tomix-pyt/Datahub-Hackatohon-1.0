"""
The comparison logic behind 'what changed since the last time this
worked'. Kept as one small pure function — easy to unit test in
isolation, no I/O, no side effects.
"""
from Cortex import config
from Cortex.models import AssetSnapshot, DiffResult

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
