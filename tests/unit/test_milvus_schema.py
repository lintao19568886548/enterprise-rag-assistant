from pymilvus import DataType

from app.clients.milvus_utils import ensure_collection_fields


class FakeMilvusClient:
    def __init__(self, *, dynamic: bool = False):
        self.dynamic = dynamic
        self.fields = [{"name": "chunk_id"}]
        self.released = False
        self.loaded = False
        self.calls: list[dict] = []

    def describe_collection(self, **_kwargs):
        return {
            "enable_dynamic_field": self.dynamic,
            "fields": list(self.fields),
        }

    def release_collection(self, **_kwargs):
        self.released = True

    def load_collection(self, **_kwargs):
        self.loaded = True

    def add_collection_field(self, **kwargs):
        self.calls.append(kwargs)
        self.fields.append({"name": kwargs["field_name"]})


def test_legacy_collection_gets_nullable_metadata_fields():
    client = FakeMilvusClient()

    added = ensure_collection_fields(
        client,
        "chunks",
        {
            "document_id": (DataType.VARCHAR, {"max_length": 64}),
            "is_active": (DataType.BOOL, {}),
        },
    )

    assert added == ["document_id", "is_active"]
    assert client.released is True
    assert client.loaded is True
    assert all(call["nullable"] is True for call in client.calls)


def test_dynamic_collection_needs_no_schema_migration():
    client = FakeMilvusClient(dynamic=True)

    assert ensure_collection_fields(
        client,
        "chunks",
        {"document_id": (DataType.VARCHAR, {"max_length": 64})},
    ) == []
    assert client.calls == []
    assert client.released is False
