from pymilvus import MilvusClient

client = MilvusClient(
    uri="http://192.168.100.101:19530",
    token="root:123456"
)
#
# # 方式一：按主键删除
# res = client.delete(
#     collection_name="demo_collection",
#     ids=[3, 4]
# )
# print(f"删除的ID: {res}")

# 方式二：按过滤条件删除
res = client.delete(
    collection_name="demo_collection",
    filter="text == '我喜欢羽毛球。'"
)
print(f"删除的ID: {res}")