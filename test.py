# from cortex.memory_semantic import DataHubClient

from cortex.memory_semantic import DataHubClient


TARGET_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)"
TEST_URN = "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.promotions,PROD)"
TEST_URN2 = 'urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.countries,PROD)'

c = DataHubClient()
snap = c.get_asset_snapshot(TARGET_URN)
print(snap)
# from datahub.sdk import DataHubClient
# client = DataHubClient.from_env()
# doc = client.entities.get(TARGET_URN)
# print(doc)
