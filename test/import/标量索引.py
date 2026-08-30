from pymilvus import MilvusClient

client = MilvusClient(
    uri="http://192.168.100.101:19530",
    token="root:123456"
)

# 为标量字段创建索引
index_params = client.prepare_index_params()
index_params.add_index(
    field_name="category",          # 标量字段
    index_type="INVERTED"           # 倒排索引
)
client.create_index(
    collection_name="articles",
    index_params=index_params
)