"""Secret-safe repository scanner.

By default the scanner checks only files Git could publish (tracked plus untracked,
excluding .gitignore matches). Use ``--include-ignored`` for a broader local audit.
Matches are always redacted.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
MAX_FILE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Rule:
    rule_id: str
    pattern: re.Pattern[bytes]
    value_group: int = 0


RULES = (
    Rule("PRIVATE-KEY", re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    Rule(
        "GITHUB-TOKEN",
        re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    Rule("CLOUD-ACCESS-KEY", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Rule(
        "API-TOKEN",
        re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}\b"),
    ),
    Rule(
        "JWT",
        re.compile(rb"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    Rule(
        "SENSITIVE-ASSIGNMENT",
        re.compile(
            rb"""(?ix)
            \b(?:api[_-]?key|password|passwd|secret|token|access[_-]?key)
            \s*[:=]\s*["']([^"'\r\n]{8,})["']
            """
        ),
        1,
    ),
    Rule(
        "CREDENTIAL-URL",
        re.compile(rb"""(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:([^/@\s]{6,})@"""),
        1,
    ),
    Rule(
        "BEARER-TOKEN",
        re.compile(rb"""(?i)\bauthorization\s*[:=]\s*["']bearer\s+([^"'\s]{12,})"""),
        1,
    ),
)

SAFE_MARKERS = (
    b"change_me",
    b"your_",
    b"example",
    b"placeholder",
    b"dummy",
    b"fake",
    b"test-",
    b"test_",
    b"not-a-real",
    b"${",
)


def public_candidates() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        ROOT / raw.decode("utf-8", errors="surrogateescape")
        for raw in completed.stdout.split(b"\0")
        if raw
    ]


def all_local_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts)
    ]


def masked(value: bytes) -> str:
    text = value.decode("utf-8", errors="replace")
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}***{text[-3:]}"


def safe_fixture(value: bytes) -> bool:
    lowered = value.strip().lower()
    return any(marker in lowered for marker in SAFE_MARKERS)


def scan_content(label: str, content: bytes, findings: set[tuple[str, str, int, str]]) -> None:
    if b"\0" in content[:8192]:
        return
    allow_next_fixture = False
    for line_number, line in enumerate(content.splitlines(), start=1):
        if b"secret-scan: allow-test-fixture" in line:
            allow_next_fixture = True
            continue
        matched_line = False
        for rule in RULES:
            for match in rule.pattern.finditer(line):
                matched_line = True
                value = match.group(rule.value_group)
                if allow_next_fixture or (rule.value_group and safe_fixture(value)):
                    continue
                findings.add((rule.rule_id, label, line_number, masked(value)))
        if matched_line:
            allow_next_fixture = False


def scan(paths: list[Path]) -> tuple[list[tuple[str, str, int, str]], list[str]]:
    findings: set[tuple[str, str, int, str]] = set()
    skipped_large: list[str] = []
    for path in sorted(set(paths)):
        if not path.is_file():
            continue
        relative = str(path.relative_to(ROOT))
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                skipped_large.append(relative)
                continue
            content = path.read_bytes()
        except OSError:
            continue
        scan_content(relative, content, findings)
    return sorted(findings), sorted(set(skipped_large))


def git_object_labels() -> dict[str, str]:
    labels: dict[str, str] = {}
    index = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    for record in index.stdout.split(b"\0"):
        if not record or b"\t" not in record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        parts = metadata.split()
        if len(parts) >= 2:
            labels.setdefault(
                parts[1].decode(),
                raw_path.decode("utf-8", errors="surrogateescape"),
            )
    history = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    for line in history.stdout.splitlines():
        object_id, _, path = line.partition(" ")
        if path:
            labels.setdefault(object_id, path)
    return labels


def scan_git_objects() -> tuple[list[tuple[str, str, int, str]], int, int]:
    labels = git_object_labels()
    objects = subprocess.run(
        [
            "git",
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    findings: set[tuple[str, str, int, str]] = set()
    blobs = 0
    skipped_large = 0
    for line in objects.stdout.splitlines():
        object_id, object_type, size_text = line.split()
        if object_type != "blob":
            continue
        blobs += 1
        if int(size_text) > MAX_FILE_BYTES:
            skipped_large += 1
            continue
        content = subprocess.run(
            ["git", "cat-file", "blob", object_id],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        label = labels.get(object_id, f"git-object:{object_id[:12]}")
        scan_content(label, content, findings)
    return sorted(findings), blobs, skipped_large


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--include-ignored",
        action="store_true",
        help="also scan ignored local files, reports, and local secret files",
    )
    parser.add_argument(
        "--git-objects",
        action="store_true",
        help="scan every local Git blob, including dangling objects",
    )
    args = parser.parse_args()
    if args.include_ignored and args.git_objects:
        parser.error("--include-ignored and --git-objects are mutually exclusive")
    if args.git_objects:
        findings, blobs, skipped_large = scan_git_objects()
        for rule_id, path, line_number, redacted in findings:
            print(f"FOUND {rule_id} {path}:{line_number} value={redacted}")
        print(
            "Secret scan completed: "
            f"scope=git-objects blobs={blobs} findings={len(findings)} "
            f"skipped_large={skipped_large}"
        )
        return 1 if findings else 0
    paths = all_local_files() if args.include_ignored else public_candidates()
    findings, skipped_large = scan(paths)
    for rule_id, path, line_number, redacted in findings:
        print(f"FOUND {rule_id} {path}:{line_number} value={redacted}")
    print(
        "Secret scan completed: "
        f"scope={'all-local' if args.include_ignored else 'publishable'} "
        f"files={len(paths)} findings={len(findings)} skipped_large={len(skipped_large)}"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
