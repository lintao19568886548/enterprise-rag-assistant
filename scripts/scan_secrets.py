"""Fail closed when tracked or new source files contain likely credentials.

The scanner deliberately reports only rule, path, and line number. It never
prints the matched value, which keeps CI logs useful without becoming another
place where a credential can leak.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MAX_FILE_BYTES = 2 * 1024 * 1024
SKIPPED_SUFFIXES = {
    ".7z",
    ".db",
    ".dll",
    ".docx",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".lock",
    ".pdf",
    ".png",
    ".pyd",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".webp",
    ".xlsx",
    ".zip",
}
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key-header",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    ("openai-compatible-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b")),
    (
        "credential-assignment",
        re.compile(
            r"(?i)(?:api[_-]?key|client[_-]?secret|password|access[_-]?token|refresh[_-]?token)"
            r"\s*[:=]\s*[\"']([A-Za-z0-9_./+\-=]{20,})[\"']"
        ),
    ),
)
ALLOWLIST_MARKERS = (
    "${",
    "<redacted>",
    "change-me",
    "dummy",
    "example",
    "fake",
    "not-a-real",
    "placeholder",
    "replace-with",
    "sk-replace",
    "sample",
    "sk-test-",
    "test-secret",
    "your-",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str


def _allowlisted(line: str) -> bool:
    normalized = line.casefold()
    return any(marker in normalized for marker in ALLOWLIST_MARKERS)


def scan_text(text: str, *, path: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _allowlisted(line):
            continue
        for rule_name, pattern in RULES:
            if pattern.search(line):
                findings.append(Finding(path=path, line=line_number, rule=rule_name))
    return findings


def repository_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def scan_files(paths: Iterable[Path], *, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not path.is_file() or path.suffix.casefold() in SKIPPED_SUFFIXES:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        try:
            display_path = path.relative_to(root).as_posix()
        except ValueError:
            display_path = path.name
        findings.extend(scan_text(text, path=display_path))
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan repository sources for likely secrets")
    parser.add_argument("paths", nargs="*", type=Path, help="optional files to scan")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root_bytes = subprocess.check_output(["git", "rev-parse", "--show-toplevel"])
    root = Path(root_bytes.decode("utf-8").strip())
    paths = [path.resolve() for path in args.paths] if args.paths else repository_files(root)
    findings = scan_files(paths, root=root)
    if findings:
        print("Secret scan failed. Matched values are intentionally redacted:")
        for finding in findings:
            print(f"- {finding.rule}: {finding.path}:{finding.line}")
        return 1
    print(f"Secret scan passed ({len(paths)} files inspected).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
