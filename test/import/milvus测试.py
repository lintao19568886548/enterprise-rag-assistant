import random

from pymilvus import MilvusClient, DataType

client = MilvusClient(
    uri="http://192.168.100.101:19530",
    token="root:123456"          # 默认用户名:密码
)

# 模拟数据（实际项目中使用 Embedding 模型生成向量）
data = [
    {
        "id": i,
        "vector": [random.uniform(-1, 1) for _ in range(1024)],
        "text": f"这是第 {i} 条测试文本",
        "subject": "technology"
    }
    for i in range(5)
]

# 插入数据
res = client.insert(
    collection_name="demo_collection",
    data=data
)
print(f"成功插入 {res['insert_count']} 条数据")
# 输出：成功插入 100 条数据