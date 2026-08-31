"""Streaming, bounded and path-safe upload handling."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiofiles
from fastapi import UploadFile

from app.core.errors import AppError, ErrorCode
from app.core.settings import settings

PDF_MIME_TYPES = {"application/pdf", "application/octet-stream"}
MARKDOWN_MIME_TYPES = {
    "text/markdown",
    "text/plain",
    "text/x-markdown",
    "application/octet-stream",
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class SavedUpload:
    original_filename: str
    stored_filename: str
    path: Path
    content_type: str
    size: int
    sha256: str


def validate_upload_metadata(upload: UploadFile) -> tuple[str, str, str]:
    original = upload.filename or ""
    if not original or len(original) > settings.upload_max_filename_length:
        raise AppError(ErrorCode.FILE_NAME_INVALID, "文件名为空或长度超出限制")
    if Path(original).name != original or any(value in original for value in ("/", "\\", "\x00")):
        raise AppError(ErrorCode.FILE_NAME_INVALID, "文件名包含不安全的路径字符")
    stem = Path(original).stem.strip().rstrip(". ")
    if not stem or stem.upper() in WINDOWS_RESERVED_NAMES:
        raise AppError(ErrorCode.FILE_NAME_INVALID, "文件名不受 Windows 文件系统支持")

    suffix = Path(original).suffix.lower()
    if suffix not in settings.allowed_upload_extensions:
        allowed = ", ".join(sorted(settings.allowed_upload_extensions))
        raise AppError(
            ErrorCode.FILE_TYPE_NOT_ALLOWED,
            f"不支持该文件类型，仅允许：{allowed}",
            status_code=415,
        )

    content_type = (upload.content_type or "application/octet-stream").lower().split(";", 1)[0].strip()
    allowed_mime = PDF_MIME_TYPES if suffix == ".pdf" else MARKDOWN_MIME_TYPES
    if content_type not in allowed_mime:
        raise AppError(
            ErrorCode.FILE_TYPE_NOT_ALLOWED,
            f"文件 MIME 类型与扩展名不匹配：{content_type}",
            status_code=415,
        )
    return original, suffix, content_type


def _validate_signature(suffix: str, head: bytes) -> None:
    if suffix == ".pdf":
        if not head.startswith(b"%PDF-"):
            raise AppError(
                ErrorCode.FILE_SIGNATURE_INVALID,
                "文件扩展名为 PDF，但文件签名不是有效 PDF",
                status_code=415,
            )
        return
    try:
        text = head.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError(
            ErrorCode.FILE_SIGNATURE_INVALID,
            "Markdown 文件必须使用 UTF-8 编码",
            status_code=415,
        ) from exc
    if not text.strip():
        raise AppError(ErrorCode.FILE_SIGNATURE_INVALID, "Markdown 文件内容为空")
    if "\x00" in text or re.search(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", text):
        raise AppError(ErrorCode.FILE_SIGNATURE_INVALID, "Markdown 文件包含二进制控制字符")


async def save_validated_upload(upload: UploadFile, target_dir: Path) -> SavedUpload:
    original, suffix, content_type = validate_upload_metadata(upload)
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}{suffix}"
    final_path = (target_dir / stored_filename).resolve()
    if target_dir not in final_path.parents:
        raise AppError(ErrorCode.FILE_NAME_INVALID, "文件保存路径不安全")
    partial_path = final_path.with_suffix(final_path.suffix + ".part")

    size = 0
    digest = hashlib.sha256()
    head = bytearray()
    try:
        async with aiofiles.open(partial_path, "xb") as output:
            while chunk := await upload.read(settings.upload_chunk_size_bytes):
                size += len(chunk)
                if size > settings.upload_max_file_size_bytes:
                    raise AppError(
                        ErrorCode.FILE_TOO_LARGE,
                        f"单个文件不能超过 {settings.upload_max_file_size_mb} MB",
                        status_code=413,
                    )
                if len(head) < 8192:
                    head.extend(chunk[: 8192 - len(head)])
                digest.update(chunk)
                await output.write(chunk)
        if size == 0:
            raise AppError(ErrorCode.FILE_SIGNATURE_INVALID, "不能上传空文件")
        _validate_signature(suffix, bytes(head))
        partial_path.replace(final_path)
    except Exception:
        partial_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    return SavedUpload(
        original_filename=original,
        stored_filename=stored_filename,
        path=final_path,
        content_type=content_type,
        size=size,
        sha256=digest.hexdigest(),
    )
