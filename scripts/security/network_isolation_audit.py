#!/usr/bin/env python3
"""Validate the Stage 7 production network matrix without using project data."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = [
    "docker",
    "compose",
    "-f",
    "compose.yaml",
    "-f",
    "compose.prod.yaml",
    "-f",
    "compose.public.yaml",
    "--profile",
    "stage5",
]
EXPECTED = {
    "api": {"api_egress", "app_net", "data_net"},
    "business-mcp": {"data_net"},
    "cloudflared": {"tunnel_net"},
    "edge": {"app_net", "tunnel_net"},
    "knowledge-mcp": {"data_net"},
    "media-mcp": {"data_net"},
    "minio": {"data_net"},
    "postgres": {"data_net"},
    "redis": {"data_net"},
    "worker-media": {"data_net"},
}
PROBE_IMAGE = os.getenv("NETWORK_AUDIT_PROBE_IMAGE", "ecommerce-multiagent-stage5-api")


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def merged_config() -> dict[str, Any]:
    return json.loads(run([*COMPOSE, "config", "--format", "json"]).stdout)


def static_check(config: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    services = config.get("services", {})
    actual = {
        name: set((service.get("networks") or {}).keys()) for name, service in services.items()
    }
    if actual != EXPECTED:
        for name in sorted(set(actual) | set(EXPECTED)):
            if actual.get(name) != EXPECTED.get(name):
                failures.append(
                    f"{name}: actual={sorted(actual.get(name, set()))} "
                    f"expected={sorted(EXPECTED.get(name, set()))}"
                )
    for name in (
        "postgres",
        "redis",
        "minio",
        "api",
        "business-mcp",
        "knowledge-mcp",
        "media-mcp",
        "worker-media",
    ):
        if services.get(name, {}).get("ports"):
            failures.append(f"{name}: production ports must be empty")
    return failures


def exec_python(container: str, source: str) -> subprocess.CompletedProcess[str]:
    return run(["docker", "exec", container, "python", "-c", source], check=False)


def can_resolve(container: str, hostname: str) -> bool:
    source = f"import socket; socket.getaddrinfo({hostname!r}, None)"
    return exec_python(container, source).returncode == 0


def can_http(container: str, hostname: str) -> bool:
    source = (
        "import urllib.request; "
        f"assert urllib.request.urlopen('http://{hostname}:8080', timeout=2).status == 200"
    )
    return exec_python(container, source).returncode == 0


def runtime_check() -> list[str]:
    if run(["docker", "image", "inspect", PROBE_IMAGE], check=False).returncode:
        return [f"local probe image is missing: {PROBE_IMAGE}"]

    suffix = uuid.uuid4().hex[:10]
    prefix = f"stage7-net-audit-{suffix}"
    networks = {
        "tunnel_net": f"{prefix}-tunnel",
        "app_net": f"{prefix}-app",
        "data_net": f"{prefix}-data",
        "api_egress": f"{prefix}-egress",
    }
    containers = {name: f"{prefix}-{name.replace('_', '-')}" for name in EXPECTED}
    failures: list[str] = []
    created_containers: list[str] = []
    created_networks: list[str] = []
    try:
        for logical, name in networks.items():
            command = ["docker", "network", "create"]
            if logical in {"app_net", "data_net"}:
                command.append("--internal")
            command.extend(["--label", "ecommerce.stage7.network-audit=true", name])
            run(command)
            created_networks.append(name)

        for service, memberships in EXPECTED.items():
            first = sorted(memberships)[0]
            command = [
                "docker",
                "run",
                "-d",
                "--name",
                containers[service],
                "--label",
                "ecommerce.stage7.network-audit=true",
                "--network",
                networks[first],
                "--network-alias",
                service,
                PROBE_IMAGE,
                "python",
                "-m",
                "http.server",
                "8080",
                "--bind",
                "0.0.0.0",
            ]
            run(command)
            created_containers.append(containers[service])
            for logical in sorted(memberships - {first}):
                run(
                    [
                        "docker",
                        "network",
                        "connect",
                        "--alias",
                        service,
                        networks[logical],
                        containers[service],
                    ]
                )

        cloud = containers["cloudflared"]
        edge = containers["edge"]
        if not can_http(cloud, "edge"):
            failures.append("cloudflared context cannot reach edge")
        for target in (
            "api",
            "postgres",
            "redis",
            "minio",
            "business-mcp",
            "knowledge-mcp",
            "media-mcp",
            "worker-media",
        ):
            if can_resolve(cloud, target):
                failures.append(f"cloudflared context unexpectedly resolves {target}")
        if not can_http(edge, "api"):
            failures.append("edge context cannot reach api")
        for target in ("postgres", "redis", "minio"):
            if can_resolve(edge, target):
                failures.append(f"edge context unexpectedly resolves {target}")
        for target in (
            "postgres",
            "redis",
            "minio",
            "business-mcp",
            "knowledge-mcp",
            "media-mcp",
            "worker-media",
        ):
            if not can_resolve(containers["api"], target):
                failures.append(f"api context cannot resolve required data service {target}")

        for service, expected in EXPECTED.items():
            inspected = json.loads(run(["docker", "inspect", containers[service]]).stdout)[0]
            attached_names = set(inspected["NetworkSettings"]["Networks"])
            actual = {logical for logical, name in networks.items() if name in attached_names}
            if actual != expected:
                failures.append(
                    f"runtime membership mismatch for {service}: "
                    f"actual={sorted(actual)} expected={sorted(expected)}"
                )
    finally:
        for container in reversed(created_containers):
            run(["docker", "rm", "-f", container], check=False)
        for network in reversed(created_networks):
            run(["docker", "network", "rm", network], check=False)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="create short-lived, volume-free diagnostic containers and networks",
    )
    args = parser.parse_args()
    failures = static_check(merged_config())
    if not failures:
        print("PASS NET-STATIC exact production network matrix and zero internal ports")
    if args.runtime:
        runtime_failures = runtime_check()
        failures.extend(runtime_failures)
        if not runtime_failures:
            print("PASS NET-RUNTIME cloudflared->edge and edge->api only; data DNS isolated")
            print("PASS NET-CLEANUP temporary diagnostic containers and networks removed")
    for failure in failures:
        print(f"FAIL NET-AUDIT {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
