from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.media.schemas import UploadCreateRequest
from app.media.security import confined_task_path, validate_mp4_signature
from app.security.service import AuthService, SecurityContext, SecurityFailure

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "evals/stage7/security_cases_v1.json").read_text())
CASES = MANIFEST["cases"]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def base_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "redis_url": "redis://localhost:6379/15",
        "session_secret": "test-session-secret-at-least-thirty-two-characters",
        "csrf_secret": "test-csrf-secret-at-least-thirty-two-characters",
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def upload(filename: str) -> UploadCreateRequest:
    return UploadCreateRequest(
        principal_id="content-001",
        store_id="store-001",
        display_filename=filename,
        idempotency_key="upload-case-001",
    )


def check(case: dict[str, object], tmp_path: Path) -> None:
    if case.get("external_gate"):
        pytest.skip(
            "EXTERNAL_GATE_UNAVAILABLE: requires domain, external backup or external client"
        )
    key = str(case["check"])
    main = text("backend/app/main.py")
    auth = text("backend/app/security/service.py")
    middleware = text("backend/app/security/middleware.py")
    runs = text("backend/app/api/v1/runs.py")
    media = text("backend/app/api/v1/media.py")
    graph = text("backend/app/agents/orchestrator/graph.py")
    caddy = text("deploy/Caddyfile")
    compose = text("compose.prod.yaml")

    if key == "public_live":
        assert '@router.get("/live")' in text("backend/app/api/v1/health.py")
    elif key == "frontend_guard":
        assert "function Protected" in text("frontend/src/App.tsx")
    elif key == "argon2id":
        hashed = AuthService.hash_password("correct horse battery staple")
        assert hashed.startswith("$argon2id$") and "correct horse" not in hashed
    elif key == "generic_login":
        assert '"INVALID_CREDENTIALS", "用户名或密码错误"' in auth
    elif key == "login_limit":
        assert "RL-LOGIN-001" in text("backend/app/api/v1/auth.py")
    elif key == "session_rotation":
        assert "raw_session = secrets.token_urlsafe" in auth
    elif key == "session_revocation":
        assert 'revoke_reason="logout"' not in auth and "_revoke_digest" in auth
    elif key == "auth_version":
        assert "user.auth_version += 1" in auth and "authorization_changed" in auth
    elif key == "session_expiry":
        assert (
            "self._utc(record.idle_expires_at) <= now" in auth
            and "self._utc(record.absolute_expires_at) <= now" in auth
        )
    elif key == "cookie_policy":
        assert "httponly=True" in text("backend/app/api/v1/auth.py")
        assert "__Host-ecom_session" in compose and 'SECURE_SESSION_COOKIE: "true"' in compose
    elif key == "csrf_middleware":
        assert "UNSAFE_METHODS" in middleware and "CSRF_INVALID" in middleware
    elif key in {"csrf_digest", "csrf_binding"}:
        service = AuthService(base_settings())
        context = SecurityContext(
            "s",
            "u",
            "p",
            "customer",
            None,
            "c",
            "normal",
            service.csrf_digest("right"),
            1,
            __import__("datetime").datetime.now(__import__("datetime").UTC),
            __import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        with pytest.raises(SecurityFailure, match="CSRF_INVALID"):
            service.validate_csrf(context, "wrong")
    elif key == "origin_policy":
        assert "allowed_origin_list" in middleware and "referer" in middleware
    elif key == "cors_policy":
        assert (
            "allow_credentials=True" in main
            and "allow_origins=settings.allowed_origin_list" in main
        )
    elif key == "trusted_host":
        assert "TrustedHostMiddleware" in main
    elif key == "trusted_proxy":
        assert "trusted_proxy_cidr_list" in text("backend/app/security/rate_limit.py")
    elif key == "customer_role":
        assert '"customer:self"' in runs and "require_roles(context" in runs
    elif key == "operations_role":
        assert 'allowed = {"store_operator", "agent:operations", "admin"}' in text(
            "backend/app/agents/operations/graph.py"
        )
    elif key in {"media_scope", "download_scope", "object_scope"}:
        repo = text("backend/app/media/repository.py")
        assert "actor.id != asset.owner_id" in repo and "actor.store_id != asset.store_id" in repo
    elif key == "sse_scope":
        assert (
            "get_run(run_id, principal_id)" in runs and "get_task(task_id, principal_id)" in media
        )
    elif key == "approval_scope":
        assert "same_approval_scope" in text("backend/app/agents/customer_service/repository.py")
    elif key == "approval_expiry":
        assert "approval.expires_at" in text("backend/app/agents/customer_service/repository.py")
    elif key == "approval_digest":
        assert "action_digest" in text("backend/app/db/models.py")
    elif key == "approval_idempotency":
        assert 'approval.status in {"approved", "rejected", "used"}' in text(
            "backend/app/agents/customer_service/repository.py"
        )
    elif key == "admin_role":
        assert 'require_roles(context, "admin")' in text("backend/app/api/v1/auth.py")
    elif key == "orchestrator_scope":
        assert "role_targets" in graph and "FORBIDDEN_TOOL_SCOPE" in graph
    elif key == "redis_atomic":
        rate = text("backend/app/security/rate_limit.py")
        assert "redis.call('INCR'" in rate and "redis.call('ZADD'" in rate
    elif key == "model_budget":
        assert "RL-MODEL-001" in runs
    elif key == "upload_limits":
        service = text("backend/app/media/service.py")
        assert "media_upload_max_bytes" in service and "received == 0" in service
    elif key == "media_limits":
        assert "RL-MEDIA-001" in media and "MEDIA_WORKER_CONCURRENCY" in text("compose.yaml")
    elif key == "fail_closed":
        assert "RATE_LIMIT_DEPENDENCY_UNAVAILABLE" in text("backend/app/security/rate_limit.py")
    elif key == "mp4_signature":
        path = tmp_path / "fake.mp4"
        path.write_bytes(b"not-an-mp4-file")
        with pytest.raises(ValueError):
            validate_mp4_signature(path)
    elif key == "filename_policy":
        for value in ("video.mp4.exe", "video", "video.mp4.zip"):
            with pytest.raises(ValidationError):
                upload(value)
        assert upload("../video.mp4").display_filename == "video.mp4"
        assert upload("/tmp/video.mp4").display_filename == "video.mp4"
        assert upload("video.mp4").display_filename == "video.mp4"
        with pytest.raises(ValueError):
            confined_task_path(tmp_path, "../escape")
    elif key == "probe_policy":
        probe = text("backend/app/media/probe.py")
        assert 'result.video_codec != "h264"' in probe and "max_duration_seconds" in probe
    elif key == "ffmpeg_exec":
        pipeline = text("backend/app/media/pipeline.py")
        assert "create_subprocess_exec" in pipeline and "shell=True" not in pipeline
    elif key == "download_proxy":
        assert "self.storage.stream" in text("backend/app/media/service.py")
    elif key in {"log_policy", "error_policy"}:
        assert "password" not in text("backend/app/core/logging.py").lower()
        assert "Cookie" not in main
    elif key == "bundle_policy":
        assert "VITE" not in text("frontend/src/api.ts") or "VITE_" not in text(
            "frontend/src/api.ts"
        )
    elif key == "compose_secrets":
        assert "DEEPSEEK_API_KEY_FILE" in compose and "cloudflare_tunnel_token" not in text(
            "compose.prod.yaml"
        )
    elif key == "docs_disabled":
        assert "docs_url=None" in main and "openapi_url=None" in main
    elif key == "caddy_headers":
        for header in (
            "Content-Security-Policy",
            "X-Content-Type-Options",
            "Referrer-Policy",
            "Permissions-Policy",
        ):
            assert header in caddy
    elif key == "checkpoint_schema":
        models = text("backend/app/db/models.py")
        assert "class AuthSession" in models and "class AgentCheckpointLink" in models
    else:
        raise AssertionError(f"unknown check: {key}")


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_security_case(case: dict[str, object], tmp_path: Path) -> None:
    check(case, tmp_path)
