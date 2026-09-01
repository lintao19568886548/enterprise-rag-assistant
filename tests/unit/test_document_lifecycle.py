from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.lifecycle_repositories import (
    activate_document_version,
    get_cleanup_event,
    get_document_cleanup_context,
    request_document_deletion,
    retry_cleanup_event,
)
from app.db.models import Document, DocumentVersion
from app.db.repositories import (
    DEFAULT_KNOWLEDGE_BASE_ID,
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    create_import_task,
    ensure_defaults,
    register_document,
)
from app.db.session import init_database, session_scope
from app.services.lifecycle import (
    CleanupStageError,
    process_cleanup_event,
    validate_minio_object_name,
    validated_task_output_directories,
)
from app.utils.upload_utils import SavedUpload


def _register(tmp_path: Path, *, name: str | None = None):
    init_database()
    ensure_defaults()
    token = uuid.uuid4().hex
    filename = name or f"manual-{token}.pdf"
    path = tmp_path / filename
    path.write_bytes(b"%PDF-1.7\n%%EOF")
    upload = SavedUpload(
        original_filename=filename,
        stored_filename=filename,
        path=path,
        content_type="application/pdf",
        size=path.stat().st_size,
        sha256=token * 2,
    )
    document, version, _ = register_document(DEFAULT_KNOWLEDGE_BASE_ID, upload, None)
    return document, version, upload


def test_deletion_request_is_idempotent(tmp_path: Path):
    document, _, _ = _register(tmp_path)

    first_document, first_event, first_created = request_document_deletion(
        document.id, DEFAULT_TENANT_ID, DEFAULT_USER_ID
    )
    second_document, second_event, second_created = request_document_deletion(
        document.id, DEFAULT_TENANT_ID, DEFAULT_USER_ID
    )

    assert first_created is True
    assert second_created is False
    assert first_event.id == second_event.id
    assert first_document.lifecycle_status == second_document.lifecycle_status == "DELETING"


def test_cleanup_resumes_after_worker_crash(tmp_path: Path):
    document, _, _ = _register(tmp_path)
    _, event, _ = request_document_deletion(document.id, DEFAULT_TENANT_ID, DEFAULT_USER_ID)
    calls = {"vectors": 0, "object_storage": 0, "local_output": 0}

    def vectors(_):
        calls["vectors"] += 1

    def fail_object_storage(_):
        calls["object_storage"] += 1
        raise ConnectionError("simulated dependency outage")

    with pytest.raises(CleanupStageError):
        process_cleanup_event(
            event.id,
            DEFAULT_TENANT_ID,
            stage_handlers={
                "vectors": vectors,
                "object_storage": fail_object_storage,
                "local_output": lambda _: None,
            },
        )

    failed = get_cleanup_event(event.id, DEFAULT_TENANT_ID)
    assert failed is not None
    assert failed.status == "RETRYING"
    assert failed.payload["completed_stages"] == ["vectors"]
    with session_scope() as session:
        assert session.get(Document, document.id).lifecycle_status == "CLEANUP_FAILED"

    retry_cleanup_event(event.id, DEFAULT_TENANT_ID)

    def object_storage(_):
        calls["object_storage"] += 1

    completed = process_cleanup_event(
        event.id,
        DEFAULT_TENANT_ID,
        stage_handlers={
            "vectors": vectors,
            "object_storage": object_storage,
            "local_output": lambda _: calls.__setitem__(
                "local_output", calls["local_output"] + 1
            ),
        },
    )
    assert completed.status == "COMPLETED"
    assert completed.payload["completed_stages"] == [
        "vectors",
        "object_storage",
        "local_output",
        "sql_metadata",
    ]
    assert calls == {"vectors": 1, "object_storage": 2, "local_output": 1}
    assert process_cleanup_event(event.id, DEFAULT_TENANT_ID).status == "COMPLETED"


def test_local_cleanup_rejects_path_outside_output(tmp_path: Path):
    document, _, upload = _register(tmp_path)
    task_id = str(uuid.uuid4())
    create_import_task(
        task_id,
        document.id,
        1,
        local_dir=str(tmp_path),
        local_file_path=str(upload.path),
    )
    _, event, _ = request_document_deletion(document.id, DEFAULT_TENANT_ID, DEFAULT_USER_ID)
    context = get_document_cleanup_context(event.id, DEFAULT_TENANT_ID)

    with pytest.raises(ValueError, match="outside project output root"):
        validated_task_output_directories(context)


@pytest.mark.parametrize(
    "value",
    ["../secret", "/absolute/object", r"images\tenant\file.png"],
)
def test_minio_object_validation_rejects_traversal(value: str):
    with pytest.raises(ValueError, match="invalid object name"):
        validate_minio_object_name(value)


def test_dead_letter_can_be_manually_retried(tmp_path: Path):
    document, _, _ = _register(tmp_path)
    _, event, _ = request_document_deletion(document.id, DEFAULT_TENANT_ID, DEFAULT_USER_ID)
    with session_scope() as session:
        persisted = session.get(type(event), event.id)
        persisted.max_attempts = 1

    with pytest.raises(CleanupStageError):
        process_cleanup_event(
            event.id,
            DEFAULT_TENANT_ID,
            stage_handlers={"vectors": lambda _: (_ for _ in ()).throw(ConnectionError())},
        )
    failed = get_cleanup_event(event.id, DEFAULT_TENANT_ID)
    assert failed is not None and failed.status == "DEAD_LETTER"

    reset = retry_cleanup_event(event.id, DEFAULT_TENANT_ID)
    assert reset.status == "PENDING"
    assert reset.attempts == 0
    completed = process_cleanup_event(
        event.id,
        DEFAULT_TENANT_ID,
        stage_handlers={
            "vectors": lambda _: None,
            "object_storage": lambda _: None,
            "local_output": lambda _: None,
        },
    )
    assert completed.status == "COMPLETED"


def test_historical_version_activation_keeps_one_active_version(tmp_path: Path):
    document, version_one, first_upload = _register(tmp_path, name=f"versioned-{uuid.uuid4().hex}.pdf")
    second_path = tmp_path / "second.pdf"
    second_path.write_bytes(b"%PDF-1.7\nsecond\n%%EOF")
    second_upload = SavedUpload(
        original_filename=first_upload.original_filename,
        stored_filename="second.pdf",
        path=second_path,
        content_type="application/pdf",
        size=second_path.stat().st_size,
        sha256="f" * 64,
    )
    _, version_two, created = register_document(
        DEFAULT_KNOWLEDGE_BASE_ID,
        second_upload,
        None,
    )
    assert created is True

    activated_document, activated, before_count, after_count = activate_document_version(
        document.id,
        version_one.version,
        DEFAULT_TENANT_ID,
        DEFAULT_USER_ID,
    )
    assert activated_document.current_version == version_one.version
    assert activated.is_active is True
    assert before_count == after_count == 0
    with session_scope() as session:
        versions = list(
            session.scalars(
                select(DocumentVersion).where(DocumentVersion.document_id == document.id)
            )
        )
    assert version_two.version == 2
    assert sum(version.is_active for version in versions) == 1
