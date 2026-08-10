"""
The gatekeeper. This is the single most important node in the whole
system to get right — everything else can be simple as long as this
stays disciplined about what earns a place in DataHub.
"""
from cortex import config
from cortex.diff import compute_diff
from cortex.memory_semantic import DataHubClient
from cortex.models import AssetSnapshot, Experience

log = config.get_logger("cortex.reflection")


def check_recurrence_despite_no_diff(
    prior_experience: dict,
    diff_found: bool,
    same_asset: bool,
) -> tuple[bool, str]:
    """
    Handles the case you and I dug into: same incident type fires again,
    on the SAME asset, and the diff says nothing changed. If the prior
    fix was already applied and marked successful, that's a contradiction —
    a fix that truly worked under unchanged conditions cannot legitimately
    fail again on that same asset.

    Deliberately scoped to same_asset=True only. A different asset that
    happens to look structurally identical to a past precedent is NOT a
    contradiction — it's a legitimate case of "I've seen this exact
    pattern before, just on a different table" and reuse is appropriate.
    """
    if not same_asset:
        return False, "different asset — structural match is a legitimate pattern reuse, not a recurrence"

    prior_was_applied_success = (
        prior_experience.get("fix_applied") and prior_experience.get("outcome") == "success"
    )

    if not diff_found and prior_was_applied_success:
        return True, (
            "Recurrence with no detected diff, but the prior fix was already "
            "applied and marked successful. This likely means the original "
            "diagnosis (or our diff coverage) was incomplete — routing to full "
            "investigation and flagging for human review rather than reusing "
            "the fix blindly."
        )

    return False, "no contradiction detected"
