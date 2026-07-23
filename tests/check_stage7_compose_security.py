"""Secret-safe structural validation for the merged Stage 7 production stack."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_ENV = {
    "DATABASE_URL",
    "REDIS_URL",
    "DEEPSEEK_API_KEY",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "SESSION_SECRET",
    "CSRF_SECRET",
    "TUNNEL_TOKEN",
}

EXPECTED_NETWORKS = {
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


def main() -> int:
    command = [
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
        "config",
        "--format",
        "json",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
    config = json.loads(result.stdout)
    failures: list[str] = []
    services = config["services"]
    for name, service in services.items():
        if service.get("ports"):
            failures.append(f"PORT-001:{name}")
        for key, value in (service.get("environment") or {}).items():
            if key in SENSITIVE_ENV and value not in {None, ""}:
                failures.append(f"SECRET-ENV-001:{name}:{key}")
        if not (service.get("cpus") and service.get("mem_limit") and service.get("pids_limit")):
            failures.append(f"RESOURCE-001:{name}")
        if "no-new-privileges:true" not in (service.get("security_opt") or []):
            failures.append(f"PRIVILEGE-001:{name}")
    actual_networks = {name: set(service.get("networks", {})) for name, service in services.items()}
    if actual_networks != EXPECTED_NETWORKS:
        for name in sorted(set(actual_networks) | set(EXPECTED_NETWORKS)):
            if actual_networks.get(name) != EXPECTED_NETWORKS.get(name):
                failures.append(f"NETWORK-001:{name}")
    for name in ("app_net", "data_net"):
        if not config.get("networks", {}).get(name, {}).get("internal"):
            failures.append(f"NETWORK-INTERNAL-001:{name}")
    for name in ("tunnel_net", "api_egress"):
        if config.get("networks", {}).get(name, {}).get("internal"):
            failures.append(f"NETWORK-EGRESS-001:{name}")
    api_environment = services["api"].get("environment", {})
    if api_environment.get("PUBLIC_DEMO_GLOBAL_CONCURRENT_RUNS") != "1":
        failures.append("DEMO-CONCURRENCY-001:api")
    if not api_environment.get("PUBLIC_DEMO_ACCOUNT_RUNS_PER_DAY"):
        failures.append("DEMO-DAILY-001:api")
    if not api_environment.get("PUBLIC_DEMO_MODEL_DAILY"):
        failures.append("DEMO-MODEL-001:api")
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print(
        json.dumps(
            {
                "services_checked": len(services),
                "published_ports": 0,
                "sensitive_environment_values": 0,
                "route": "cloudflare_tunnel",
                "network_matrix": "exact",
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
