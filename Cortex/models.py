"""
The three shapes that matter in Cortex. Kept as plain dataclasses on
purpose — no ORM, no validation framework. If a field is wrong you'll
see it immediately in a print/log, not buried in a stack trace from
some other library.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class Incident:
    """What triggered Cortex to run."""
    id: str = field(default_factory=lambda: _new_id("inc"))
    incident_type: str = "unclassified"        # e.g. "schema_drift" — must match a procedures/*.yaml
    trigger_asset_urn: str = ""                  # the DataHub asset that broke
    description: str = ""                        # human-readable symptom, e.g. "Revenue Dashboard shows $0"
    timestamp: str = field(default_factory=_now)


@dataclass
class AssetSnapshot:
    """
    A point-in-time fingerprint of an asset's structure, used both to
    store 'what the world looked like when this fix worked' and to
    diff against 'what the world looks like now'.

    Deliberately narrow — three structural fields plus two that catch
    the cases structure alone misses (logic changes, silent run failures).
    See conversation history for why: a wider snapshot sounds more
    rigorous but most of the extra fields don't have a clean DataHub
    source and aren't worth the build time for a hackathon MVP.
    """
    asset_urn: str = ""
    upstream_urns: list[str] = field(default_factory=list)
    downstream_urns: list[str] = field(default_factory=list)
    schema_fields: list[str] = field(default_factory=list)   # sorted "name:type" strings
    model_logic_hash: Optional[str] = None                     # hash of compiled dbt SQL, if available
    last_run_status: Optional[str] = None                      # "success" | "failed" | "unknown"


@dataclass
class Experience:
    """
    The one artifact Cortex produces per incident, whether or not it
    ever gets promoted to DataHub. This is what gets embedded and
    stored in episodic memory (Chroma), always — promotion to DataHub
    is a separate, conditional step (see reflection.py).
    """
    id: str = field(default_factory=lambda: _new_id("exp"))
    incident_id: str = ""
    incident_type: str = "unclassified"
    trigger_asset_urn: str = ""
    timestamp: str = field(default_factory=_now)

    procedure_used: str = "default"

    snapshot: Optional[AssetSnapshot] = None      # state of the world when this experience was recorded

    root_cause: str = ""
    fix_proposed: str = ""
    fix_applied: bool = False
    outcome: str = "pending"                        # "success" | "failed" | "pending_review"
    nodes_visited: int = 0                           # cheap proxy for "how much graph we had to search"

    matched_prior_experience_id: Optional[str] = None
    similarity_score: Optional[float] = None

    novel: bool = True
    promoted: bool = False

    embedding_text: str = ""                         # what actually got embedded, for debugging retrieval


@dataclass
class DiffResult:
    """Output of comparing a stored snapshot against the current DataHub state."""
    structural_diff: bool = False
    logic_diff: bool = False
    run_status_diff: bool = False
    details: dict = field(default_factory=dict)      # human-readable notes per field, for logging/debugging

    @property
    def any_diff(self) -> bool:
        return self.structural_diff or self.logic_diff or self.run_status_diff
