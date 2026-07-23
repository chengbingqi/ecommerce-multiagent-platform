from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from redis.exceptions import ConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.customer_service.repository import RepositoryFailure
from app.agents.customer_service.schemas import AgentRunStatus
from app.agents.orchestrator.repository import OrchestratorRepository
from app.api.v1.auth import update_user_authorization
from app.api.v1.demo import demo_orders
from app.api.v1.media import (
    ClipTaskApiCreateRequest,
    UploadApiCreateRequest,
    create_clip_task,
    create_upload,
)
from app.api.v1.runs import (
    ApprovalApiDecisionRequest,
    CustomerServiceApiRunRequest,
    create_run,
    decide_approval,
)
from app.core.config import Settings
from app.db.base import Base
from app.db.models import AgentEvent, AgentRun, AuthSession, AuthUser, Principal
from app.security.demo import (
    DEMO_ACCOUNT_TYPE,
    reject_demo_write_text,
    require_demo_order,
)
from app.security.rate_limit import RateLimiter, RateLimitFailure, RateRule
from app.security.schemas import AuthorizationUpdateRequest
from app.security.service import AuthService, SecurityContext
from app.seed.generator import load_manifest
from app.seed.service import seed_manifest

ROOT = Path(__file__).resolve().parents[2]
STAGE2_MANIFEST = ROOT / "data/manifests/stage2_base_v1.json"
DEMO_MANIFEST = ROOT / "data/manifests/public_demo_orders_v1.json"
_BOOTSTRAP_SPEC = importlib.util.spec_from_file_location(
    "bootstrap_demo_user", ROOT / "backend/scripts/bootstrap_demo_user.py"
)
assert _BOOTSTRAP_SPEC and _BOOTSTRAP_SPEC.loader
bootstrap_demo_user = importlib.util.module_from_spec(_BOOTSTRAP_SPEC)
_BOOTSTRAP_SPEC.loader.exec_module(bootstrap_demo_user)
STORE_1 = "b39e17d7-c6aa-5aaf-adfc-b84c80d0f8f1"
STORE_2 = "8dd8607c-daf0-5de6-8e21-d0d227f48bc5"


@pytest_asyncio.fixture
async def demo_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'public-demo.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await seed_manifest(factory, load_manifest(STAGE2_MANIFEST))
    monkeypatch.setattr("app.api.v1.demo.get_sessionmaker", lambda: factory)
    monkeypatch.setattr(bootstrap_demo_user, "get_sessionmaker", lambda: factory)
    yield factory
    await engine.dispose()


def demo_context(session_digest: str = "a" * 64) -> SecurityContext:
    now = datetime.now(UTC)
    return SecurityContext(
        session_digest=session_digest,
        user_id="demo-user-001",
        principal_id="public-demo-001",
        role="store_operator",
        store_id=STORE_1,
        customer_ref=None,
        account_type=DEMO_ACCOUNT_TYPE,
        csrf_digest="b" * 64,
        auth_version=1,
        idle_expires_at=now,
        absolute_expires_at=now,
    )


def test_public_demo_manifest_is_store1_only_and_stable() -> None:
    payload = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
    assert payload["allowed_store_ids"] == [STORE_1]
    assert payload["operations_store_id"] == STORE_1
    assert 3 <= len(payload["orders"]) <= 6
    assert [row["demo_case_id"] for row in payload["orders"]] == [
        "normal_order",
        "normal_logistics",
        "logistics_delay",
        "refund_eligible",
        "refund_ineligible",
    ]
    assert {row["store_id"] for row in payload["orders"]} == {STORE_1}
    assert len({row["order_id"] for row in payload["orders"]}) == len(payload["orders"])


