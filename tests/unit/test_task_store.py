import fakeredis

from app.utils.task_store import MemoryTaskStore, RedisTaskStore


def test_memory_task_store_tracks_order_and_removes_running_node():
    store = MemoryTaskStore()
    store.set_status("task-1", "processing")
    store.add_running("task-1", "node_entry")
    store.add_running("task-1", "node_entry")
    store.add_done("task-1", "node_entry")
    store.set_result("task-1", "answer", "ok")

    assert store.get_status("task-1") == "processing"
    assert store.get_running("task-1") == []
    assert store.get_done("task-1") == ["node_entry"]
    assert store.get_result("task-1", "answer") == "ok"


def test_memory_task_store_clear_is_idempotent():
    store = MemoryTaskStore()
    store.set_status("task-2", "completed")
    store.clear("task-2")
    store.clear("task-2")
    assert store.get_status("task-2") == ""


def test_task_results_support_structured_values_in_memory_and_redis():
    stores = [
        MemoryTaskStore(),
        RedisTaskStore(fakeredis.FakeRedis(decode_responses=True)),
    ]
    payload = {"citations": [{"chunk_id": "42"}], "confidence": 0.91}
    for store in stores:
        store.set_result("task-structured", "answer_metadata", payload)
        assert store.get_result("task-structured", "answer_metadata") == payload
