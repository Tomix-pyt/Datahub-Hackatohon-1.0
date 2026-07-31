from datahub.sdk import DataHubClient
client = DataHubClient.from_env() 
dataset_urn = "urn:li:dataset:(urn:li:dataPlatform:s3,b2fd91.demo-data-bucket/order_entry/customers,PROD)"
dataset = client.entities.get(dataset_urn) 
if dataset.schema:
    print("\n--- Dataset Schema ---")
    for field in dataset.schema:
        # field_path contains the name/path of your S3 column
        name = field.field_path
        print(name)
        # Access optional details if available (using getattr as a safe fallback)
        data_type = getattr(field, "Type", "Unknown Type")
        description = getattr(field, "Description", "No description")
        
        print(f"Column: {name} | Type: {data_type}  Desc: {description}")
else:
    print("\nNo schema fields found for this dataset.")


