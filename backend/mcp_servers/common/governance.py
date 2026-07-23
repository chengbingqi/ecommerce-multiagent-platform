import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Principal, ToolCallAudit
from mcp_servers.common.contracts import (
    CallContext,
    Evidence,
    ToolErrorData,
    ToolMeta,
    ToolResult,
    ToolSpec,
)
from mcp_servers.common.errors import ToolFailure

Handler = Callable[[AsyncSession, Principal], Awaitable[tuple[dict[str, Any], list[Evidence]]]]


def action_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _summary(spec: ToolSpec, payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in spec.audit_fields:
        if key not in payload:
            continue
        value = payload[key]
        if value is None:
            continue
        if key == "query":
            result[key] = hashlib.sha256(str(value).encode()).hexdigest()[:16]
        else:
            result[key] = value
    return result


def _meta(spec: ToolSpec, context: CallContext, latency_ms: int) -> ToolMeta:
    return ToolMeta(
        tool_name=spec.tool_name,
        tool_version=spec.tool_version,
        run_id=context.run_id,
        task_id=context.task_id,
        trace_id=context.trace_id,
        latency_ms=latency_ms,
    )


async def execute_tool(
    session_factory: async_sessionmaker[AsyncSession],
    spec: ToolSpec,
    context: CallContext,
    payload: dict[str, Any],
    handler: Handler,
    *,
    approval_id: str | None = None,
    idempotency_key: str | None = None,
) -> ToolResult:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    permission_result = "denied"
    principal: Principal | None = None
    store_scope_summary: str | None = None
    evidence: list[Evidence] = []
    error: ToolFailure | None = None
    data: dict[str, Any] | None = None
    status = "success"
    try:
        async with session_factory() as session, session.begin():
            principal = await session.get(Principal, context.principal_id)
            if principal is None or not principal.active:
                raise ToolFailure("UNAUTHENTICATED", "principal is not authenticated")
            store_scope_summary = principal.store_id
            if principal.role not in spec.permissions:
                raise ToolFailure("FORBIDDEN", "principal is not allowed to call this tool")
            permission_result = "allowed"
            try:
                async with asyncio.timeout(spec.timeout_seconds):
                    data, evidence = await handler(session, principal)
            except TimeoutError as exc:
                raise ToolFailure("TIMEOUT", "tool execution timed out", retryable=True) from exc
            finished_at = datetime.now(UTC)
            latency_ms = max(0, int((time.perf_counter() - started) * 1000))
            session.add(
                _audit(
                    spec,
                    context,
                    store_scope_summary,
                    payload,
                    permission_result,
                    started_at,
                    finished_at,
                    latency_ms,
                    status,
                    None,
                    evidence,
                    approval_id,
                    idempotency_key,
                )
            )
    except ToolFailure as exc:
        error = exc
        status = "forbidden" if exc.error_code == "FORBIDDEN" else "rejected"
        finished_at = datetime.now(UTC)
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        try:
            async with session_factory() as audit_session, audit_session.begin():
                audit_session.add(
                    _audit(
                        spec,
                        context,
                        store_scope_summary,
                        payload,
                        permission_result,
                        started_at,
                        finished_at,
                        latency_ms,
                        status,
                        exc.error_code,
                        evidence,
                        approval_id,
                        idempotency_key,
                    )
                )
        except Exception:
            error = ToolFailure("INTERNAL_ERROR", "audit persistence failed")
    except Exception:
        error = ToolFailure("INTERNAL_ERROR", "tool execution failed")
        status = "error"
        finished_at = datetime.now(UTC)
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        try:
            async with session_factory() as audit_session, audit_session.begin():
                audit_session.add(
                    _audit(
                        spec,
                        context,
                        store_scope_summary,
                        payload,
                        permission_result,
                        started_at,
                        finished_at,
                        latency_ms,
                        status,
                        error.error_code,
                        evidence,
                        approval_id,
                        idempotency_key,
                    )
                )
        except Exception:
            pass
    latency_ms = max(0, int((time.perf_counter() - started) * 1000))
    if error:
        return ToolResult(
            ok=False,
            error=ToolErrorData(
                error_code=error.error_code,
                message=error.message,
                retryable=error.retryable,
                trace_id=context.trace_id,
            ),
            meta=_meta(spec, context, latency_ms),
        )
    return ToolResult(
        ok=True,
        data=data,
        evidence=evidence,
        meta=_meta(spec, context, latency_ms),
    )


def _audit(
    spec: ToolSpec,
    context: CallContext,
    store_scope_summary: str | None,
    payload: dict[str, Any],
    permission_result: str,
    started_at: datetime,
    finished_at: datetime,
    latency_ms: int,
    status: str,
    error_code: str | None,
    evidence: list[Evidence],
    approval_id: str | None,
    idempotency_key: str | None,
) -> ToolCallAudit:
    return ToolCallAudit(
        id=str(uuid.uuid4()),
        tool_name=spec.tool_name,
        tool_version=spec.tool_version,
        run_id=context.run_id,
        task_id=context.task_id,
        agent=context.agent,
        principal_id=context.principal_id,
        store_scope_summary=store_scope_summary,
        input_summary=_summary(spec, payload),
        permission_result=permission_result,
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=latency_ms,
        status=status,
        error_code=error_code,
        retry_count=0,
        evidence_ref=[item.evidence_ref for item in evidence],
        side_effect=spec.side_effect,
        approval_id=approval_id,
        idempotency_key=idempotency_key,
        trace_id=context.trace_id,
    )
