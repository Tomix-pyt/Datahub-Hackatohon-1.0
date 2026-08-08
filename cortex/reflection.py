"""Promotion gate and recurrence guardrails."""
from __future__ import annotations

from cortex import config
from cortex.models import Experience

log = config.get_logger("cortex.reflection")


def check_recurrence_despite_no_diff(
    prior_experience: Experience | None,
    diff_found: bool,
    same_asset: bool,
) -> tuple[bool, str]:
    if not same_asset or prior_experience is None:
        return False, "no same-asset recurrence contradiction"

    if not diff_found and prior_experience.fix_applied and prior_experience.outcome == "success":
        return True, (
            "The same asset reproduced the incident even though the stored structural "
            "fingerprint is unchanged after a previously successful fix. Reuse is unsafe; "
            "Cortex must investigate again and treat the prior traversal as context."
        )
    return False, "no contradiction detected"


def reflect(experience: Experience) -> tuple[bool, str]:
    """Decide whether this experience has earned semantic promotion."""
    if not experience.fix_applied:
        return False, "fix was not approved/applied — nothing proven yet"
    if experience.outcome != "success":
        return False, f"outcome was '{experience.outcome}', not a validated success"
    return True, "human-approved successful experience is eligible for semantic promotion"
