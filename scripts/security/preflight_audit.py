#!/usr/bin/env python3
"""Secret-safe public exposure preflight checks for the Stage 7 Compose stack."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ["compose.yaml", "compose.prod.yaml", "compose.public.yaml"]
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
    "CLOUDFLARE_TUNNEL_TOKEN",
}
SECRET_ALLOWLIST: dict[str, set[str]] = {
    "cloudflare_tunnel_token": {"cloudflared"},
    "deepseek_api_key": {"api"},
    "session_secret": {"api"},
    "csrf_secret": {"api"},
    "database_url": {"api", "business-mcp", "knowledge-mcp", "media-mcp", "worker-media"},
    "redis_url": {"api", "business-mcp", "knowledge-mcp", "media-mcp", "worker-media"},
    "postgres_password": {"postgres"},
    "redis_password": {"redis"},
    "minio_root_user": {"minio"},
    "minio_root_password": {"minio"},
    "minio_api_access_key": {"api"},
    "minio_api_secret_key": {"api"},
    "minio_worker_access_key": {"worker-media"},
    "minio_worker_secret_key": {"worker-media"},
}
INTERNAL_ONLY_SERVICES = {
    "api",
    "postgres",
    "redis",
    "minio",
    "business-mcp",
    "knowledge-mcp",
    "media-mcp",
    "worker-media",
}
NON_ROOT_REQUIRED = {"api", "business-mcp", "knowledge-mcp", "media-mcp", "worker-media", "minio"}
DANGEROUS_HOST_PATHS = {"/", "/home", "/root", "/etc", "/var/lib/docker"}
DANGEROUS_CAPS = {"SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE"}
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


@dataclass
class Result:
    status: str
    check_id: str
    message: str


def emit(results: list[Result], status: str, check_id: str, message: str) -> None:
    results.append(Result(status, check_id, message))


def compose_config() -> dict[str, Any]:
    command = ["docker", "compose"]
    for file_name in COMPOSE_FILES:
        command.extend(["-f", file_name])
    command.extend(["--profile", "stage5", "config", "--format", "json"])
    env = os.environ.copy()
    env.setdefault("PUBLIC_DOMAIN", "stage7.invalid")
    completed = subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True, check=True
    )
    return json.loads(completed.stdout)


def networks_of(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks") or {}
    if isinstance(networks, list):
        return set(networks)
    return set(networks.keys())


def port_is_public(port: Any) -> bool:
    host_ip = ""
    if isinstance(port, dict):
        host_ip = str(port.get("host_ip") or port.get("HostIp") or "")
    elif isinstance(port, str):
        if port.startswith("127.0.0.1:") or port.startswith("[::1]:"):
            return False
        return True
    return host_ip not in {"", "127.0.0.1", "::1", "localhost"}


def volume_source(volume: Any) -> tuple[str, str, bool]:
    if isinstance(volume, dict):
        source = str(volume.get("source") or volume.get("Source") or "")
        target = str(volume.get("target") or volume.get("Target") or "")
        read_only = bool(volume.get("read_only") or volume.get("ReadOnly"))
        return source, target, read_only
    text = str(volume)
    parts = text.split(":")
    source = parts[0] if parts else ""
    target = parts[1] if len(parts) > 1 else ""
    read_only = len(parts) > 2 and "ro" in parts[2].split(",")
    return source, target, read_only


def normalized_path(source: str) -> str:
    if not source or source.startswith("${"):
        return source
    return str((ROOT / source).resolve()) if source.startswith(".") else source


def check_compose(config: dict[str, Any]) -> list[Result]:
    results: list[Result] = []
    services: dict[str, Any] = config.get("services") or {}
    networks: dict[str, Any] = config.get("networks") or {}

    emit(
        results, "PASS", "COMPOSE-001", f"merged production config parsed; services={len(services)}"
    )
    for name, service in services.items():
        if service.get("privileged") is True:
            emit(results, "FAIL", "PRIV-001", f"{name}: privileged=true")
        if service.get("network_mode") == "host":
            emit(results, "FAIL", "NET-001", f"{name}: network_mode=host")
        for key in ("pid", "ipc", "userns_mode"):
            if service.get(key) == "host":
                emit(results, "FAIL", "NS-001", f"{name}: {key}=host")
        security_opt = set(service.get("security_opt") or [])
        if "seccomp=unconfined" in security_opt or "apparmor=unconfined" in security_opt:
            emit(results, "FAIL", "PRIV-002", f"{name}: unconfined security profile")
        if "no-new-privileges:true" not in security_opt:
            emit(results, "FAIL", "PRIV-003", f"{name}: missing no-new-privileges")
        cap_add = set(service.get("cap_add") or [])
        dangerous = cap_add & DANGEROUS_CAPS
        if dangerous:
            emit(
                results,
                "FAIL",
                "PRIV-004",
                f"{name}: dangerous cap_add={','.join(sorted(dangerous))}",
            )
        if "ALL" not in set(service.get("cap_drop") or []):
            emit(results, "WARN", "PRIV-005", f"{name}: cap_drop ALL not configured")
        if name in NON_ROOT_REQUIRED and not service.get("user"):
            emit(results, "FAIL", "USER-001", f"{name}: missing explicit non-root user")
        if not service.get("init"):
            emit(results, "WARN", "PROC-001", f"{name}: init=true missing")
        for key in ("cpus", "mem_limit", "pids_limit"):
            if key not in service:
                emit(results, "FAIL", "RESOURCE-001", f"{name}: missing {key}")
        if not service.get("ulimits"):
            emit(results, "WARN", "RESOURCE-002", f"{name}: missing ulimits")
        if not service.get("stop_grace_period"):
            emit(results, "WARN", "PROC-002", f"{name}: missing stop_grace_period")
        logging = service.get("logging") or {}
        options = logging.get("options") or {}
        if not (options.get("max-size") and options.get("max-file")):
            emit(results, "FAIL", "LOG-001", f"{name}: missing log rotation")
        for port in service.get("ports") or []:
            if name in INTERNAL_ONLY_SERVICES and port_is_public(port):
                emit(results, "FAIL", "PORT-001", f"{name}: public host port is forbidden")
            elif not port_is_public(port):
                emit(
                    results,
                    "WARN",
                    "PORT-002",
                    f"{name}: loopback host port remains an admin surface",
                )
        for volume in service.get("volumes") or []:
            source, target, read_only = volume_source(volume)
            source = normalized_path(source)
            if (
                source in DANGEROUS_HOST_PATHS
                or source.endswith("/var/run/docker.sock")
                or source.endswith("/run/docker.sock")
            ):
                emit(results, "FAIL", "MOUNT-001", f"{name}: dangerous mount {source} -> {target}")
            if source == str(ROOT) and not read_only:
                emit(results, "FAIL", "MOUNT-002", f"{name}: writable project root mount")
        for key, value in (service.get("environment") or {}).items():
            if key in SENSITIVE_ENV and value not in {None, ""}:
                emit(
                    results, "FAIL", "SECRET-001", f"{name}: sensitive environment value for {key}"
                )
        for secret in service.get("secrets") or []:
            secret_name = secret.get("source") if isinstance(secret, dict) else str(secret)
            allowed = SECRET_ALLOWLIST.get(secret_name)
            if allowed is not None and name not in allowed:
                emit(results, "FAIL", "SECRET-002", f"{name}: unexpected secret {secret_name}")

    for net_name in ("tunnel_net", "app_net", "data_net", "api_egress"):
        if net_name not in networks:
            emit(results, "FAIL", "NET-002", f"missing network {net_name}")
    for net_name in ("app_net", "data_net"):
        if not networks.get(net_name, {}).get("internal"):
            emit(results, "FAIL", "NET-003", f"{net_name} must be internal")
    for net_name in ("tunnel_net", "api_egress"):
        if networks.get(net_name, {}).get("internal"):
            emit(results, "FAIL", "NET-004", f"{net_name} must permit required egress")
    for name, expected in EXPECTED_NETWORKS.items():
        actual = networks_of(services.get(name, {}))
        if actual != expected:
            actual_text = ",".join(sorted(actual))
            expected_text = ",".join(sorted(expected))
            emit(
                results,
                "FAIL",
                "NET-005",
                f"{name}: networks={actual_text}; expected={expected_text}",
            )

    worker = services.get("worker-media") or {}
    worker_env = worker.get("environment") or {}
    devices = (
        ((worker.get("deploy") or {}).get("resources") or {})
        .get("reservations", {})
        .get("devices", [])
    )
    device_ids = {device_id for device in devices for device_id in device.get("device_ids", [])}
    if len(device_ids) != 1 or not next(iter(device_ids), "").startswith("GPU-"):
        emit(results, "FAIL", "GPU-001", "worker-media must reserve one explicit GPU UUID")
    if worker_env.get("CUDA_VISIBLE_DEVICES") != "0":
        emit(
            results, "FAIL", "GPU-002", "worker-media must expose exactly one container CUDA device"
        )
    if worker_env.get("MEDIA_WORKER_CONCURRENCY") != "1":
        emit(results, "FAIL", "GPU-003", "worker-media concurrency must be 1")

    api_env = services.get("api", {}).get("environment") or {}
    if (
        api_env.get("APP_ENV") != "production"
        or api_env.get("ENABLE_DEV_PRINCIPAL_INPUT") != "false"
    ):
        emit(
            results, "FAIL", "APP-001", "production API must disable development identity injection"
        )
    if api_env.get("ENFORCE_STAGE7_SECURITY") != "true":
        emit(results, "FAIL", "APP-002", "production API must enforce Stage 7 security")
    if api_env.get("PUBLIC_DEMO_GLOBAL_CONCURRENT_RUNS") != "1":
        emit(results, "FAIL", "DEMO-004", "public demo account concurrency must be 1")
    if not api_env.get("PUBLIC_DEMO_ACCOUNT_RUNS_PER_DAY"):
        emit(results, "FAIL", "DEMO-005", "public demo account daily run limit is missing")
    return results


def check_source_policies() -> list[Result]:
    results: list[Result] = []
    config_text = (ROOT / "backend/app/core/config.py").read_text(encoding="utf-8")
    main_text = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    runs_text = (ROOT / "backend/app/api/v1/runs.py").read_text(encoding="utf-8")
    media_text = (ROOT / "backend/app/api/v1/media.py").read_text(encoding="utf-8")
    caddy_public = (ROOT / "deploy/Caddyfile.public").read_text(encoding="utf-8")
    if "Strict-Transport-Security" in caddy_public:
        emit(
            results, "FAIL", "WEB-001", "HSTS must remain disabled before external HTTPS validation"
        )
    else:
        emit(results, "PASS", "WEB-001", "HSTS disabled in default public Caddyfile")
    for needle, check_id in (
        ("docs_url=None", "WEB-002"),
        ("openapi_url=None", "WEB-003"),
        ("public_demo_model_daily", "DEMO-001"),
        ("public_demo_session_concurrent_runs", "DEMO-002"),
        ("public_demo_account_runs_per_day", "DEMO-004"),
        ("media_task_soft_limit_seconds", "WORKER-001"),
        ("media_task_hard_limit_seconds", "WORKER-002"),
    ):
        haystack = config_text + main_text + runs_text
        if needle in haystack:
            emit(results, "PASS", check_id, f"policy present: {needle}")
        else:
            emit(results, "FAIL", check_id, f"policy missing: {needle}")
    if "forbid_public_demo" in media_text and "RL-MEDIA-001" in media_text:
        emit(results, "PASS", "DEMO-003", "public demo media/GPU task path is server-side blocked")
    else:
        emit(results, "FAIL", "DEMO-003", "public demo media/GPU task block missing")
    if re.search(
        r"shell\s*=\s*True|eval\(|exec\(",
        (ROOT / "backend/app").read_text(encoding="utf-8")
        if (ROOT / "backend/app").is_file()
        else "",
    ):
        emit(results, "WARN", "CODE-001", "manual review required for dynamic execution")
    return results


def main() -> int:
    try:
        config = compose_config()
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"BLOCKED COMPOSE-000 unable to parse merged production compose: {exc}")
        return 2
    results = check_compose(config)
    results.extend(check_source_policies())
    severity = {"PASS": 0, "WARN": 0, "BLOCKED": 1, "FAIL": 2}
    exit_code = 0
    for result in results:
        print(f"{result.status} {result.check_id} {result.message}")
        if severity[result.status] > severity["WARN"]:
            exit_code = max(exit_code, 1)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
