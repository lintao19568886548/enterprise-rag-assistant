
import time
from pymilvus import MilvusClient
from app.clients.milvus_utils import get_milvus_client

def test_load_collection_performance():
    """
    测试 load_collection() 对 delete() 和 insert() 性能的影响
    """
    client = get_milvus_client()
    if not client:
        print("无法连接 Milvus")
        return

    collection_name = "test_performance_collection"
    test_item_name = "test_product_001"

    # 清理环境
    if client.has_collection(collection_name):
        client.drop_collection(collection_name)

    # 创建测试集合
    from pymilvus import DataType
    schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
    schema.add_field(field_name="pk", datatype=DataType.INT64, is_primary=True, auto_id=True)
    schema.add_field(field_name="item_name", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=1024)

    index_params = client.prepare_index_params()
    index_params.add_index(field_name="dense_vector", index_type="FLAT", metric_type="COSINE")
    client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)

    test_vector = [0.1] * 1024

    print("\n=== 测试 1: 不使用 load_collection() ===")
    start_time = time.time()

    # 方案 B: 不提前 load
    for i in range(10):
        client.delete(collection_name=collection_name, filter=f'item_name=="{test_item_name}_{i}"')
        client.insert(collection_name=collection_name, data=[{
            "item_name": f"{test_item_name}_{i}",
            "dense_vector": test_vector
        }])

    time_without_load = time.time() - start_time
    print(f"总耗时：{time_without_load:.4f}秒")
    print(f"平均每次操作：{time_without_load/10:.4f}秒")

    # 清理数据
    client.drop_collection(collection_name)
    client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)

    print("\n=== 测试 2: 使用 load_collection() ===")
    start_time = time.time()

    # 方案 A: 提前 load
    client.load_collection(collection_name=collection_name)
    for i in range(10):
        client.delete(collection_name=collection_name, filter=f'item_name=="{test_item_name}_{i}"')
        client.insert(collection_name=collection_name, data=[{
            "item_name": f"{test_item_name}_{i}",
            "dense_vector": test_vector
        }])

    time_with_load = time.time() - start_time
    print(f"总耗时：{time_with_load:.4f}秒")
    print(f"平均每次操作：{time_with_load/10:.4f}秒")

    # 计算性能差异
    improvement = ((time_without_load - time_with_load) / time_without_load) * 100
    print(f"\n=== 性能对比 ===")
    print(f"性能提升：{improvement:.2f}%")

    if improvement > 5:
        print("结论：load_collection() 有明显性能提升 ✅")
    elif improvement > 0:
        print("结论：load_collection() 有轻微提升，但不明显 ⚠️")
    else:
        print("结论：load_collection() 没有性能提升，反而可能更慢 ❌")

    # 清理
    client.drop_collection(collection_name)

if __name__ == "__main__":
    test_load_collection_performance()
