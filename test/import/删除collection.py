from pymilvus import MilvusClient

client = MilvusClient(
    uri="http://192.168.100.101:19530",
    token="root:123456"
)

# 删除整个 Collection（不可恢复）
client.drop_collection(collection_name="demo_collection")
print("Collection 已删除")