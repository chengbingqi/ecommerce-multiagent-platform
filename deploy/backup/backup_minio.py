"""Copy allowlisted MinIO buckets to an external filesystem with SHA-256 evidence."""

import argparse
import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from minio import Minio

BUCKETS = ("media-original", "media-derived")


def secret(path: Path) -> str:
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError("secret file permissions are too broad")
    return path.read_text(encoding="utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("external_target", type=Path)
    parser.add_argument("--endpoint", default="127.0.0.1:9000")
    parser.add_argument("--access-key-file", type=Path, required=True)
    parser.add_argument("--secret-key-file", type=Path, required=True)
    args = parser.parse_args()
    data_root_value = os.environ.get("DATA_ROOT")
    if not data_root_value:
        raise RuntimeError("DATA_ROOT must identify the project data filesystem")
    data_root = Path(data_root_value)
    args.external_target.mkdir(parents=True, exist_ok=True)
    if os.stat(args.external_target).st_dev == os.stat(data_root).st_dev:
        raise RuntimeError("BACKUP-EXTERNAL-001: external target is on the data filesystem")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    partial = args.external_target / "minio" / f"{stamp}.partial"
    final = partial.with_suffix("")
    partial.mkdir(parents=True)
    client = Minio(
        args.endpoint,
        access_key=secret(args.access_key_file),
        secret_key=secret(args.secret_key_file),
        secure=False,
    )
    manifest: list[dict[str, object]] = []
    for bucket in BUCKETS:
        if not client.bucket_exists(bucket):
            continue
        for item in client.list_objects(bucket, recursive=True):
            destination = partial / bucket / item.object_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            client.fget_object(bucket, item.object_name, str(destination))
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            manifest.append(
                {"bucket": bucket, "object": item.object_name, "size": item.size, "sha256": digest}
            )
    (partial / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (partial / "COMPLETED").touch()
    partial.rename(final)
    print(f"MinIO backup completed: {final}")


if __name__ == "__main__":
    main()
