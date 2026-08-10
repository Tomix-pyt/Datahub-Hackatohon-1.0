#!/usr/bin/env python
"""
Cortex Incident Simulator

Simulates DataHub metadata changes to trigger Cortex investigations.

Usage:
    python scripts/simulate_incident.py break rename      # customer_id → customer_uuid
    python scripts/simulate_incident.py break type        # order_total FLOAT → VARCHAR
    python scripts/simulate_incident.py break remove      # customer_id removed entirely
    python scripts/simulate_incident.py break add         # add new_analytics_flag
    python scripts/simulate_incident.py break freshness   # Set last_modified to 60+ hours ago
    python scripts/simulate_incident.py restore           # Restore to healthy baseline
"""

import sys
import os
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Any

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

# ============================================================
# CONFIGURATION
# ============================================================

TARGET_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)"

# Staleness threshold for freshness incidents (hours)
STALENESS_HOURS = 60

# ============================================================
# BASELINE SCHEMA (55 columns)
# ============================================================

FULL_SCHEMA_FIELDS: List[Tuple[str, str, Any]] = [
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
    ("customer_id", "NUMBER(38,0)", NumberTypeClass()),
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

# ============================================================
# DRIFT TRANSFORMATIONS
# ============================================================

def apply_drift_rename(fields: List[Tuple]) -> List[Tuple]:
    """DRIFT 1: Rename customer_id → customer_uuid"""
    result = []
    for name, native_type, type_cls in fields:
        if name == "customer_id":
            result.append(("customer_uuid", "VARCHAR(16777216)", StringTypeClass()))
        else:
            result.append((name, native_type, type_cls))
    return result


def apply_drift_type_mismatch(fields: List[Tuple]) -> List[Tuple]:
    """DRIFT 2: Change order_total FLOAT → VARCHAR"""
    result = []
    for name, native_type, type_cls in fields:
        if name == "order_total":
            result.append(("order_total", "VARCHAR(16777216)", StringTypeClass()))
        else:
            result.append((name, native_type, type_cls))
    return result


def apply_drift_remove(fields: List[Tuple]) -> List[Tuple]:
    """DRIFT 3: Remove customer_id entirely"""
    return [
        (name, native_type, type_cls)
        for name, native_type, type_cls in fields
        if name != "customer_id"
    ]


def apply_drift_add(fields: List[Tuple]) -> List[Tuple]:
    """DRIFT 4: Add a new column"""
    result = list(fields)
    result.append(("new_analytics_flag", "BOOLEAN", StringTypeClass()))
    return result


DRIFT_MODES = {
    "rename": {
        "label": "DRIFT: 'customer_id' → 'customer_uuid' (Column Rename)",
        "transform": apply_drift_rename,
    },
    "type": {
        "label": "DRIFT: 'order_total' FLOAT → VARCHAR (Type Mismatch)",
        "transform": apply_drift_type_mismatch,
    },
    "remove": {
        "label": "DRIFT: 'customer_id' removed (Column Deletion)",
        "transform": apply_drift_remove,
    },
    "add": {
        "label": "DRIFT: Added 'new_analytics_flag' (Column Addition)",
        "transform": apply_drift_add,
    },
}

# ============================================================
# DATAHUB EMITTER
# ============================================================

def get_emitter() -> DataHubRestEmitter:
    gms_url = os.getenv("DATAHUB_GMS_URL", "http://localhost:8080")
    token = os.getenv("DATAHUB_GMS_TOKEN", None)
    return DataHubRestEmitter(gms_server=gms_url, token=token)


def emit_schema_aspect(target_urn: str, fields: List[Tuple], label: str = "HEALTHY"):
    """Emits schemaMetadata aspect to DataHub."""
    emitter = get_emitter()
    
    schema_fields = []
    for name, native_type, type_cls in fields:
        schema_fields.append(
            SchemaFieldClass(
                fieldPath=name,
                type=SchemaFieldDataTypeClass(type=type_cls),   # ← NO parentheses!
                nativeDataType=native_type,
                description=f"Field {name}",
            )
        )

    schema_aspect = SchemaMetadataClass(
        schemaName="analytics.order_details",
        platform="urn:li:dataPlatform:snowflake",
        version=0,
        hash="",
        platformSchema=OtherSchemaClass(rawSchema=""),
        fields=schema_fields,
    )

    mcp = MetadataChangeProposalWrapper(
        entityUrn=target_urn,
        aspect=schema_aspect,
    )
    emitter.emit(mcp)
    print(f"✅ Emitted SchemaMetadata: {label}")


def emit_operation_aspect(target_urn: str, timestamp_ms: int):
    """Emits an operation aspect to update last_modified timestamp."""
    emitter = get_emitter()

    operation_aspect = OperationClass(
        timestampMillis=timestamp_ms,
        operationType=OperationTypeClass.UPDATE,
        actor="urn:li:corpuser:ingestion",
        lastUpdatedTimestamp=timestamp_ms,
    )

    mcp = MetadataChangeProposalWrapper(
        entityUrn=target_urn,
        aspect=operation_aspect,
    )
    emitter.emit(mcp)

    dt_str = datetime.fromtimestamp(
        timestamp_ms / 1000, tz=timezone.utc
    ).isoformat()
    print(f"✅ Emitted Operation aspect: timestamp={dt_str}")

# ============================================================
# COMMAND HANDLERS
# ============================================================

def handle_break_schema(drift_type: str):
    """Break schema with the specified drift type."""
    if drift_type not in DRIFT_MODES:
        print(f"❌ Unknown drift type: {drift_type}")
        print(f"   Available: {', '.join(DRIFT_MODES.keys())}")
        return
    
    mode = DRIFT_MODES[drift_type]
    drifted_fields = mode["transform"](FULL_SCHEMA_FIELDS)
    label = mode["label"]
    print(f"⚡ {label}")
    emit_schema_aspect(TARGET_URN, drifted_fields, label)


def handle_break_freshness():
    """Break freshness by setting last_modified to 60+ hours ago."""
    print(f"⚡ Simulating Data Freshness (Stale Pipeline) incident...")
    print(f"   Setting last_modified to {STALENESS_HOURS} hours ago")
    # First ensure schema is baseline healthy
    emit_schema_aspect(TARGET_URN, FULL_SCHEMA_FIELDS, "HEALTHY (55 columns)")
    stale_time_ms = int((datetime.now(timezone.utc) - timedelta(hours=STALENESS_HOURS)).timestamp() * 1000)
    emit_operation_aspect(TARGET_URN, stale_time_ms)
    print(f"✅ Freshness broken: data is now {STALENESS_HOURS}+ hours stale")


def handle_restore():
    """Restore to healthy baseline."""
    print("🧹 Restoring asset to healthy state...")
    emit_schema_aspect(TARGET_URN, FULL_SCHEMA_FIELDS, "HEALTHY (55 columns)")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    emit_operation_aspect(TARGET_URN, now_ms)
    print("✅ Asset fully restored to healthy baseline.")

# ============================================================
# MAIN
# ============================================================

def print_usage():
    print("""
Cortex Incident Simulator

Usage:
    python scripts/simulate_incident.py break <drift_type>   # Break schema
    python scripts/simulate_incident.py break freshness     # Break freshness
    python scripts/simulate_incident.py restore             # Restore to healthy

Schema Drift Types:
    rename    : customer_id → customer_uuid (Column Rename)
    type      : order_total FLOAT → VARCHAR (Type Mismatch)
    remove    : customer_id removed entirely (Column Deletion)
    add       : Add new_analytics_flag (Column Addition)

Examples:
    python scripts/simulate_incident.py break rename
    python scripts/simulate_incident.py break type
    python scripts/simulate_incident.py break freshness
    python scripts/simulate_incident.py restore
""")


def main():
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "break":
        if len(sys.argv) < 3:
            print("❌ Missing sub-command: specify 'rename', 'type', 'remove', 'add', or 'freshness'")
            print_usage()
            sys.exit(1)
        
        sub_cmd = sys.argv[2].lower()
        
        if sub_cmd == "freshness":
            handle_break_freshness()
        else:
            handle_break_schema(sub_cmd)

    elif cmd == "restore":
        handle_restore()

    else:
        print(f"❌ Unknown command: {cmd}")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()