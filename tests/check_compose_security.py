#!/usr/bin/env python3
"""Validate security invariants in a rendered production Compose JSON document."""

import json
import sys
from typing import Any


def fail(message: str) -> None:
    raise SystemExit(f"compose security check failed: {message}")


document: dict[str, Any] = json.load(sys.stdin)
services: dict[str, dict[str, Any]] = document.get("services", {})
required = {"api", "edge", "postgres", "redis"}
mcp_services = {"business-mcp", "knowledge-mcp", "media-mcp"}
stage5_internal = {"minio", "worker-media"}
allowed = required | {"frontend"} | mcp_services | stage5_internal
if not required.issubset(services):
    fail(f"missing required services: {sorted(required - set(services))}")
if not set(services).issubset(allowed):
    fail(f"unexpected services: {sorted(set(services) - allowed)}")
if set(services) & mcp_services and not mcp_services.issubset(services):
    fail("MCP profile must include all three internal services")

total_memory = 0
for name, service in services.items():
    if name != "edge" and service.get("ports"):
        fail(f"{name} publishes host ports")
    if service.get("privileged"):
        fail(f"{name} is privileged")
    if service.get("network_mode") == "host":
        fail(f"{name} uses host networking")
    resources = service.get("deploy", {}).get("resources", {})
    total_memory += int(resources.get("limits", {}).get("memory", 0))
    devices = resources.get("reservations", {}).get("devices", [])
    if name != "worker-media" and devices:
        fail(f"{name} requests a GPU/device reservation")
    for volume in service.get("volumes", []):
        source = volume.get("source", "") if isinstance(volume, dict) else str(volume)
        target = volume.get("target", "") if isinstance(volume, dict) else str(volume)
        if "/var/run/docker.sock" in source or "/var/run/docker.sock" in target:
            fail(f"{name} mounts the Docker socket")
    if name in mcp_services | stage5_internal:
        networks = service.get("networks", {})
        if set(networks) != {"backend"}:
            fail(f"{name} must connect only to backend network")
    if name == "worker-media":
        limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
        if float(limits.get("cpus", 0)) > 8 or int(limits.get("memory", 0)) != 10 * 1024**3:
            fail("worker-media exceeds Stage 5 CPU/memory budget")
        if len(devices) != 1:
            fail("worker-media must reserve exactly one GPU")
        device = devices[0]
        if device.get("driver") != "nvidia" or device.get("capabilities") != ["gpu"]:
            fail("worker-media GPU reservation is invalid")
        device_ids = device.get("device_ids") or []
        if (
            device.get("count") is not None
            or len(device_ids) != 1
            or not str(device_ids[0]).startswith("GPU-")
        ):
            fail("worker-media must reserve only the approved physical GPU UUID")
        model_target = "/data/models/asr/large-v3-edaa852ec7e145841d8ffdb056a99866b5f0a478"
        model_mounts = [
            volume
            for volume in service.get("volumes", [])
            if isinstance(volume, dict) and volume.get("target") == model_target
        ]
        if len(model_mounts) != 1 or model_mounts[0].get("read_only") is not True:
            fail("worker-media must mount only the approved large-v3 model read-only")
        if (
            not model_mounts[0]
            .get("source", "")
            .endswith("/models/asr/large-v3-edaa852ec7e145841d8ffdb056a99866b5f0a478")
        ):
            fail("worker-media model mount source is not the approved large-v3 directory")

if total_memory >= 32 * 1024**3:
    fail(f"aggregate memory limit is {total_memory} bytes")

edge_ports = services["edge"].get("ports", [])
published = {int(port["published"]) for port in edge_ports}
if published != {80, 443}:
    fail(f"edge ports are {sorted(published)}, expected [80, 443]")

print("compose production security invariants: PASS")