@pytest.mark.asyncio
async def test_bootstrap_demo_user_create_and_idempotency(
    demo_factory: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file = tmp_path / "demo-password"
    password_file.write_text("Correct-public-demo-password-9!", encoding="utf-8")
    password_file.chmod(0o600)
    monkeypatch.setenv("DEMO_BOOTSTRAP_PASSWORD_FILE", str(password_file))

    created = await bootstrap_demo_user.create("demo_user", "public-demo-001", "store-001")
    duplicate = await bootstrap_demo_user.create("demo_user", "public-demo-001", "store-001")

    assert created["status"] == "CREATED"
    assert created["principal_id"] == "public-demo-001"
    assert created["account_type"] == DEMO_ACCOUNT_TYPE
    assert created["role"] == "store_operator"
    assert created["store_scope"] == STORE_1
    assert duplicate["status"] == "EXISTS"
    async with demo_factory() as session:
        users = (
            await session.scalars(
                select(AuthUser).where(AuthUser.username_normalized == "demo_user")
            )
        ).all()
        assert len(users) == 1
        principal = await session.get(Principal, "public-demo-001")
        assert principal is not None
        assert principal.role == "store_operator"
        assert principal.store_id == STORE_1
        assert principal.customer_ref is None
        assert principal.active
        assert "Correct-public-demo-password-9!" not in users[0].password_hash
        assert users[0].password_hash.startswith("$argon2id$")


@pytest.mark.asyncio
async def test_bootstrap_demo_user_flushes_principal_before_auth_user(
    demo_factory: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file = tmp_path / "demo-password"
    password_file.write_text("Correct-public-demo-password-9!", encoding="utf-8")
    password_file.chmod(0o600)
    monkeypatch.setenv("DEMO_BOOTSTRAP_PASSWORD_FILE", str(password_file))
    calls = 0
    original_flush = AsyncSession.flush

    async def counted_flush(self: AsyncSession, *args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        await original_flush(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", counted_flush)

    await bootstrap_demo_user.create("demo_user", "public-demo-001", "store-001")

    assert calls >= 1


@pytest.mark.asyncio
async def test_bootstrap_demo_user_recovers_when_principal_exists_without_user(
    demo_factory: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with demo_factory() as session, session.begin():
        session.add(
            Principal(id="public-demo-001", role="store_operator", store_id=STORE_1, active=True)
        )
    password_file = tmp_path / "demo-password"
    password_file.write_text("Correct-public-demo-password-9!", encoding="utf-8")
    password_file.chmod(0o600)
    monkeypatch.setenv("DEMO_BOOTSTRAP_PASSWORD_FILE", str(password_file))

    created = await bootstrap_demo_user.create("demo_user", "public-demo-001", "store-001")

    assert created["status"] == "CREATED"
    async with demo_factory() as session:
        users = (
            await session.scalars(
                select(AuthUser).where(AuthUser.principal_id == "public-demo-001")
            )
        ).all()
        assert len(users) == 1


@pytest.mark.asyncio
async def test_bootstrap_demo_user_rolls_back_principal_when_user_creation_fails(
    demo_factory: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file = tmp_path / "demo-password"
    password_file.write_text("Correct-public-demo-password-9!", encoding="utf-8")
    password_file.chmod(0o600)
    monkeypatch.setenv("DEMO_BOOTSTRAP_PASSWORD_FILE", str(password_file))
    monkeypatch.setattr(
        bootstrap_demo_user.AuthService,
        "hash_password",
        lambda _password: (_ for _ in ()).throw(RuntimeError("hash failed")),
    )

    with pytest.raises(RuntimeError, match="hash failed"):
        await bootstrap_demo_user.create("demo_user", "public-demo-001", "store-001")

    async with demo_factory() as session:
        assert await session.get(Principal, "public-demo-001") is None
        assert (
            await session.scalar(
                select(AuthUser).where(AuthUser.username_normalized == "demo_user")
            )
            is None
        )


@pytest.mark.asyncio
async def test_bootstrap_demo_user_rejects_username_and_principal_conflicts(
    demo_factory: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    async with demo_factory() as session, session.begin():
        session.add(Principal(id="other-principal", role="store_operator", store_id=STORE_1))
        session.add(Principal(id="public-demo-001", role="store_operator", store_id=STORE_1))
        session.add(
            AuthUser(
                id="other-user",
                principal_id="other-principal",
                username_normalized="demo_user",
                password_hash=AuthService.hash_password("Correct-public-demo-password-9!"),
                account_type=DEMO_ACCOUNT_TYPE,
                auth_version=1,
                failed_attempts=0,
                locked_until=None,
                active=True,
                password_changed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    password_file = tmp_path / "demo-password"
    password_file.write_text("Correct-public-demo-password-9!", encoding="utf-8")
    password_file.chmod(0o600)
    monkeypatch.setenv("DEMO_BOOTSTRAP_PASSWORD_FILE", str(password_file))

    with pytest.raises(bootstrap_demo_user.DemoBootstrapError) as username_conflict:
        await bootstrap_demo_user.create("demo_user", "public-demo-001", "store-001")
    assert username_conflict.value.code == "USERNAME_CONFLICT"

    async with demo_factory() as session, session.begin():
        existing = await session.scalar(
            select(AuthUser).where(AuthUser.username_normalized == "demo_user")
        )
        assert existing is not None
        await session.delete(existing)
        session.add(
            AuthUser(
                id="demo-principal-other-user",
                principal_id="public-demo-001",
                username_normalized="other_demo",
                password_hash=AuthService.hash_password("Correct-public-demo-password-9!"),
                account_type=DEMO_ACCOUNT_TYPE,
                auth_version=1,
                failed_attempts=0,
                locked_until=None,
                active=True,
                password_changed_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    with pytest.raises(bootstrap_demo_user.DemoBootstrapError) as principal_conflict:
        await bootstrap_demo_user.create("demo_user", "public-demo-001", "store-001")
    assert principal_conflict.value.code == "PRINCIPAL_CONFLICT"


@pytest.mark.asyncio
async def test_bootstrap_demo_user_rejects_invalid_demo_principal(
    demo_factory: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with demo_factory() as session, session.begin():
        session.add(Principal(id="public-demo-001", role="admin", store_id=None, active=True))
    password_file = tmp_path / "demo-password"
    password_file.write_text("Correct-public-demo-password-9!", encoding="utf-8")
    password_file.chmod(0o600)
    monkeypatch.setenv("DEMO_BOOTSTRAP_PASSWORD_FILE", str(password_file))

    with pytest.raises(bootstrap_demo_user.DemoBootstrapError) as captured:
        await bootstrap_demo_user.create("demo_user", "public-demo-001", "store-001")
    assert captured.value.code == "PRINCIPAL_INVALID"


def test_bootstrap_demo_user_main_safe_error_does_not_print_hash_or_sql_parameters(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["bootstrap_demo_user.py", "--username", "demo_user"])

    async def fail_create(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        raise bootstrap_demo_user.DemoBootstrapError(
            "INTEGRITY_ERROR", "公共演示账号创建失败：数据库约束检查未通过，事务已回滚。"
        )

    monkeypatch.setattr(bootstrap_demo_user, "create", fail_create)

    with pytest.raises(SystemExit) as captured:
        bootstrap_demo_user.main()

    output = f"{capsys.readouterr().out}\n{captured.value}"
    assert "password_hash" not in output
    assert "$argon2id$" not in output
    assert "parameters" not in output.lower()
    assert "INSERT INTO" not in output


@pytest.mark.asyncio
async def test_reset_password_revokes_demo_sessions(
    demo_factory: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_file = tmp_path / "first"
    first_file.write_text("Correct-public-demo-password-9!", encoding="utf-8")
    first_file.chmod(0o600)
    monkeypatch.setenv("DEMO_BOOTSTRAP_PASSWORD_FILE", str(first_file))
    await bootstrap_demo_user.create("demo_user", "public-demo-001", "store-001")
    async with demo_factory() as session, session.begin():
        user = await session.scalar(
            select(AuthUser).where(AuthUser.username_normalized == "demo_user")
        )
        assert user is not None
        session.add(
            AuthSession(
                token_digest="a" * 64,
                user_id=user.id,
                csrf_digest="b" * 64,
                auth_version=user.auth_version,
                created_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                idle_expires_at=datetime.now(UTC),
                absolute_expires_at=datetime.now(UTC),
                revoked_at=None,
                revoke_reason=None,
            )
        )

    second_file = tmp_path / "second"
    second_file.write_text("Changed-public-demo-password-9!", encoding="utf-8")
    second_file.chmod(0o600)
    monkeypatch.setenv("DEMO_BOOTSTRAP_PASSWORD_FILE", str(second_file))
    result = await bootstrap_demo_user.reset_password("demo_user")

    assert result["status"] == "RESET_PASSWORD"
    async with demo_factory() as session:
        session_row = await session.get(AuthSession, "a" * 64)
        assert session_row is not None
        assert session_row.revoked_at is not None
        assert session_row.revoke_reason == "public_demo_password_reset"


@pytest.mark.asyncio
async def test_bootstrap_status_validates_demo_scope_without_password(
    demo_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_file = tmp_path / "demo-password"
    password_file.write_text("Correct-public-demo-password-9!", encoding="utf-8")
    password_file.chmod(0o600)
    monkeypatch.setenv("DEMO_BOOTSTRAP_PASSWORD_FILE", str(password_file))
    await bootstrap_demo_user.create("demo_user", "public-demo-001", "store-001")

    result = await bootstrap_demo_user.status("demo_user", "store-001")

    assert result == {
        "status": "VALID",
        "username": "demo_user",
        "principal_id": "public-demo-001",
        "account_type": DEMO_ACCOUNT_TYPE,
        "role": "store_operator",
        "store_scope": STORE_1,
        "enabled": "True",
    }


@pytest.mark.asyncio
async def test_demo_orders_endpoint_returns_only_safe_store1_fields(
    demo_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with demo_factory() as session, session.begin():
        session.add(
            Principal(id="public-demo-001", role="store_operator", store_id=STORE_1, active=True)
        )
    response = await demo_orders(demo_context())
    payload = response.model_dump(mode="json")

    assert response.account_type == DEMO_ACCOUNT_TYPE
    assert [order.demo_scenario for order in response.orders] == [
        "normal_order",
        "normal_logistics",
        "logistics_delay",
        "refund_eligible",
        "refund_ineligible",
    ]
    assert [order.demo_case_id for order in response.orders] == [
        "normal_order",
        "normal_logistics",
        "logistics_delay",
        "refund_eligible",
        "refund_ineligible",
    ]
    assert all(order.description for order in response.orders)
    assert {order.store_id for order in response.orders} == {STORE_1}
    assert all(order.masked_order_id.startswith("****") for order in response.orders)
    forbidden_text = json.dumps(payload, ensure_ascii=False).lower()
    for sensitive in ("customer_ref", "phone", "address", "password", "token", "csrf"):
        assert sensitive not in forbidden_text


def test_demo_order_and_text_policy_allows_read_only_refund_but_blocks_writes() -> None:
    context = demo_context()
    reject_demo_write_text(context, "查询该订单是否符合退款条件，但不要发起退款。")
    with pytest.raises(HTTPException) as captured:
        reject_demo_write_text(context, "帮我申请退款")
    assert captured.value.status_code == 403
    assert "只读查询" in captured.value.detail["message"]
    with pytest.raises(HTTPException):
        require_demo_order(context, "5edc0ad9-1431-5009-a193-9f0ce95302a4")


@pytest.mark.asyncio
async def test_public_demo_session_isolation_for_run_and_sse_events(
    demo_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    output = {
        "run_id": "run-demo-session",
        "thread_id": "thread-demo-session",
        "trace_id": "trace-demo-session",
        "overall_status": "SUCCEEDED",
        "route": OrchestratorRepository.empty_route().model_dump(mode="json"),
        "plan": [],
        "subtasks": [],
        "customer_service_result": None,
        "operations_result": None,
        "media_result": None,
        "evidence_refs": [],
        "partial_failures": [],
        "warnings": [],
        "need_human": False,
        "completion_rule": "ALL_REQUIRED",
        "error": None,
        "versions": {},
    }
    async with demo_factory() as session, session.begin():
        session.add(
            Principal(id="public-demo-001", role="store_operator", store_id=STORE_1, active=True)
        )
        session.add(
            AuthUser(
                id="demo-user-001",
                principal_id="public-demo-001",
                username_normalized="demo_user",
                password_hash=AuthService.hash_password("Correct-public-demo-password-9!"),
                account_type=DEMO_ACCOUNT_TYPE,
                auth_version=1,
                failed_attempts=0,
                locked_until=None,
                active=True,
                password_changed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            AgentRun(
                run_id="run-demo-session",
                thread_id="thread-demo-session",
                principal_id="public-demo-001",
                auth_session_digest="a" * 64,
                agent_type="orchestrator",
                status=AgentRunStatus.SUCCEEDED.value,
                input_summary={},
                output_summary=output,
                need_human=False,
                error_code=None,
                approval_id=None,
                trace_id="trace-demo-session",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            AgentEvent(
                id="event-demo-001",
                run_id="run-demo-session",
                event_type="run_state",
                status=AgentRunStatus.SUCCEEDED.value,
                payload_summary={},
                trace_id="trace-demo-session",
                created_at=now,
            )
        )

    repository = OrchestratorRepository(demo_factory)
    assert (await repository.get_output("run-demo-session", "public-demo-001", "a" * 64)).run_id
    assert (
        len(await repository.list_events("run-demo-session", "public-demo-001", None, "a" * 64))
        == 1
    )
    with pytest.raises(RepositoryFailure) as wrong_run:
        await repository.get_output("run-demo-session", "public-demo-001", "c" * 64)
    assert wrong_run.value.code == "FORBIDDEN"
    with pytest.raises(RepositoryFailure) as wrong_events:
        await repository.list_events("run-demo-session", "public-demo-001", None, "c" * 64)
    assert wrong_events.value.code == "FORBIDDEN"


class FailingRedis:
    async def eval(self, *_args):
        raise ConnectionError("unavailable")


@pytest.mark.asyncio
async def test_demo_high_cost_rate_limit_fails_closed_on_redis_failure() -> None:
    limiter = RateLimiter(
        Settings(
            _env_file=None,
            app_env="test",
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://localhost:6379/15",
        ),
        FailingRedis(),  # type: ignore[arg-type]
    )
    with pytest.raises(RateLimitFailure) as captured:
        await limiter.enforce(RateRule("RL-DEMO-IP-DAY-001", 1, 86_400), "127.0.0.1")
    assert captured.value.unavailable
    assert captured.value.code == "RATE_LIMIT_DEPENDENCY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_public_demo_direct_api_calls_are_denied_before_business_services() -> None:
    context = demo_context()
    unused = object()

    with pytest.raises(HTTPException) as admin_denied:
        await update_user_authorization(
            "target-user",
            AuthorizationUpdateRequest(role="admin"),
            context,
            unused,  # type: ignore[arg-type]
        )
    assert admin_denied.value.status_code == 403

    with pytest.raises(HTTPException) as approval_denied:
        await decide_approval(
            "approval-demo-001",
            ApprovalApiDecisionRequest(
                decision="approved",
                trace_id="trace-demo-approval-001",
            ),
            unused,  # type: ignore[arg-type]
            unused,  # type: ignore[arg-type]
            context,
            unused,  # type: ignore[arg-type]
        )
    assert approval_denied.value.status_code == 403

    with pytest.raises(HTTPException) as upload_denied:
        await create_upload(
            UploadApiCreateRequest(
                store_id=STORE_1,
                display_filename="demo.mp4",
                idempotency_key="demo-upload-001",
            ),
            unused,  # type: ignore[arg-type]
            context,
            unused,  # type: ignore[arg-type]
        )
    assert upload_denied.value.status_code == 403

    with pytest.raises(HTTPException) as gpu_denied:
        await create_clip_task(
            "asset-demo-001",
            ClipTaskApiCreateRequest(
                idempotency_key="demo-clip-001",
                run_id="run-demo-clip-001",
                thread_id="thread-demo-clip-001",
                trace_id="trace-demo-clip-001",
            ),
            unused,  # type: ignore[arg-type]
            context,
            unused,  # type: ignore[arg-type]
        )
    assert gpu_denied.value.status_code == 403

    http_request = type(
        "RequestStub",
        (),
        {"client": type("Client", (), {"host": "203.0.113.10"})(), "headers": {}},
    )()
    with pytest.raises(HTTPException) as order_denied:
        await create_run(
            CustomerServiceApiRunRequest(
                agent_type="customer_service",
                message="查询非演示订单",
                order_id="order-outside-demo-manifest",
                run_id="run-demo-order-001",
                thread_id="thread-demo-order-001",
                trace_id="trace-demo-order-001",
            ),
            http_request,  # type: ignore[arg-type]
            unused,  # type: ignore[arg-type]
            unused,  # type: ignore[arg-type]
            unused,  # type: ignore[arg-type]
            context,
            unused,  # type: ignore[arg-type]
        )
    assert order_denied.value.status_code == 403
