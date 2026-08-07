"""
Wraps DataHub. In MOCK_MODE (default), returns canned fixture data so
you can develop and debug graph.py without a live DataHub instance.
Flip CORTEX_MOCK_MODE=false and fill in DATAHUB_GMS_URL / DATAHUB_TOKEN
in .env once you're testing against the real Cloud trial.

Real implementation is intentionally left as clearly-marked TODOs —
don't guess at DataHub's exact GraphQL schema from memory; check the
live docs/schema once you're actually wired up.
"""
from datetime import datetime, timezone
from typing import Optional

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

        # Real path — uses confirmed SDK methods (dataset.upstreams, dataset.last_modified, dataset.schema)
        dataset = self.client.entities.get(urn)

        # Extract upstreams
        upstream_urns = []
        if dataset.upstreams and dataset.upstreams.upstreams:
            upstream_urns = [u.dataset for u in dataset.upstreams.upstreams]

        # Extract downstreams
        downstream_urns = []
        # Extract schema fields
        schema_fields = []
        try:
            for field in dataset.schema or []:
                schema_fields.append(f"{field.field_path}:{field.native_type}")
        except AttributeError:
            log.warning(
                "SchemaField attribute names not confirmed against this SDK "
                "version — falling back to str() representation."
            )
            schema_fields = [str(f) for f in (dataset.schema or [])]

        # Calculate freshness age relative to current UTC time
        last_modified_dt = dataset.last_modified
        freshness_age = None
        last_modified_str = None

        if last_modified_dt:
            if isinstance(last_modified_dt, datetime):
                if last_modified_dt.tzinfo is None:
                    last_modified_dt = last_modified_dt.replace(
                        tzinfo=timezone.utc
                    )
                now_utc = datetime.now(timezone.utc)
                delta = now_utc - last_modified_dt
                freshness_age = round(delta.total_seconds() / 3600.0, 2)
                last_modified_str = last_modified_dt.isoformat()
            else:
                last_modified_str = str(last_modified_dt)

        snapshot = AssetSnapshot(
            asset_urn=urn,
            upstream_urns=sorted(upstream_urns),
            downstream_urns=sorted(downstream_urns),
            schema_fields=sorted(schema_fields),
            last_modified=last_modified_str,
            freshness_age_hours=freshness_age,
            last_run_status=str(dataset.last_modified),
        )

        log.info(
            f"Live snapshot for {urn}: {len(upstream_urns)} upstream, "
            f"last_modified={dataset.last_modified}, age={freshness_age}h"        )
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
