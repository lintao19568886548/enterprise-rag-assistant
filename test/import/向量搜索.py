import numpy as np
from pymilvus import MilvusClient

from app.lm.embedding_utils import generate_embeddings, get_bge_m3_ef

client = MilvusClient(
    uri="http://192.168.100.101:19530",
    token="root:123456"
)

#使用BGE_M3模型
model = get_bge_m3_ef()
# 构造查询向量
query_result = model.encode_queries(["什么是人工智能？"])

# 提取稠密向量：需关闭FP16
# query_vectors = query_result["dense"]

# 匹配半精度推理
query_vectors = [np.array(vec, dtype=np.float32).tolist() for vec in query_result["dense"]]

# 按主键查询
res = client.query(
    collection_name="demo_collection",
    ids=[0, 1, 2],
    output_fields=["text", "subject"],
)

for item in res:
    print(f"ID: {item['id']}, 文本: {item['text']}")