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
import re
from typing import Dict, Optional
from datahub.sdk import Document
from datahub.sdk import DataHubClient as RealDataHubClient
from cortex import config
from cortex.models import AssetSnapshot

log = config.get_logger("cortex.memory_semantic")

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
            self.client = RealDataHubClient.from_env()
            log.info("DataHubClient connected to live DataHub instance")

    def get_asset_snapshot(self, urn: str) -> AssetSnapshot:
        """Fetch current structural state of an asset for diffing/storage."""
        if self.mock:
            asset = _MOCK_ASSETS.get(urn)
            if asset is None:
                log.warning(f"Mock: unknown urn '{urn}', returning empty snapshot")
                return AssetSnapshot(asset_urn=urn)

            snapshot = AssetSnapshot(
                asset_urn=urn,
                upstream_urns=asset["upstream"],
                downstream_urns=asset["downstream"],
                schema_fields=sorted(asset[f"schema"]),
                last_run_status=asset["last_run_status"],
            )
            log.debug(f"Mock snapshot for {urn} : {snapshot}")
            return snapshot
        dataset = self.client.entities.get(urn)
        upstream_urns = []
        if dataset.upstreams and dataset.upstreams.upstreams:
            upstream_urns = [u.dataset for u in dataset.upstreams.upstreams]
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
            f"last_modified={dataset.last_modified}, age={freshness_age} Hrs"        )
        return snapshot
    def get_existing_lesson_metadata(self, asset_urn: str) -> Optional[Dict[str, int]]:
            """Queries DataHub for existing documentation on asset_urn and extracts observed_count."""
            if self.mock:
                # Mock mode: return None (or default dict) for testing
                return None

            try:
                # 1. Fetch existing aspect from DataHub (e.g. editableDatasetProperties or institutionalMemory)
                aspect = self.graph.get_aspect(
                    entity_urn=asset_urn, aspect_type="editableDatasetProperties"
                )

                if not aspect or not getattr(aspect, "description", None):
                    return None

                description = aspect.description

                # 2. Parse the 'Observed: Xx' pattern out of the markdown documentation
                # Example text: "Stats: Observed: 2x | Success Rate: 100%"
                match = re.search(r"Observed:\s*(\d+)x", description)
                if match:
                    count = int(match.group(1))
                    return {"observed_count": count}

                return None

            except Exception as e:
                log.warning(
                    f"[DATAHUB] Could not read existing metadata for {asset_urn}: {e}"
                )
                return None

    def write_lesson(self, asset_urn: str, lesson: dict | str, fix: str) -> None:
        """
        Write (or update) a generalized, promoted lesson attached to an
        asset. Uses DataHub's native Document entity (AI-agent-facing
        context, hidden from normal search via show_in_global_context=False)
        rather than a bespoke custom aspect — this is a stronger DataHub
        integration story since it's DataHub's own built-in primitive for
        exactly this purpose.
        """
        existing_meta = self.get_existing_lesson_metadata(asset_urn)
        if self.mock:
            _MOCK_PROMOTED_LESSONS.setdefault(asset_urn, []).append(lesson)
            log.info(f"[MOCK WRITE] Promoted lesson to DataHub asset {asset_urn}: {lesson}")
            return
        if isinstance(lesson, str):
                lesson_data = {
                    "root_cause": lesson,
                    "proposed_fix": fix,
                }
        elif isinstance(lesson, dict):
            lesson_data = lesson
        else:
            lesson_data = {}
        if existing_meta:
            previous_count = existing_meta.get("observed_count", 1)
            observed_count = previous_count + 1
        else:
            observed_count = 1
        success_rate = lesson_data.get("success_rate", "100%")
        doc = Document.create_document(
            id=f"cortex-lesson-{lesson_data.get('source_experience_ids', ['unknown'])[0]}",
            title=f"Cortex lesson for {lesson_data.get('root_cause', 'unknown')}",
            text=f"{lesson_data.get('root_cause', '').upper()}\n\nFix: \n {fix}\n"
                 f"Observed: {observed_count}x, success rate {success_rate}",
            related_assets=[asset_urn],
            show_in_global_context=False,
            last_modified_time= datetime.now(timezone.utc),
        )
        self.client.entities.upsert(doc)
        log.info(f"Wrote promoted lesson as Document, attached to {asset_urn}")
