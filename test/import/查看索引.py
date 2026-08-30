# 连接 Milvus
from pymilvus import MilvusClient

client = MilvusClient(
    uri="http://192.168.100.101:19530",
    token="root:123456"
)

# 列出所有索引
indexes = client.list_indexes(collection_name="my_collection")
print(f"索引列表：{indexes}")
# 输出：索引列表：['vector', 'category']

# 查看某个索引的详情
info = client.describe_index(
    collection_name="my_collection",
    index_name="vector"
)
print(f"索引类型：{info['index_type']}")
print(f"已索引行数：{info['indexed_rows']} / {info['total_rows']}")

# 删除索引（需要更换索引类型时使用）
client.drop_index(
    collection_name="my_collection",
    index_name="vector"
)