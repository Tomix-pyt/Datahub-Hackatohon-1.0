"""
Wraps DataHub. In MOCK_MODE (default), returns canned fixture data so
you can develop and debug graph.py without a live DataHub instance.
Flip CORTEX_MOCK_MODE=false and fill in DATAHUB_GMS_URL / DATAHUB_TOKEN
in .env once you're testing against the real Cloud trial.

Real implementation is intentionally left as clearly-marked TODOs —
don't guess at DataHub's exact GraphQL schema from memory; check the
live docs/schema once you're actually wired up.
"""
from cortex import config
from cortex.models import AssetSnapshot

log = config.get_logger("cortex.memory_semantic")

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
            from datahub.sdk import DataHubClient as RealDataHubClient
            self.client = RealDataHubClient.from_env()
            log.info("DataHubClient connected to live DataHub instance")

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

        # Real path — uses only methods confirmed to exist on the
        # installed SDK (dataset.upstreams, dataset.last_modified,
        # dataset.schema). Downstream lookup isn't wired yet — that
        # needs the SDK's lineage_client, which hasn't been verified
        # against your installed version, so it's left as an explicit
        # TODO rather than guessed.
        dataset = self.client.entities.get(urn)

        upstream_urns = []
        if dataset.upstreams and dataset.upstreams.upstreams:
            upstream_urns = [u.dataset for u in dataset.upstreams.upstreams]

        # dataset.schema returns SchemaField objects — exact attribute
        # names for field name/type aren't confirmed yet, so fall back
        # to str() per field if the expected attributes aren't there.
        schema_fields = []
        try:
            for field in (dataset.schema or []):
                schema_fields.append(f"{field.field_path}:{field.native_type}")
        except AttributeError:
            log.warning(
                "SchemaField attribute names not confirmed against this SDK "
                "version — falling back to str() representation. Run "
                "`dir(dataset.schema[0])` to find the real attribute names "
                "and tighten this up."
            )
            schema_fields = [str(f) for f in (dataset.schema or [])]

        snapshot = AssetSnapshot(
            asset_urn=urn,
            upstream_urns=sorted(upstream_urns),
            downstream_urns=[],  # TODO: wire via lineage_client once its API is confirmed
            schema_fields=sorted(schema_fields),
            model_logic_hash=None,  # TODO: not available via this SDK path yet
            last_run_status=str(dataset.last_modified),
        )
        log.info(f"Live snapshot for {urn}: {len(upstream_urns)} upstream, last_modified={dataset.last_modified}")
        return snapshot

    def write_lesson(self, asset_urn: str, lesson: dict) -> None:
        """
        Write (or update) a generalized, promoted lesson attached to an
        asset. Uses DataHub's native Document entity (AI-agent-facing
        context, hidden from normal search via show_in_global_context=False)
        rather than a bespoke custom aspect — this is a stronger DataHub
        integration story since it's DataHub's own built-in primitive for
        exactly this purpose.
        """
        if self.mock:
            _MOCK_PROMOTED_LESSONS.setdefault(asset_urn, []).append(lesson)
            log.info(f"[MOCK WRITE] Promoted lesson to DataHub asset {asset_urn}: {lesson}")
            return

        from datahub.sdk import Document

        doc = Document.create_document(
            id=f"cortex-lesson-{lesson.get('source_experience_ids', ['unknown'])[0]}",
            title=f"Cortex lesson: {lesson.get('lesson', '')[:60]}",
            text=f"{lesson.get('lesson', '')}\n\nFix: {lesson.get('fix', '')}\n"
                 f"Observed: {lesson.get('observed_count')}x, success rate {lesson.get('success_rate')}",
            related_assets=[asset_urn],
            show_in_global_context=False,
        )
        self.client.entities.upsert(doc)
        log.info(f"Wrote promoted lesson as Document, attached to {asset_urn}")

    def get_promoted_lessons(self, asset_urn: str) -> list[dict]:
        if self.mock:
            return _MOCK_PROMOTED_LESSONS.get(asset_urn, [])
        raise NotImplementedError  # TODO: query Documents related to this asset
