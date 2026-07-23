#!/usr/bin/env python3
"""Plan and download a pinned Hugging Face snapshot with reusable caches."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a Hugging Face repository first, then optionally download a pinned "
            "snapshot without deleting existing cache data."
        )
    )
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--local-dir", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--allow-pattern", action="append", default=[])
    parser.add_argument("--ignore-pattern", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Download after printing the resolved file plan; without this flag, "
            "only inspect metadata."
        ),
    )
    return parser.parse_args()


def selected(path: str, allow: list[str], ignore: list[str]) -> bool:
    if allow and not any(fnmatch.fnmatch(path, pattern) for pattern in allow):
        return False
    return not any(fnmatch.fnmatch(path, pattern) for pattern in ignore)


def human_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_proxy_environment() -> None:
    """Prefer usable HTTP proxies when a desktop exports legacy socks:// URLs."""
    has_http_proxy = any(
        os.environ.get(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    )
    if not has_http_proxy:
        return
    for name in ("ALL_PROXY", "all_proxy"):
        value = os.environ.get(name)
        if value and urlsplit(value).scheme.lower() == "socks":
            os.environ.pop(name, None)


def build_manifest(
    local_dir: Path,
    repo_id: str,
    requested_revision: str,
    resolved_revision: str,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted(local_dir.rglob("*")):
        if not path.is_file() or path.name == "download_manifest.json":
            continue
        relative = path.relative_to(local_dir).as_posix()
        if relative.startswith(".cache/huggingface/"):
            continue
        rows.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schema_version": "hf_snapshot_manifest_v1",
        "repo_id": repo_id,
        "requested_revision": requested_revision,
        "resolved_revision": resolved_revision,
        "generated_at": datetime.now(UTC).isoformat(),
        "files": rows,
    }


def main() -> int:
    args = parse_args()
    if args.timeout < 1 or args.retries < 1 or not 1 <= args.max_workers <= 8:
        raise SystemExit("timeout/retries must be positive and max-workers must be between 1 and 8")

    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", str(args.timeout))
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(args.timeout))
    normalize_proxy_environment()

    try:
        from huggingface_hub import HfApi, snapshot_download
    except ModuleNotFoundError as error:
        raise SystemExit(
            "huggingface_hub is required; run this script with the project .venv Python"
        ) from error

    api = HfApi()
    info = api.model_info(
        args.repo_id,
        revision=args.revision,
        files_metadata=True,
        token=False,
    )
    files = [
        (row.rfilename, row.size)
        for row in info.siblings
        if selected(row.rfilename, args.allow_pattern, args.ignore_pattern)
    ]
    total = sum(size for _, size in files if size is not None)
    unknown = sum(size is None for _, size in files)

    print(f"repo_id={args.repo_id}")
    print(f"requested_revision={args.revision}")
    print(f"resolved_revision={info.sha}")
    print(f"file_count={len(files)} total_known_size={human_bytes(total)} unknown_sizes={unknown}")
    for name, size in files:
        print(f"{human_bytes(size):>12}  {name}")

    if not args.execute:
        print("plan_only=true; add --execute to download")
        return 0

    args.local_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(1, args.retries + 1):
        try:
            snapshot_download(
                repo_id=args.repo_id,
                revision=info.sha,
                local_dir=args.local_dir,
                cache_dir=args.cache_dir,
                allow_patterns=args.allow_pattern or None,
                ignore_patterns=args.ignore_pattern or None,
                max_workers=args.max_workers,
                token=False,
            )
            last_error = None
            break
        except Exception as error:  # The cache is intentionally retained for the next attempt.
            last_error = error
            print(
                f"download_attempt={attempt}/{args.retries} failed={type(error).__name__}",
                file=sys.stderr,
            )
            if attempt < args.retries:
                time.sleep(min(2**attempt, 10))
    if last_error is not None:
        print("download_failed=true; partial cache retained", file=sys.stderr)
        return 1

    manifest = build_manifest(args.local_dir, args.repo_id, args.revision, info.sha)
    manifest_path = args.local_dir / "download_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"download_complete=true files={len(manifest['files'])}")
    print(f"manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
