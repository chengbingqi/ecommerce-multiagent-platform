"""Restore representative objects into an isolated temporary MinIO bucket."""

import argparse
import hashlib
import json
import secrets
from pathlib import Path

from backup_minio import secret
from minio import Minio


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup_directory", type=Path)
    parser.add_argument("--endpoint", default="127.0.0.1:9000")
    parser.add_argument("--access-key-file", type=Path, required=True)
    parser.add_argument("--secret-key-file", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.backup_directory / "manifest.json").read_text(encoding="utf-8"))
    selected = []
    categories = (("original", "original"), ("subtitle", ".srt"), ("candidate", ".mp4"))
    for _category, marker in categories:
        match = next((row for row in manifest if marker in str(row["object"]).lower()), None)
        if match and match not in selected:
            selected.append(match)
    if not selected:
        raise RuntimeError("RESTORE-MINIO-INPUT-001: no representative objects")
    client = Minio(
        args.endpoint,
        access_key=secret(args.access_key_file),
        secret_key=secret(args.secret_key_file),
        secure=False,
    )
    bucket = f"stage7-restore-drill-{secrets.token_hex(6)}"
    client.make_bucket(bucket)
    restored: list[str] = []
    try:
        for row in selected:
            source = args.backup_directory / str(row["bucket"]) / str(row["object"])
            object_name = f"isolated/{row['bucket']}/{row['object']}"
            client.fput_object(bucket, object_name, str(source))
            response = client.get_object(bucket, object_name)
            try:
                payload = response.read()
            finally:
                response.close()
                response.release_conn()
            if (
                len(payload) != int(row["size"])
                or hashlib.sha256(payload).hexdigest() != row["sha256"]
            ):
                raise RuntimeError("RESTORE-MINIO-VERIFY-001: checksum mismatch")
            restored.append(object_name)
        print(f"MinIO isolated restore verified: objects={len(restored)}")
    finally:
        for object_name in restored:
            client.remove_object(bucket, object_name)
        client.remove_bucket(bucket)


if __name__ == "__main__":
    main()
