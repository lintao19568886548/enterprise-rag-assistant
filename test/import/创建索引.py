from pymilvus import MilvusClient, DataType

# 连接 Milvus
client = MilvusClient(
    uri="http://192.168.100.101:19530",
    token="root:123456"
)

# 检查集合是否存在
collection_name = "my_collection"

if not client.has_collection(collection_name):
    print(f"集合 '{collection_name}' 不存在，开始创建...")

    # 创建集合 Schema
    schema = client.create_schema(auto_id=True, enable_dynamic_field=True)

    # 添加主键字段
    schema.add_field(
        field_name="pk",
        datatype=DataType.INT64,
        is_primary=True,
        auto_id=True
    )

    # 添加向量字段
    schema.add_field(
        field_name="vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=1024  # BGE-M3 的维度
    )

    # 添加文本字段
    schema.add_field(
        field_name="text",
        datatype=DataType.VARCHAR,
        max_length=65535
    )

    # 创建集合
    client.create_collection(
        collection_name=collection_name,
        schema=schema
    )
    print(f"集合 '{collection_name}' 创建成功！")
else:
    print(f"集合 '{collection_name}' 已存在")


# 第一步：准备索引参数对象
index_params = client.prepare_index_params()

# 第二步：添加索引配置
index_params.add_index(
    field_name="vector",
    index_type="AUTOINDEX",
    metric_type="COSINE"
)

# 第三步：执行创建
client.create_index(
    collection_name=collection_name,
    index_params=index_params,
    sync=True
)

print("索引创建成功！")
