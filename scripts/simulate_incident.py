"""
Simulates a freshness incident by directly setting a stale last_modified
timestamp on a real dataset in your DataHub instance, using the SDK's
confirmed-available Dataset.set_last_modified() method.

We're using freshness rather than schema mutation deliberately: the
newer datahub.sdk.Dataset class has no set_schema()/add_field() method
(confirmed by inspecting dir(Dataset) directly against your installed
SDK, rather than guessing) — but set_last_modified() DOES exist and
maps directly onto the freshness incident type + procedures/freshness.yaml
we already built. This also means we stay on showcase-ecommerce, which
is already loaded and working, instead of chasing nyc-taxi ingestion.

Usage:
    python scripts/simulate_incident.py break     # set a stale timestamp
    python scripts/simulate_incident.py restore   # set it back to "now"
"""
import sys
from datetime import datetime, timedelta, timezone

from datahub.sdk import DataHubClient

# The upstream Snowflake source — this is where the actual staleness gets
# planted. The PowerBI dashboard (urn below, for reference) is the
# downstream symptom Cortex's incident.trigger_asset_urn would point at;
# investigation walks upstream from there to find this.
TARGET_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)"

# For reference — the downstream PowerBI table an incident would actually
# trigger on. Not touched by this script, just noted here for graph.py's
# Incident.trigger_asset_urn when you wire the real demo.
DOWNSTREAM_DASHBOARD_URN = "urn:li:dataset:(urn:li:dataPlatform:powerbi,b2fd91.datahub_order_entries.ORDER_DETAILS,PROD)"

# How stale to make it look for the "break" — tune this for the demo;
# it should be old enough to obviously look wrong.
STALE_HOURS = 72


def apply_break(client: DataHubClient, urn: str):
    dataset = client.entities.get(urn)
    stale_time = datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)
    print(f"Current last_modified: {dataset.last_modified}")
    dataset.set_last_modified(stale_time)
    client.entities.upsert(dataset)
    print(f"Set last_modified to {stale_time} ({STALE_HOURS}h stale) — incident planted")


def apply_restore(client: DataHubClient, urn: str):
    dataset = client.entities.get(urn)
    now = datetime.now(timezone.utc)
    dataset.set_last_modified(now)
    client.entities.upsert(dataset)
    print(f"Set last_modified to {now} — restored to fresh")


def main():
    client = DataHubClient.from_env()
    mode = sys.argv[1] if len(sys.argv) > 1 else "break"

    if mode == "break":
        apply_break(client, TARGET_URN)
    elif mode == "restore":
        apply_restore(client, TARGET_URN)
    else:
        print("Usage: python scripts/simulate_incident.py [break|restore]")


if __name__ == "__main__":
    main()
