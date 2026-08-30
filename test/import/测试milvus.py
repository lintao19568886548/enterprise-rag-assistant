from pymilvus import MilvusClient, DataType

client = MilvusClient(
    uri="http://192.168.100.101:19530",
    token="root:Milvus"          # 默认用户名:密码
)


# 第一步：创建 Schema
schema = MilvusClient.create_schema(
    auto_id=False,           # 不自动生成主键
    enable_dynamic_field=True # 允许动态字段
)

# 第二步：添加字段
schema.add_field(
    field_name="id",
    datatype=DataType.INT64,
    is_primary=True,         # 设为主键
    description="主键ID"
)
schema.add_field(
    field_name="vector",
    datatype=DataType.FLOAT_VECTOR,
    dim=768,                 # 向量维度
    description="文本嵌入向量"
)
schema.add_field(
    field_name="text",
    datatype=DataType.VARCHAR,
    max_length=512,          # VARCHAR 必须指定最大长度
    description="原始文本"
)
schema.add_field(
    field_name="category",
    datatype=DataType.VARCHAR,
    max_length=64,
    description="类别标签"
)

# 第三步：创建 Collection
if client.has_collection(collection_name="articles"):
    client.drop_collection(collection_name="articles")

client.create_collection(
    collection_name="articles",
    schema=schema,
    description="文章向量数据集"
)
print("自定义 Collection 创建成功！")