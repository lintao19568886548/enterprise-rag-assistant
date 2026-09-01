"""Execute a non-destructive recovery drill with generated, non-business data."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def _digest(rows: list[tuple[object, ...]]) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def run_drill(report_path: Path) -> dict[str, object]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="enterprise-rag-recovery-") as raw_dir:
        directory = Path(raw_dir)
        source_path = directory / "source.db"
        backup_path = directory / "backup.db"
        restored_path = directory / "restored.db"
        with closing(sqlite3.connect(source_path)) as source:
            source.execute("PRAGMA foreign_keys=ON")
            source.executescript(
                "CREATE TABLE tenants(id TEXT PRIMARY KEY, name TEXT NOT NULL);"
                "CREATE TABLE users(id TEXT PRIMARY KEY, subject TEXT NOT NULL UNIQUE);"
                "CREATE TABLE memberships(tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id), role TEXT NOT NULL, PRIMARY KEY(tenant_id,user_id));"
                "CREATE TABLE knowledge_bases(id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), name TEXT NOT NULL);"
                "CREATE TABLE documents(id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id), title TEXT NOT NULL);"
                "CREATE TABLE document_versions(document_id TEXT NOT NULL REFERENCES documents(id), version INTEGER NOT NULL, is_active INTEGER NOT NULL, PRIMARY KEY(document_id,version));"
                "CREATE TABLE sessions(id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id));"
                "CREATE TABLE messages(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), role TEXT NOT NULL, text TEXT NOT NULL);"
                "CREATE TABLE citations(id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id), document_id TEXT NOT NULL REFERENCES documents(id), page_number INTEGER);"
                "CREATE TABLE images(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), object_path TEXT NOT NULL);"
                "CREATE TABLE vector_manifest(document_id TEXT NOT NULL REFERENCES documents(id), version INTEGER NOT NULL, chunk_count INTEGER NOT NULL, PRIMARY KEY(document_id,version));"
            )
            source.execute("INSERT INTO tenants VALUES (?, ?)", ("tenant-drill", "脱敏恢复演练"))
            source.execute("INSERT INTO users VALUES (?, ?)", ("user-drill", "synthetic-subject"))
            source.execute("INSERT INTO memberships VALUES (?, ?, ?)", ("tenant-drill", "user-drill", "admin"))
            source.execute("INSERT INTO knowledge_bases VALUES (?, ?, ?)", ("kb-drill", "tenant-drill", "synthetic-kb"))
            source.executemany(
                "INSERT INTO documents VALUES (?, ?, ?, ?)",
                [
                    ("doc-1", "tenant-drill", "kb-drill", "synthetic-a"),
                    ("doc-2", "tenant-drill", "kb-drill", "synthetic-b"),
                ],
            )
            source.executemany(
                "INSERT INTO document_versions VALUES (?, ?, ?)",
                [("doc-1", 1, 1), ("doc-2", 1, 1)],
            )
            source.execute("INSERT INTO sessions VALUES (?, ?, ?)", ("session-drill", "tenant-drill", "user-drill"))
            source.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?)",
                [
                    ("message-1", "session-drill", "user", "synthetic question"),
                    ("message-2", "session-drill", "assistant", "synthetic grounded answer"),
                ],
            )
            source.execute("INSERT INTO citations VALUES (?, ?, ?, ?)", ("citation-1", "message-2", "doc-1", 3))
            source.execute("INSERT INTO images VALUES (?, ?, ?)", ("image-1", "doc-1", "synthetic/images/1.png"))
            source.executemany(
                "INSERT INTO vector_manifest VALUES (?, ?, ?)",
                [("doc-1", 1, 4), ("doc-2", 1, 3)],
            )
            source.commit()
            with closing(sqlite3.connect(backup_path)) as backup:
                source.backup(backup)
        backup_path.replace(restored_path)
        with closing(sqlite3.connect(restored_path)) as restored:
            restored.execute("PRAGMA foreign_keys=ON")
            tenants = restored.execute("SELECT id, name FROM tenants ORDER BY id").fetchall()
            documents = restored.execute(
                "SELECT id, tenant_id, knowledge_base_id, title FROM documents ORDER BY id"
            ).fetchall()
            table_names = (
                "users",
                "memberships",
                "knowledge_bases",
                "document_versions",
                "sessions",
                "messages",
                "citations",
                "images",
                "vector_manifest",
            )
            entity_counts = {
                table: int(restored.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in table_names
            }
            vector_count = int(restored.execute("SELECT COALESCE(SUM(chunk_count),0) FROM vector_manifest").fetchone()[0])
            foreign_key_errors = restored.execute("PRAGMA foreign_key_check").fetchall()
            integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
        report: dict[str, object] = {
            "drill": "sanitized-sqlite-backup-restore",
            "executed_at": datetime.now(UTC).isoformat(),
            "source_is_business_data": False,
            "tenant_count": len(tenants),
            "document_count": len(documents),
            "entity_counts": entity_counts,
            "vector_manifest_chunk_count": vector_count,
            "row_digest": _digest(tenants + documents + sorted(entity_counts.items())),
            "foreign_key_error_count": len(foreign_key_errors),
            "integrity_check": integrity,
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "passed": (
                len(tenants) == 1
                and len(documents) == 2
                and entity_counts
                == {
                    "users": 1,
                    "memberships": 1,
                    "knowledge_bases": 1,
                    "document_versions": 2,
                    "sessions": 1,
                    "messages": 2,
                    "citations": 1,
                    "images": 1,
                    "vector_manifest": 2,
                }
                and vector_count == 7
                and not foreign_key_errors
                and integrity == "ok"
            ),
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = run_drill(args.report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
