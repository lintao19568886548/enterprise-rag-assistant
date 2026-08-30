from pymilvus import MilvusClient

from app.lm.embedding_utils import generate_embeddings

# Token form
client = MilvusClient(
    uri="http://192.168.100.101:19530",
    token="root:123456"
)

# 将文本转换为向量
docs = [
    "人工智能于1956年被确立为一门学科。",
    "Alan Turing 是 AI 研究领域的先驱。",
    "机器学习已被用于药物设计。",
]

#使用BGE_M3模型进行向量生成
vectors = generate_embeddings(docs)

print(f"生成了 {len(vectors)} 个向量")

# 组装数据
data = [
    {"id": i, "vector": vectors["dense"][i], "text": docs[i], "subject": "AI"}
    for i in range(len(docs))
]

# 插入
res = client.insert(collection_name="demo_collection", data=data)
print(f"成功插入 {res['insert_count']} 条数据")