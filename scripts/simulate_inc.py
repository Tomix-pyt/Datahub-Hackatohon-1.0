import sys
import os
import time
from datetime import datetime, timedelta, timezone

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DataHubRestEmitter
from datahub.metadata.schema_classes import (
    SchemaMetadataClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    StringTypeClass,
    NumberTypeClass,
    OtherSchemaClass,
    OperationClass,
    OperationTypeClass,
)

TARGET_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)"

# Complete 55-column baseline schema matching upstream dbt model
FULL_SCHEMA_FIELDS = [
    ("billing_address_line1", "VARCHAR(16777216)", StringTypeClass()),
    ("billing_address_line2", "VARCHAR(16777216)", StringTypeClass()),
    ("billing_country", "VARCHAR(16777216)", StringTypeClass()),
    ("billing_region", "VARCHAR(16777216)", StringTypeClass()),
    ("billing_town_city", "VARCHAR(16777216)", StringTypeClass()),
    ("billing_zipcode", "NUMBER(38,0)", NumberTypeClass()),
    ("category_id", "NUMBER(38,0)", NumberTypeClass()),
    ("category_name", "VARCHAR(16777216)", StringTypeClass()),
    ("condition", "VARCHAR(16777216)", StringTypeClass()),
    ("cost_of_delivery", "FLOAT", NumberTypeClass()),
    ("cust_email", "VARCHAR(16777216)", StringTypeClass()),
    ("cust_first_name", "VARCHAR(16777216)", StringTypeClass()),
    ("cust_last_name", "VARCHAR(16777216)", StringTypeClass()),
    ("customer_class", "VARCHAR(16777216)", StringTypeClass()),
    ("customer_id", "NUMBER(38,0)", NumberTypeClass()),  # Target column for schema drift test
    ("delivery_status", "VARCHAR(11)", StringTypeClass()),
    ("delivery_type", "VARCHAR(16777216)", StringTypeClass()),
    ("discount_amount", "FLOAT", NumberTypeClass()),
    ("discount_percent", "FLOAT", NumberTypeClass()),
    ("dispatch_date", "VARCHAR(16777216)", StringTypeClass()),
    ("estimated_delivery", "VARCHAR(16777216)", StringTypeClass()),
    ("gift_wrap", "VARCHAR(16777216)", StringTypeClass()),
    ("line_item_id", "NUMBER(38,0)", NumberTypeClass()),
    ("line_total", "FLOAT", NumberTypeClass()),
    ("list_price", "FLOAT", NumberTypeClass()),
    ("order_date", "VARCHAR(16777216)", StringTypeClass()),
    ("order_id", "NUMBER(38,0)", NumberTypeClass()),
    ("order_mode", "VARCHAR(16777216)", StringTypeClass()),
    ("order_status", "NUMBER(38,0)", NumberTypeClass()),
    ("order_total", "FLOAT", NumberTypeClass()),
    ("payment_method_code", "VARCHAR(16777216)", StringTypeClass()),
    ("phone_number", "VARCHAR(16777216)", StringTypeClass()),
    ("product_description", "VARCHAR(16777216)", StringTypeClass()),
    ("product_id", "NUMBER(38,0)", NumberTypeClass()),
    ("product_name", "VARCHAR(16777216)", StringTypeClass()),
    ("product_status", "VARCHAR(16777216)", StringTypeClass()),
    ("promotion_description", "VARCHAR(16777216)", StringTypeClass()),
    ("promotion_id", "NUMBER(38,0)", NumberTypeClass()),
    ("promotion_name", "VARCHAR(16777216)", StringTypeClass()),
    ("quantity", "NUMBER(38,0)", NumberTypeClass()),
    ("quantity_on_hand", "NUMBER(38,0)", NumberTypeClass()),
    ("return_date", "VARCHAR(16777216)", StringTypeClass()),
    ("return_status", "VARCHAR(12)", StringTypeClass()),
    ("shipping_address_line1", "VARCHAR(16777216)", StringTypeClass()),
    ("shipping_address_line2", "VARCHAR(16777216)", StringTypeClass()),
    ("shipping_country", "VARCHAR(16777216)", StringTypeClass()),
    ("shipping_region", "VARCHAR(16777216)", StringTypeClass()),
    ("shipping_town_city", "VARCHAR(16777216)", StringTypeClass()),
    ("shipping_zipcode", "NUMBER(38,0)", NumberTypeClass()),
    ("stock_status", "VARCHAR(11)", StringTypeClass()),
    ("unit_price", "FLOAT", NumberTypeClass()),
    ("updated_at", "TIMESTAMP_LTZ", StringTypeClass()),
    ("wait_till_complete_yn", "VARCHAR(16777216)", StringTypeClass()),
    ("warehouse_id", "NUMBER(38,0)", NumberTypeClass()),
    ("warehouse_name", "VARCHAR(16777216)", StringTypeClass()),
]


