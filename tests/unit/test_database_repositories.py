from pathlib import Path

from app.db.repositories import (
    DEFAULT_KNOWLEDGE_BASE_ID,
    clear_history,
    ensure_defaults,
    get_recent_messages,
    register_document,
    save_chat_message,
)
from app.db.session import init_database
from app.utils.upload_utils import SavedUpload


def test_document_registration_is_idempotent(tmp_path: Path):
    init_database()
    ensure_defaults()
    path = tmp_path / "stored.pdf"
    path.write_bytes(b"%PDF-1.7\n%%EOF")
    upload = SavedUpload(
        original_filename="manual.pdf",
        stored_filename="stored.pdf",
        path=path,
        content_type="application/pdf",
        size=path.stat().st_size,
        sha256="a" * 64,
    )

    first_document, _, first_created = register_document(DEFAULT_KNOWLEDGE_BASE_ID, upload, None)
    second_document, _, second_created = register_document(DEFAULT_KNOWLEDGE_BASE_ID, upload, None)

    assert first_created is True
    assert second_created is False
    assert first_document.id == second_document.id

    changed_upload = SavedUpload(
        original_filename="manual.pdf",
        stored_filename="stored-v2.pdf",
        path=path,
        content_type="application/pdf",
        size=path.stat().st_size + 1,
        sha256="b" * 64,
    )
    versioned_document, version, version_created = register_document(
        DEFAULT_KNOWLEDGE_BASE_ID,
        changed_upload,
        None,
    )
    assert version_created is True
    assert versioned_document.id == first_document.id
    assert versioned_document.current_version == 2
    assert version.version == 2


def test_sql_chat_history_round_trip():
    init_database()
    ensure_defaults()
    session_id = "history-test-session"
    clear_history(session_id)
    save_chat_message(session_id, "user", "问题")
    save_chat_message(session_id, "assistant", "回答", item_names=["产品A"])

    records = get_recent_messages(session_id, limit=10)

    assert [record["role"] for record in records] == ["user", "assistant"]
    assert records[-1]["item_names"] == ["产品A"]
    assert clear_history(session_id) == 2
