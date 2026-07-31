"""
Real implementation is intentionally left as clearly-marked TODOs —
don't guess at DataHub's exact GraphQL schema from memory; check the
live docs/schema once you're actually wired up.
"""
from Cortex import config
from Cortex.models import AssetSnapshot

log = config.get_logger("Cortex.memory_semantic")

# --- Mock fixtures -----------------------------------------------------
# A tiny fake warehouse: revenue dashboard <- daily_metrics <- dbt model <- raw_sales.
# Two versions of raw_sales exist so you can simulate a schema-drift incident
# by switching which one "current" points to (see MOCK_CURRENT_VERSION below).

_MOCK_ASSETS = {
    "urn:dataset:raw_sales": {
        "urn": "urn:dataset:raw_sales",
        "upstream": [],
        "downstream": ["urn:dataset:daily_metrics"],
        "schema_v1": ["customer_id:int", "amount:float", "created_at:timestamp"],
        "schema_v2": ["customer_uuid:string", "amount:float", "created_at:timestamp"],  # renamed column
        "model_logic_hash_v1": "hash_a1",
        "model_logic_hash_v2": "hash_a1",  # logic unchanged in this scenario
        "last_run_status": "success",
    },
    "urn:dataset:daily_metrics": {
        "urn": "urn:dataset:daily_metrics",
        "upstream": ["urn:dataset:raw_sales"],
        "downstream": ["urn:dashboard:revenue_dashboard"],
        "schema_v1": ["region:string", "total_revenue:float"],
        "schema_v2": ["region:string", "total_revenue:float"],
        "model_logic_hash_v1": "hash_b1",
        "model_logic_hash_v2": "hash_b1",
        "last_run_status": "success",
    },
    "urn:dashboard:revenue_dashboard": {
        "urn": "urn:dashboard:revenue_dashboard",
        "upstream": ["urn:dataset:daily_metrics"],
        "downstream": [],
        "schema_v1": [],
        "schema_v2": [],
        "model_logic_hash_v1": None,
        "model_logic_hash_v2": None,
        "last_run_status": "success",
    },
    # A second, structurally analogous dashboard. Used to demonstrate the
    # legitimate clean-reuse path: a *similar* incident on a *different*
    # asset, where nothing about THIS asset's state has changed, so
    # reuse is safe — as opposed to the same asset recurring, which our
    # design correctly treats as a contradiction (see reflection.py).
    "urn:dashboard:regional_revenue_dashboard": {
        "urn": "urn:dashboard:regional_revenue_dashboard",
        "upstream": ["urn:dataset:daily_metrics"],
        "downstream": [],
        "schema_v1": [],
        "schema_v2": [],
        "model_logic_hash_v1": None,
        "model_logic_hash_v2": None,
        "last_run_status": "success",
    },
}

# Toggle this between "v1" and "v2" to simulate the schema drift happening.
MOCK_CURRENT_VERSION = "v2"

# Where promoted lessons get written in mock mode, so you can inspect them.
_MOCK_PROMOTED_LESSONS: dict[str, list[dict]] = {}


class DataHubClient:
    def __init__(self):
        self.mock = config.MOCK_MODE
        if self.mock:
            log.info("DataHubClient running in MOCK_MODE — no live DataHub calls will be made")
        else:
            # TODO: initialize real client, e.g. DataHub's Python SDK or a
            # GraphQL client pointed at config.DATAHUB_GMS_URL with
            # config.DATAHUB_TOKEN. Check current DataHub docs for the
            # exact client class/auth pattern before wiring this up.
            raise NotImplementedError(
                "Real DataHub client not yet implemented. "
                "Set CORTEX_MOCK_MODE=true while developing, or implement "
                "this branch once you're testing against the Cloud trial."
            )

    def get_asset_snapshot(self, urn: str) -> AssetSnapshot:
        """Fetch current structural state of an asset for diffing/storage."""
        if self.mock:
            asset = _MOCK_ASSETS.get(urn)
            if asset is None:
                log.warning(f"Mock: unknown urn '{urn}', returning empty snapshot")
                return AssetSnapshot(asset_urn=urn)

            version = MOCK_CURRENT_VERSION
            snapshot = AssetSnapshot(
                asset_urn=urn,
                upstream_urns=asset["upstream"],
                downstream_urns=asset["downstream"],
                schema_fields=sorted(asset[f"schema_{version}"]),
                model_logic_hash=asset[f"model_logic_hash_{version}"],
                last_run_status=asset["last_run_status"],
            )
            log.debug(f"Mock snapshot for {urn} (version={version}): {snapshot}")
            return snapshot

        raise NotImplementedError  # TODO: real GraphQL query

    def write_lesson(self, asset_urn: str, lesson: dict) -> None:
        """
        Write (or update) a generalized, promoted lesson attached to an
        asset. This is Reflect/Promote's write target — NOT where raw
        experiences go (those live in episodic memory unconditionally).
        """
        if self.mock:
            _MOCK_PROMOTED_LESSONS.setdefault(asset_urn, []).append(lesson)
            log.info(f"[MOCK WRITE] Promoted lesson to DataHub asset {asset_urn}: {lesson}")
            return

        raise NotImplementedError  # TODO: real custom-aspect write via DataHub API

    def get_promoted_lessons(self, asset_urn: str) -> list[dict]:
        if self.mock:
            return _MOCK_PROMOTED_LESSONS.get(asset_urn, [])
        raise NotImplementedError
