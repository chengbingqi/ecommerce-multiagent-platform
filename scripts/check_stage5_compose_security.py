import json
import os
import subprocess
import sys
from decimal import Decimal


def load_config(override: str) -> dict:
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            os.getenv("COMPOSE_PROJECT_NAME", "ecommerce-multiagent-stage5"),
            "-f",
            "compose.yaml",
            "-f",
            override,
            "--profile",
            "stage5",
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "DATA_ROOT": os.getenv("DATA_ROOT", "/tmp/ecommerce-multiagent-compose-check"),
            "MEDIA_GPU_DEVICE_ID": os.getenv("MEDIA_GPU_DEVICE_ID", "GPU-EXAMPLE"),
        },
    )
    return json.loads(completed.stdout)


def published_ports(service: dict) -> set[tuple[str, int, int]]:
    return {
        (
            str(port.get("host_ip") or ""),
            int(port["published"]),
            int(port["target"]),
        )
        for port in service.get("ports", [])
    }


def check(override: str) -> dict[str, object]:
    config = load_config(override)
    services = config["services"]
    internal = {
        "postgres",
        "redis",
        "business-mcp",
        "knowledge-mcp",
        "media-mcp",
        "minio",
        "worker-media",
    }
    if override == "compose.prod.yaml":
        internal.add("api")
    assert not any(published_ports(services[name]) for name in internal)
    if override == "compose.prod.yaml":
        # Stage 7 selected Cloudflare Tunnel as the only production route. The
        # host therefore publishes no project port; cloudflared reaches edge on
        # the isolated ingress network when compose.public.yaml is added.
        assert not any(published_ports(service) for service in services.values())
        assert not published_ports(services.get("frontend", {}))
    else:
        allowed = {
            ("127.0.0.1", 5173),
            ("127.0.0.1", 8000),
            ("127.0.0.1", 8080),
            ("127.0.0.1", 8443),
        }
        actual = {
            (host, published)
            for service in services.values()
            for host, published, _ in published_ports(service)
        }
        assert actual <= allowed
        assert all(host == "127.0.0.1" for host, _ in actual)
    for name, service in services.items():
        assert not service.get("privileged", False)
        assert service.get("network_mode") != "host"
        assert not any(
            "/var/run/docker.sock" in str(volume) for volume in service.get("volumes", [])
        )
        devices = (
            service.get("deploy", {})
            .get("resources", {})
            .get("reservations", {})
            .get("devices", [])
        )
        if name != "worker-media":
            assert not any("gpu" in device.get("capabilities", []) for device in devices)
    internal_network = "data_net" if override == "compose.prod.yaml" else "backend"
    for name in {"business-mcp", "knowledge-mcp", "media-mcp", "minio", "worker-media"}:
        assert set(services[name].get("networks", {})) == {internal_network}
    worker = services["worker-media"]
    worker_environment = worker["environment"]
    revision = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
    assert worker_environment["ASR_MODEL"] == "Systran/faster-whisper-large-v3"
    assert worker_environment["ASR_MODEL_ID"] == "large-v3"
    assert worker_environment["ASR_MODEL_VERSION"] == revision
    assert worker_environment["ASR_MODEL_REVISION"] == revision
    assert worker_environment["ASR_MODEL_PATH"].endswith(f"large-v3-{revision}")
    assert worker_environment["ASR_DEVICE"] == "cuda"
    assert worker_environment["ASR_DEVICE_INDEX"] == "0"
    assert worker_environment["ASR_COMPUTE_TYPE"] == "float16"
    assert worker_environment["MEDIA_WORKER_CONCURRENCY"] == "1"
    assert worker_environment["HF_HUB_OFFLINE"] == "1"
    assert worker_environment["CUDA_VISIBLE_DEVICES"] == "0"
    devices = (
        worker.get("deploy", {}).get("resources", {}).get("reservations", {}).get("devices", [])
    )
    assert len(devices) == 1
    device = devices[0]
    assert device.get("driver") == "nvidia"
    assert device.get("capabilities") == ["gpu"]
    assert device.get("device_ids") == [os.getenv("MEDIA_GPU_DEVICE_ID", "GPU-EXAMPLE")]
    assert "count" not in device
    model_mount = next(
        volume
        for volume in worker.get("volumes", [])
        if volume.get("target")
        == "/data/models/asr/large-v3-edaa852ec7e145841d8ffdb056a99866b5f0a478"
    )
    assert model_mount.get("read_only") is True
    assert model_mount.get("source", "").endswith(
        "/models/asr/large-v3-edaa852ec7e145841d8ffdb056a99866b5f0a478"
    )
    memory_bytes = sum(
        Decimal(
            str(service.get("deploy", {}).get("resources", {}).get("limits", {}).get("memory", 0))
        )
        for service in services.values()
    )
    assert memory_bytes < Decimal(32 * 1024**3)
    return {
        "override": override,
        "services": sorted(services),
        "published_services": sorted(
            name for name, service in services.items() if published_ports(service)
        ),
        "memory_limit_gib": str((memory_bytes / Decimal(1024**3)).quantize(Decimal("0.01"))),
        "security_invariants": "passed",
    }


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"compose.dev.yaml", "compose.prod.yaml"}:
        raise SystemExit(
            "usage: check_stage5_compose_security.py compose.dev.yaml|compose.prod.yaml"
        )
    print(json.dumps(check(sys.argv[1]), sort_keys=True))
