"""
Quick standalone check that the live DataHub connection works.
Run with: python3 check_snapshot.py
"""
from cortex.memory_semantic import DataHubClient

TARGET_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)"

c = DataHubClient()
snap = c.get_asset_snapshot(TARGET_URN)
print(snap)
