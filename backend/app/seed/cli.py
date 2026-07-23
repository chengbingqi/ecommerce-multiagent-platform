import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.seed.generator import (
    canonical_checksum,
    generate_dataset,
    load_manifest,
    scenario_evidence,
)
from app.seed.service import reset_stage2_data, seed_manifest, verify_manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Stage 2 deterministic seed management")
    subcommands = result.add_subparsers(dest="command", required=True)
    for name in ("seed", "verify", "checksum"):
        command = subcommands.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
    reset = subcommands.add_parser("reset")
    reset.add_argument("--confirm-reset", action="store_true")
    return result


async def run() -> None:
    args = parser().parse_args()
    if args.command == "reset":
        await reset_stage2_data(
            get_sessionmaker(),
            app_env=get_settings().app_env,
            confirmed=args.confirm_reset,
        )
        print(json.dumps({"reset": "completed"}))
        return
    manifest = load_manifest(args.manifest)
    if args.command == "checksum":
        dataset = generate_dataset(manifest)
        print(
            json.dumps(
                {
                    "manifest_version": manifest["manifest_version"],
                    "checksum": canonical_checksum(dataset),
                    "scenario_evidence": scenario_evidence(dataset),
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "seed":
        checksum, inserted = await seed_manifest(get_sessionmaker(), manifest)
        print(json.dumps({"checksum": checksum, "inserted": inserted}))
    else:
        print(json.dumps(await verify_manifest(get_sessionmaker(), manifest), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run())
