import random

from pymilvus import MilvusClient

client = MilvusClient(
    uri="http://192.168.100.101:19530",
    token="root:123456"
)

data = [
    {
        "id": 100,
        "vector": [random.uniform(-1, 1) for _ in range(1024)],
        "text": "我喜欢羽毛球。",
        "subject": "AI"
    }
]

res = client.upsert(
    collection_name="demo_collection",
    data=data
)
print(f"Upsert 结果：{res}")