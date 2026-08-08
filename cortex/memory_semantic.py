"""DataHub integration and the small mock warehouse used by the test/demo.

The real path uses the DataHub SDK's explicit lineage client rather than
assuming that a Dataset entity exposes a ``downstreams`` attribute.  This is
important because lineage is a separate SDK capability in DataHub 1.6.x.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from cortex import config
from cortex.models import AssetSnapshot

log = config.get_logger("cortex.memory_semantic")


_MOCK_ASSETS = {
    "urn:dataset:raw_sales": {
        "urn": "urn:dataset:raw_sales",
        "upstream": [],
        "downstream": ["urn:dataset:daily_metrics"],
        "schema_v1": ["customer_id:int", "amount:float", "created_at:timestamp"],
        "schema_v2": ["customer_uuid:string", "amount:float", "created_at:timestamp"],
        "model_logic_hash_v1": "hash_a1",
        "model_logic_hash_v2": "hash_a1",
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

_MOCK_PROMOTED_LESSONS: dict[str, list[dict]] = {}


def _schema_fields(dataset) -> list[str]:
    """Extract schema fields defensively across SDK object shapes."""
    fields = getattr(dataset, "schema", None) or []
    result: list[str] = []
    for field in fields:
        path = getattr(field, "field_path", None) or getattr(field, "fieldPath", None)
        native = getattr(field, "native_type", None) or getattr(field, "nativeDataType", None)
        if path is not None:
            result.append(f"{path}:{native or 'UNKNOWN'}")
        else:
            result.append(str(field))
    return sorted(result)


def _lineage_urns(client, urn: str, direction: str) -> list[str]:
    """Use the SDK's lineage client; return an empty list on unavailable lineage."""
    try:
        results = client.lineage.get_lineage(
            source_urn=urn,
            direction=direction,
            max_hops=1,
            count=500,
        )
        return sorted({item.urn for item in results if getattr(item, "urn", None)})
    except Exception as exc:
        log.warning("Unable to read %s lineage for %s: %s", direction, urn, exc)
        return []


class DataHubClient:
    def __init__(self):
        self.mock = config.MOCK_MODE
        if self.mock:
            log.info("DataHubClient running in MOCK_MODE — no live DataHub calls")
            self.client = None
        else:
            from datahub.sdk import DataHubClient as RealDataHubClient

            # Explicit construction is easier to debug than relying on a
            # hidden ~/.datahubenv file and supports the names in config.py.
            self.client = RealDataHubClient(
                server=config.DATAHUB_GMS_URL,
                token=config.DATAHUB_TOKEN or None,
            )
            self.client.test_connection()
            log.info("DataHubClient connected to %s", config.DATAHUB_GMS_URL)

    def get_asset_snapshot(self, urn: str) -> AssetSnapshot:
        """Fetch the current structural state of an asset."""
        if self.mock:
            asset = _MOCK_ASSETS.get(urn)
            if asset is None:
                log.warning("Mock: unknown urn '%s', returning empty snapshot", urn)
                return AssetSnapshot(asset_urn=urn)

            version = os.getenv("CORTEX_MOCK_VERSION", config.MOCK_CURRENT_VERSION)
            if version not in {"v1", "v2"}:
                raise ValueError(f"Unsupported CORTEX_MOCK_VERSION={version!r}; use v1 or v2")

            upstream_schemas = {
                upstream: sorted(_MOCK_ASSETS[upstream][f"schema_{version}"])
                for upstream in asset["upstream"]
                if upstream in _MOCK_ASSETS
            }
            return AssetSnapshot(
                asset_urn=urn,
                upstream_urns=sorted(asset["upstream"]),
                downstream_urns=sorted(asset["downstream"]),
                schema_fields=sorted(asset[f"schema_{version}"]),
                upstream_schemas=upstream_schemas,
                last_modified="mock-static",
                freshness_age_hours=0.0,
                last_run_status=asset["last_run_status"],
            )

        dataset = self.client.entities.get(urn)
        if dataset is None:
            raise ValueError(f"DataHub returned no dataset for URN: {urn}")

        upstream_urns = _lineage_urns(self.client, urn, "upstream")
        downstream_urns = _lineage_urns(self.client, urn, "downstream")
        schema_fields = _schema_fields(dataset)

        # DataHub's Dataset.last_modified is a metadata modification timestamp,
        # not a guaranteed pipeline execution timestamp. We use it as a
        # freshness proxy for this MVP and keep last_run_status separate rather
        # than pretending the timestamp itself is a run status.
        last_modified_dt = getattr(dataset, "last_modified", None)
        freshness_age = None
        last_modified_str = None
        if last_modified_dt:
            if isinstance(last_modified_dt, datetime):
                if last_modified_dt.tzinfo is None:
                    last_modified_dt = last_modified_dt.replace(tzinfo=timezone.utc)
                last_modified_str = last_modified_dt.isoformat()
                freshness_age = round(
                    (datetime.now(timezone.utc) - last_modified_dt).total_seconds() / 3600.0,
                    2,
                )
            else:
                last_modified_str = str(last_modified_dt)

        upstream_schemas: dict[str, list[str]] = {}
        for upstream in upstream_urns:
            try:
                upstream_entity = self.client.entities.get(upstream)
                if upstream_entity is not None:
                    upstream_schemas[upstream] = _schema_fields(upstream_entity)
            except Exception as exc:
                log.warning("Unable to snapshot upstream schema %s: %s", upstream, exc)

        snapshot = AssetSnapshot(
            asset_urn=urn,
            upstream_urns=upstream_urns,
            downstream_urns=downstream_urns,
            schema_fields=schema_fields,
            last_modified=last_modified_str,
            upstream_schemas=upstream_schemas,
            freshness_age_hours=freshness_age,
            last_run_status=None,
        )
        log.info(
            "Live snapshot for %s: %d upstream, %d downstream, %d fields, age=%sh",
            urn,
            len(upstream_urns),
            len(downstream_urns),
            len(schema_fields),
            freshness_age,
        )
        return snapshot

    def write_lesson(self, asset_urn: str, lesson: dict) -> None:
        """Write a generalized Cortex lesson as a DataHub Document."""
        if self.mock:
            _MOCK_PROMOTED_LESSONS.setdefault(asset_urn, []).append(lesson)
            log.info("[MOCK WRITE] Promoted lesson to %s: %s", asset_urn, lesson)
            return

        from datahub.sdk import Document

        lesson_id = lesson["lesson_id"]
        doc = Document.create_document(
            id=lesson_id,
            title=f"Cortex lesson: {lesson.get('title', 'validated incident pattern')}",
            text=(
                f"{lesson.get('lesson', '')}\n\n"
                f"Fix: {lesson.get('fix', '')}\n"
                f"Observed: {lesson.get('observed_count', 1)}x\n"
                f"Success rate: {lesson.get('success_rate', 'unknown')}\n"
                f"Last validated: {lesson.get('last_validated', 'unknown')}"
            ),
            related_assets=[asset_urn],
            show_in_global_context=False,
        )
        self.client.entities.upsert(doc)
        log.info("Wrote promoted lesson %s to DataHub asset %s", lesson_id, asset_urn)
