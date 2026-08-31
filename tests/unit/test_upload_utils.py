from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core.errors import AppError, ErrorCode
from app.utils.upload_utils import save_validated_upload, validate_upload_metadata


def make_upload(filename: str, content: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=BytesIO(content),
        headers=Headers({"content-type": content_type}),
    )


def test_rejects_path_traversal_filename():
    upload = make_upload("../secret.pdf", b"%PDF-1.7\n", "application/pdf")
    with pytest.raises(AppError) as captured:
        validate_upload_metadata(upload)
    assert captured.value.code == ErrorCode.FILE_NAME_INVALID


def test_rejects_extension_mime_mismatch():
    upload = make_upload("manual.pdf", b"%PDF-1.7\n", "image/png")
    with pytest.raises(AppError) as captured:
        validate_upload_metadata(upload)
    assert captured.value.code == ErrorCode.FILE_TYPE_NOT_ALLOWED


@pytest.mark.asyncio
async def test_pdf_is_streamed_to_uuid_filename(tmp_path: Path):
    upload = make_upload("用户手册.pdf", b"%PDF-1.7\nbody\n%%EOF", "application/pdf")
    saved = await save_validated_upload(upload, tmp_path)
    assert saved.original_filename == "用户手册.pdf"
    assert saved.path.exists()
    assert saved.path.name != saved.original_filename
    assert saved.path.suffix == ".pdf"
    assert saved.sha256


@pytest.mark.asyncio
async def test_rejects_fake_pdf_and_cleans_partial_file(tmp_path: Path):
    upload = make_upload("fake.pdf", b"this is not a pdf", "application/pdf")
    with pytest.raises(AppError) as captured:
        await save_validated_upload(upload, tmp_path)
    assert captured.value.code == ErrorCode.FILE_SIGNATURE_INVALID
    assert list(tmp_path.iterdir()) == []
