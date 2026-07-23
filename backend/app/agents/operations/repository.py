import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.customer_service.repository import RepositoryFailure
from app.agents.customer_service.schemas import AgentRunStatus
from app.agents.operations.anomaly_rules import ANOMALY_RULES_VERSION
from app.agents.operations.provider import PROMPT_VERSION
from app.agents.operations.schemas import (
    OperationsOutput,
    OperationsState,
    OperationsTopic,
    PeriodView,
)
from app.db.models import (
    AgentCheckpointLink,
    AgentEvent,
    AgentRun,
    AuthUser,
    Principal,
    ToolCallAudit,
)
from mcp_servers.business.operations import METRICS_VERSION


class OperationsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_run(self, state: OperationsState) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            if await session.get(AgentRun, state["run_id"]):
                raise RepositoryFailure("IDEMPOTENCY_CONFLICT", "run_id already exists")
            link = await session.get(AgentCheckpointLink, state["thread_id"])
            if link:
                previous = await session.get(AgentRun, link.run_id)
                if previous and previous.principal_id != state["principal_id"]:
                    raise RepositoryFailure("FORBIDDEN", "thread belongs to another principal")
                if link.checkpoint_namespace != "operations":
                    raise RepositoryFailure(
                        "IDEMPOTENCY_CONFLICT", "thread belongs to another agent"
                    )
            session.add(
                AgentRun(
                    run_id=state["run_id"],
                    thread_id=state["thread_id"],
                    principal_id=state["principal_id"],
                    auth_session_digest=state.get("auth_session_digest"),
                    agent_type="operations",
                    status=AgentRunStatus.PENDING.value,
                    intent=state["topic"],
                    input_summary={
                        "store_id": state["requested_store_id"],
                        "start_at": state["start_at"],
                        "end_at": state["end_at"],
                        "topic": state["topic"],
                        "question_sha256": hashlib.sha256(
                            (state.get("user_question") or "").encode()
                        ).hexdigest(),
                    },
                    output_summary={},
                    need_human=False,
                    error_code=None,
                    approval_id=None,
                    trace_id=state["trace_id"],
                    created_at=now,
                    updated_at=now,
                )
            )
            # The link carries a real FK but no ORM relationship; flush the parent explicitly
            # so PostgreSQL enforces the intended ordering deterministically.
            await session.flush()
            if link:
                link.run_id = state["run_id"]
                link.status = AgentRunStatus.PENDING.value
                link.updated_at = now
            else:
                session.add(
                    AgentCheckpointLink(
                        thread_id=state["thread_id"],
                        run_id=state["run_id"],
                        checkpoint_namespace="operations",
                        status=AgentRunStatus.PENDING.value,
                        updated_at=now,
                    )
                )
            self._event(session, state, "run_created", AgentRunStatus.PENDING.value, {})

    async def resolve_principal(self, principal_id: str) -> Principal:
        async with self.session_factory() as session:
            principal = await session.get(Principal, principal_id)
            if principal is None or not principal.active:
                raise RepositoryFailure("UNAUTHENTICATED", "principal is not active")
            return principal

    async def persist_state(self, state: OperationsState, status: AgentRunStatus) -> None:
        output = self.output_from_state(state, status)
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            run = await session.get(AgentRun, state["run_id"])
            if run is None:
                raise RepositoryFailure("NOT_FOUND", "run was not found")
            run.status = status.value
            run.intent = state.get("topic")
            run.output_summary = output.model_dump(mode="json")
            run.need_human = state.get("need_human", False)
            run.error_code = (state.get("error") or {}).get("error_code")
            run.updated_at = now
            link = await session.get(AgentCheckpointLink, state["thread_id"])
            if link:
                link.status = status.value
                link.updated_at = now
            self._event(
                session,
                state,
                "run_state",
                status.value,
                {
                    "topic": state.get("topic"),
                    "need_human": run.need_human,
                    "error_code": run.error_code,
                },
            )

    async def get_output(
        self, run_id: str, principal_id: str, auth_session_digest: str | None = None
    ) -> OperationsOutput:
        async with self.session_factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None or run.agent_type != "operations":
                raise RepositoryFailure("NOT_FOUND", "operations run was not found")
            await self._authorize_run(session, run, principal_id, auth_session_digest)
            if run.output_summary:
                return OperationsOutput.model_validate(run.output_summary)
            summary = run.input_summary
            return OperationsOutput(
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                status=AgentRunStatus(run.status),
                store_id=str(summary["store_id"]),
                period=PeriodView(start=str(summary["start_at"]), end=str(summary["end_at"])),
                granularity="day",
                metrics_version=METRICS_VERSION,
                anomaly_rules_version=ANOMALY_RULES_VERSION,
                prompt_version=PROMPT_VERSION,
                topic=OperationsTopic(str(summary["topic"])),
                metrics=[],
                trend_series=[],
                anomalies=[],
                evidence_rows=[],
                explanation="任务正在处理。",
                recommendations=[],
                no_data=False,
                warnings=[],
                need_human=False,
                error=None,
            )

    async def audit_forbidden(self, state: OperationsState, reason: str) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            session.add(
                ToolCallAudit(
                    id=str(uuid.uuid4()),
                    tool_name=reason[:100],
                    tool_version="1.0.0",
                    run_id=state["run_id"],
                    task_id=f"{state['run_id']}-forbidden"[:80],
                    agent="operations",
                    principal_id=state["principal_id"],
                    store_scope_summary=state.get("store_id"),
                    input_summary={"store_id": state.get("requested_store_id")},
                    permission_result="denied",
                    started_at=now,
                    finished_at=now,
                    latency_ms=0,
                    status="error",
                    error_code="FORBIDDEN",
                    retry_count=0,
                    evidence_ref=[],
                    side_effect=False,
                    approval_id=None,
                    idempotency_key=None,
                    trace_id=state["trace_id"],
                )
            )
            self._event(
                session, state, "permission_denied", AgentRunStatus.FAILED.value, {"reason": reason}
            )

    @staticmethod
    def output_from_state(state: OperationsState, status: AgentRunStatus) -> OperationsOutput:
        return OperationsOutput(
            run_id=state["run_id"],
            thread_id=state["thread_id"],
            trace_id=state["trace_id"],
            status=status,
            store_id=state.get("store_id", state["requested_store_id"]),
            period=PeriodView(start=state["start_at"], end=state["end_at"]),
            granularity=state["granularity"],
            metrics_version=METRICS_VERSION,
            anomaly_rules_version=ANOMALY_RULES_VERSION,
            prompt_version=PROMPT_VERSION,
            topic=OperationsTopic(state.get("topic", OperationsTopic.UNKNOWN.value)),
            metrics=state.get("metrics", []),
            trend_series=state.get("trend_series", []),
            anomalies=state.get("anomalies", []),
            evidence_rows=state.get("evidence_rows", []),
            explanation=state.get("explanation", ""),
            recommendations=state.get("recommendations", []),
            no_data=state.get("no_data", False),
            warnings=state.get("warnings", []),
            need_human=state.get("need_human", False),
            error=state.get("error"),
        )

    @staticmethod
    async def _authorize_run(
        session: AsyncSession,
        run: AgentRun,
        principal_id: str,
        auth_session_digest: str | None = None,
    ) -> None:
        actor = await session.get(Principal, principal_id)
        if actor is None or not actor.active:
            raise RepositoryFailure("UNAUTHENTICATED", "principal is not active")
        if actor.role == "admin":
            return
        if actor.id != run.principal_id:
            raise RepositoryFailure("FORBIDDEN", "run is outside the principal scope")
        auth_user = await session.scalar(select(AuthUser).where(AuthUser.principal_id == actor.id))
        if auth_user and auth_user.account_type == "public_demo":
            if not run.auth_session_digest or run.auth_session_digest != auth_session_digest:
                raise RepositoryFailure("FORBIDDEN", "run is outside the current demo session")

    @staticmethod
    def _event(
        session: AsyncSession,
        state: OperationsState,
        event_type: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        session.add(
            AgentEvent(
                id=str(uuid.uuid4()),
                run_id=state["run_id"],
                event_type=event_type,
                status=status,
                payload_summary=payload,
                trace_id=state["trace_id"],
                created_at=datetime.now(UTC),
            )
        )