def get_emitter() -> DataHubRestEmitter:
    gms_url = os.getenv("DATAHUB_GMS_URL", "http://localhost:8080")
    token = os.getenv("DATAHUB_GMS_TOKEN", None)
    return DataHubRestEmitter(gms_server=gms_url, token=token)


def emit_schema_aspect(target_urn: str, drifted: bool = False):
    """Emits complete schemaMetadata aspect to DataHub."""
    emitter = get_emitter()
    fields = []

    for name, native_type, type_cls in FULL_SCHEMA_FIELDS:
        # Mutate customer_id if drifted=True
        if name == "customer_id" and drifted:
            col_name = "customer_uuid"
            col_native = "VARCHAR"
            col_cls = StringTypeClass()
        else:
            col_name = name
            col_native = native_type
            col_cls = type_cls  # <--- Removed parentheses since type_cls is already instantiated

        fields.append(
            SchemaFieldClass(
                fieldPath=col_name,
                type=SchemaFieldDataTypeClass(type=col_cls),
                nativeDataType=col_native,
                description=f"Field {col_name}",
            )
        )

    schema_aspect = SchemaMetadataClass(
        schemaName="analytics.order_details",
        platform="urn:li:dataPlatform:snowflake",
        version=0,
        hash="",
        platformSchema=OtherSchemaClass(rawSchema=""),
        fields=fields,
    )

    mcp = MetadataChangeProposalWrapper(
        entityUrn=target_urn,
        aspect=schema_aspect,
    )
    emitter.emit(mcp)

    status = "DRIFTED ('customer_id' -> 'customer_uuid')" if drifted else "HEALTHY (55 columns)"
    print(f"✅ Emitted SchemaMetadata aspect to DataHub: {status}")

from datetime import datetime, timezone
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import OperationClass, OperationTypeClass


def emit_operation_aspect(target_urn: str, timestamp_ms: int):
    """Emits an operation aspect to update last_modified execution timestamp."""
    emitter = get_emitter()

    # Pass lastUpdatedTimestamp alongside timestampMillis to satisfy Avro schema
    operation_aspect = OperationClass(
        timestampMillis=timestamp_ms,  # type: ignore[call-arg]
        operationType=OperationTypeClass.UPDATE,  # type: ignore[call-arg]
        actor="urn:li:corpuser:ingestion",  # type: ignore[call-arg]
        lastUpdatedTimestamp=timestamp_ms,  # type: ignore[call-arg]
    )

    mcp = MetadataChangeProposalWrapper(
        entityUrn=target_urn,
        aspect=operation_aspect,
    )
    emitter.emit(mcp)

    dt_str = datetime.fromtimestamp(
        timestamp_ms / 1000, tz=timezone.utc
    ).isoformat()
    print(f"✅ Emitted Operation aspect to DataHub: timestamp={dt_str}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/simulate_inc.py [break schema | break freshness | restore]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    sub_cmd = sys.argv[2].lower() if len(sys.argv) > 2 else ""

    if cmd == "break" and sub_cmd == "schema":
        print("⚡ Simulating Schema Drift incident...")
        emit_schema_aspect(TARGET_URN, drifted=True)

    elif cmd == "break" and sub_cmd == "freshness":
        print("⚡ Simulating Data Freshness (Stale Pipeline) incident...")
        # First ensure schema is baseline healthy
        emit_schema_aspect(TARGET_URN, drifted=False)
        # Set operation timestamp to 72 hours in the past
        stale_time_ms = int((datetime.now(timezone.utc) - timedelta(hours=60)).timestamp() * 1000)
        emit_operation_aspect(TARGET_URN, stale_time_ms)

    elif cmd == "restore":
        print("🧹 Restoring asset to healthy state...")
        emit_schema_aspect(TARGET_URN, drifted=False)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        emit_operation_aspect(TARGET_URN, now_ms)
        print("✅ Restored asset schema and timestamp to healthy baseline.")

    else:
        print(f"Unknown command: {' '.join(sys.argv[1:])}")
        print("Available options: break schema | break freshness | restore")


if __name__ == "__main__":
    main()