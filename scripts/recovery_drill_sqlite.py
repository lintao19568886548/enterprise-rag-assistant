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
                "CREATE TABLE documents(id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), title TEXT NOT NULL);"
            )
            source.execute("INSERT INTO tenants VALUES (?, ?)", ("tenant-drill", "脱敏恢复演练"))
            source.executemany(
                "INSERT INTO documents VALUES (?, ?, ?)",
                [("doc-1", "tenant-drill", "synthetic-a"), ("doc-2", "tenant-drill", "synthetic-b")],
            )
            source.commit()
            with closing(sqlite3.connect(backup_path)) as backup:
                source.backup(backup)
        backup_path.replace(restored_path)
        with closing(sqlite3.connect(restored_path)) as restored:
            restored.execute("PRAGMA foreign_keys=ON")
            tenants = restored.execute("SELECT id, name FROM tenants ORDER BY id").fetchall()
            documents = restored.execute(
                "SELECT id, tenant_id, title FROM documents ORDER BY id"
            ).fetchall()
            foreign_key_errors = restored.execute("PRAGMA foreign_key_check").fetchall()
            integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
        report: dict[str, object] = {
            "drill": "sanitized-sqlite-backup-restore",
            "executed_at": datetime.now(UTC).isoformat(),
            "source_is_business_data": False,
            "tenant_count": len(tenants),
            "document_count": len(documents),
            "row_digest": _digest(tenants + documents),
            "foreign_key_error_count": len(foreign_key_errors),
            "integrity_check": integrity,
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "passed": len(tenants) == 1 and len(documents) == 2 and not foreign_key_errors and integrity == "ok",
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
