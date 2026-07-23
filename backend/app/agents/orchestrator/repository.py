from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.customer_service.repository import RepositoryFailure
from app.agents.customer_service.schemas import AgentRunStatus
from app.agents.orchestrator.schemas import (
    AgentEventView,
    AgentTarget,
    MediaResult,
    OrchestratorOutput,
    OrchestratorRunRequest,
    OrchestratorState,
    RetryPolicy,
    RouteDecision,
    SubTaskPlan,
    SubTaskView,
)
from app.db.models import (
    AgentCheckpointLink,
    AgentEvent,
    AgentRun,
    AuthUser,
    OrchestratorSubTask,
    Principal,
)

TERMINAL_STATUSES = {
    AgentRunStatus.SUCCEEDED.value,
    AgentRunStatus.PARTIAL_SUCCESS.value,
    AgentRunStatus.FAILED.value,
    AgentRunStatus.CANCELLED.value,
}


class OrchestratorRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_run(self, request: OrchestratorRunRequest) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            if await session.get(AgentRun, request.run_id):
                raise RepositoryFailure("IDEMPOTENCY_CONFLICT", "run_id already exists")
            principal = await session.get(Principal, request.principal_id)
            if principal is None or not principal.active:
                raise RepositoryFailure("UNAUTHENTICATED", "principal is not active")
            link = await session.get(AgentCheckpointLink, request.thread_id)
            if link:
                previous = await session.get(AgentRun, link.run_id)
                if previous and previous.principal_id != request.principal_id:
                    raise RepositoryFailure("FORBIDDEN", "thread belongs to another principal")
                if link.checkpoint_namespace != "orchestrator":
                    raise RepositoryFailure(
                        "IDEMPOTENCY_CONFLICT", "thread belongs to another agent"
                    )
            request_hash = hashlib.sha256(request.request_text.encode()).hexdigest()
            run = AgentRun(
                run_id=request.run_id,
                thread_id=request.thread_id,
                principal_id=request.principal_id,
                auth_session_digest=request.auth_session_digest,
                agent_type="orchestrator",
                status=AgentRunStatus.PENDING.value,
                intent=None,
                input_summary={
                    "request_sha256": request_hash,
                    "request_length": len(request.request_text),
                    "provided_fields": sorted(
                        name
                        for name in (
                            "order_id",
                            "store_id",
                            "start_date",
                            "end_date",
                            "topic",
                            "asset_id",
                        )
                        if getattr(request, name) is not None
                    ),
                },
                output_summary={},
                need_human=False,
                error_code=None,
                approval_id=None,
                trace_id=request.trace_id,
                created_at=now,
                updated_at=now,
            )
            session.add(run)
            await session.flush([run])
            if link:
                link.run_id = request.run_id
                link.status = AgentRunStatus.PENDING.value
                link.updated_at = now
            else:
                session.add(
                    AgentCheckpointLink(
                        thread_id=request.thread_id,
                        run_id=request.run_id,
                        checkpoint_namespace="orchestrator",
                        status=AgentRunStatus.PENDING.value,
                        updated_at=now,
                    )
                )
            self._event(
                session,
                request.run_id,
                request.trace_id,
                "run_created",
                AgentRunStatus.PENDING.value,
                {"agent_type": "orchestrator"},
            )

    async def resolve_principal(self, principal_id: str) -> Principal:
        async with self.session_factory() as session:
            principal = await session.get(Principal, principal_id)
            if principal is None or not principal.active:
                raise RepositoryFailure("UNAUTHENTICATED", "principal is not active")
            return principal

    async def mark_run_running(self, state: OrchestratorState) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            run = await session.get(AgentRun, state["run_id"])
            if run is None:
                raise RepositoryFailure("NOT_FOUND", "run was not found")
            if run.status != AgentRunStatus.PENDING.value:
                raise RepositoryFailure("IDEMPOTENCY_CONFLICT", "run is not pending")
            run.status = AgentRunStatus.RUNNING.value
            run.updated_at = now
            link = await session.get(AgentCheckpointLink, state["thread_id"])
            if link:
                link.status = AgentRunStatus.RUNNING.value
                link.updated_at = now
            self._event(
                session,
                run.run_id,
                run.trace_id,
                "run_started",
                AgentRunStatus.RUNNING.value,
                {"agent_type": "orchestrator"},
            )

    async def sync_subtasks(
        self, state: OrchestratorState, plans: list[SubTaskPlan]
    ) -> list[OrchestratorSubTask]:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            rows: list[OrchestratorSubTask] = []
            for plan in sorted(plans, key=lambda item: item.task_id):
                existing = await session.scalar(
                    select(OrchestratorSubTask).where(
                        OrchestratorSubTask.parent_run_id == state["run_id"],
                        OrchestratorSubTask.idempotency_key == plan.idempotency_key,
                    )
                )
                if existing:
                    if (
                        existing.agent_type != plan.agent_type.value
                        or existing.child_run_id != plan.child_run_id
                        or existing.dependency_ids != plan.dependency_ids
                    ):
                        raise RepositoryFailure(
                            "IDEMPOTENCY_CONFLICT", "subtask plan binding mismatch"
                        )
                    rows.append(existing)
                    continue
                row = OrchestratorSubTask(
                    subtask_id=plan.task_id,
                    parent_run_id=state["run_id"],
                    child_run_id=plan.child_run_id,
                    external_task_id=None,
                    agent_type=plan.agent_type.value,
                    dependency_ids=plan.dependency_ids,
                    required_inputs=plan.required_inputs,
                    input_summary=self._subtask_input_summary(state, plan.agent_type),
                    output_ref=None,
                    output_summary={},
                    evidence_refs=[],
                    expected_output_schema=plan.expected_output_schema,
                    tool_scope=plan.tool_scope,
                    timeout_seconds=plan.timeout_seconds,
                    retry_policy=plan.retry_policy.model_dump(mode="json"),
                    status=AgentRunStatus.PENDING.value,
                    retry_count=0,
                    error_code=None,
                    approval_id=None,
                    idempotency_key=plan.idempotency_key,
                    created_at=now,
                    started_at=None,
                    completed_at=None,
                    updated_at=now,
                )
                session.add(row)
                rows.append(row)
            self._event(
                session,
                state["run_id"],
                state["trace_id"],
                "subtasks_created",
                AgentRunStatus.RUNNING.value,
                {"subtask_count": len(rows)},
            )
            return rows

    async def mark_subtask_running(self, subtask_id: str) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            row = await session.get(OrchestratorSubTask, subtask_id)
            if row is None:
                raise RepositoryFailure("NOT_FOUND", "subtask was not found")
            if row.status in TERMINAL_STATUSES | {AgentRunStatus.WAITING_APPROVAL.value}:
                return
            row.status = AgentRunStatus.RUNNING.value
            row.started_at = row.started_at or now
            row.updated_at = now

    async def complete_subtask(
        self,
        subtask_id: str,
        *,
        status: AgentRunStatus,
        output: dict[str, Any],
        output_ref: str | None,
        evidence_refs: list[str],
        error_code: str | None = None,
        approval_id: str | None = None,
        external_task_id: str | None = None,
        retry_count: int | None = None,
    ) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            row = await session.get(OrchestratorSubTask, subtask_id)
            if row is None:
                raise RepositoryFailure("NOT_FOUND", "subtask was not found")
            row.status = status.value
            row.output_summary = output
            row.output_ref = output_ref
            row.evidence_refs = sorted(set(evidence_refs))
            row.error_code = error_code
            row.approval_id = approval_id
            row.external_task_id = external_task_id or row.external_task_id
            if retry_count is not None:
                row.retry_count = retry_count
            row.started_at = row.started_at or now
            row.completed_at = now if status.value in TERMINAL_STATUSES else None
            row.updated_at = now
            parent = await session.get(AgentRun, row.parent_run_id)
            assert parent is not None
            self._event(
                session,
                parent.run_id,
                parent.trace_id,
                "subtask_state",
                status.value,
                {
                    "subtask_id": row.subtask_id,
                    "agent_type": row.agent_type,
                    "error_code": error_code,
                },
            )

    async def list_subtasks(self, parent_run_id: str) -> list[OrchestratorSubTask]:
        async with self.session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(OrchestratorSubTask)
                        .where(OrchestratorSubTask.parent_run_id == parent_run_id)
                        .order_by(OrchestratorSubTask.created_at, OrchestratorSubTask.subtask_id)
                    )
                ).all()
            )

    async def persist_state(self, state: OrchestratorState, status: AgentRunStatus) -> None:
        now = datetime.now(UTC)
        output = await self.output_from_state(state, status)
        async with self.session_factory() as session, session.begin():
            run = await session.get(AgentRun, state["run_id"])
            if run is None:
                raise RepositoryFailure("NOT_FOUND", "run was not found")
            run.status = status.value
            route = state.get("route") or {}
            run.intent = ",".join(route.get("intents", []))[:40] or None
            run.output_summary = output.model_dump(mode="json")
            run.need_human = state.get("need_human", False)
            run.error_code = (state.get("error") or {}).get("error_code")
            run.approval_id = state.get("approval_id")
            run.updated_at = now
            link = await session.get(AgentCheckpointLink, state["thread_id"])
            if link:
                link.status = status.value
                link.updated_at = now
            self._event(
                session,
                run.run_id,
                run.trace_id,
                "run_state",
                status.value,
                {
                    "need_human": run.need_human,
                    "error_code": run.error_code,
                    "subtask_count": len(state.get("subtasks", [])),
                },
            )

    async def get_output(
        self, run_id: str, principal_id: str, auth_session_digest: str | None = None
    ) -> OrchestratorOutput:
        async with self.session_factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None or run.agent_type != "orchestrator":
                raise RepositoryFailure("NOT_FOUND", "orchestrator run was not found")
            await self._authorize_run(session, run, principal_id, auth_session_digest)
            if run.output_summary:
                return OrchestratorOutput.model_validate(run.output_summary)
        state: OrchestratorState = {
            "run_id": run.run_id,
            "thread_id": run.thread_id,
            "trace_id": run.trace_id,
            "route": self.empty_route().model_dump(mode="json"),
            "plan": [],
            "subtasks": [],
            "warnings": [],
            "evidence_refs": [],
            "partial_failures": [],
            "need_human": False,
        }
        return await self.output_from_state(state, AgentRunStatus(run.status))

    async def output_from_state(
        self, state: OrchestratorState, status: AgentRunStatus
    ) -> OrchestratorOutput:
        route = RouteDecision.model_validate(state.get("route") or self.empty_route())
        plan = [SubTaskPlan.model_validate(item) for item in state.get("plan", [])]
        rows = await self.list_subtasks(state["run_id"])
        subtasks = [self.subtask_view(row) for row in rows]
        media = state.get("media_result")
        error = state.get("error")
        return OrchestratorOutput(
            run_id=state["run_id"],
            thread_id=state["thread_id"],
            trace_id=state["trace_id"],
            overall_status=status,
            route=route,
            plan=plan,
            subtasks=subtasks,
            customer_service_result=state.get("customer_service_result"),
            operations_result=state.get("operations_result"),
            media_result=MediaResult.model_validate(media) if media else None,
            evidence_refs=sorted(set(state.get("evidence_refs", []))),
            partial_failures=state.get("partial_failures", []),
            warnings=state.get("warnings", []),
            need_human=state.get("need_human", False),
            completion_rule=route.completion_rule,
            error=error,
            versions={
                "orchestrator": "orchestrator_v1.0.0",
                "route": route.route_version,
                "plan": "orchestrator_plan_v1.0.0",
                "customer_service": "customer_service_v1.0.0",
                "operations": "operations_analysis_v1.0.0",
                "media": "media_pipeline_v1.2.1",
            },
        )

    async def list_events(
        self,
        run_id: str,
        principal_id: str,
        after_event_id: str | None = None,
        auth_session_digest: str | None = None,
    ) -> list[AgentEventView]:
        async with self.session_factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RepositoryFailure("NOT_FOUND", "run was not found")
            await self._authorize_run(session, run, principal_id, auth_session_digest)
            statement = select(AgentEvent).where(AgentEvent.run_id == run_id)
            if after_event_id:
                cursor = await session.get(AgentEvent, after_event_id)
                if cursor is not None and cursor.run_id == run_id:
                    statement = statement.where(
                        or_(
                            AgentEvent.created_at > cursor.created_at,
                            (AgentEvent.created_at == cursor.created_at)
                            & (AgentEvent.id > cursor.id),
                        )
                    )
            rows = (
                await session.scalars(statement.order_by(AgentEvent.created_at, AgentEvent.id))
            ).all()
            return [
                AgentEventView(
                    event_id=row.id,
                    run_id=row.run_id,
                    event_type=row.event_type,
                    status=row.status,
                    payload_summary=row.payload_summary,
                    trace_id=row.trace_id,
                    timestamp=row.created_at.isoformat(),
                )
                for row in rows
            ]

    async def parent_for_child(self, child_run_id: str) -> str:
        async with self.session_factory() as session:
            parent = await session.scalar(
                select(OrchestratorSubTask.parent_run_id).where(
                    OrchestratorSubTask.child_run_id == child_run_id
                )
            )
            if parent is None:
                raise RepositoryFailure("NOT_FOUND", "parent orchestrator run was not found")
            return parent

    async def cancel(
        self, run_id: str, principal_id: str, auth_session_digest: str | None = None
    ) -> OrchestratorOutput:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            run = await session.get(AgentRun, run_id)
            if run is None or run.agent_type != "orchestrator":
                raise RepositoryFailure("NOT_FOUND", "orchestrator run was not found")
            await self._authorize_run(session, run, principal_id, auth_session_digest)
            if run.status in TERMINAL_STATUSES:
                raise RepositoryFailure("IDEMPOTENCY_CONFLICT", "terminal run cannot be cancelled")
            if run.status != AgentRunStatus.PENDING.value:
                raise RepositoryFailure(
                    "IDEMPOTENCY_CONFLICT",
                    "only a pending orchestrator run can be cancelled safely",
                )
            rows = (
                await session.scalars(
                    select(OrchestratorSubTask).where(
                        OrchestratorSubTask.parent_run_id == run_id,
                        OrchestratorSubTask.status == AgentRunStatus.PENDING.value,
                    )
                )
            ).all()
            for row in rows:
                row.status = AgentRunStatus.CANCELLED.value
                row.completed_at = now
                row.updated_at = now
            run.status = AgentRunStatus.CANCELLED.value
            if run.output_summary:
                output = dict(run.output_summary)
                output["overall_status"] = AgentRunStatus.CANCELLED.value
                output["need_human"] = False
                output["warnings"] = list(
                    dict.fromkeys([*output.get("warnings", []), "运行已由使用者取消。"])
                )
                run.output_summary = output
            run.updated_at = now
            self._event(
                session,
                run.run_id,
                run.trace_id,
                "run_cancelled",
                run.status,
                {"cancelled_pending_subtasks": len(rows)},
            )
        return await self.get_output(run_id, principal_id, auth_session_digest)

    async def record_node(
        self, state: OrchestratorState, node: str, payload: dict[str, Any] | None = None
    ) -> None:
        async with self.session_factory() as session, session.begin():
            self._event(
                session,
                state["run_id"],
                state["trace_id"],
                "orchestrator_node",
                state.get("status", AgentRunStatus.RUNNING.value),
                {"node": node, **(payload or {})},
            )

    @staticmethod
    def subtask_view(row: OrchestratorSubTask) -> SubTaskView:
        duration = None
        if row.started_at and row.completed_at:
            duration = max(int((row.completed_at - row.started_at).total_seconds() * 1000), 0)
        return SubTaskView(
            task_id=row.subtask_id,
            parent_run_id=row.parent_run_id,
            child_run_id=row.child_run_id,
            external_task_id=row.external_task_id,
            agent_type=AgentTarget(row.agent_type),
            dependency_ids=list(row.dependency_ids),
            required_inputs=list(row.required_inputs),
            expected_output_schema=row.expected_output_schema,
            tool_scope=list(row.tool_scope),
            timeout_seconds=row.timeout_seconds,
            retry_policy=RetryPolicy.model_validate(row.retry_policy),
            status=AgentRunStatus(row.status),
            retry_count=row.retry_count,
            output_ref=row.output_ref,
            evidence_refs=list(row.evidence_refs),
            error_code=row.error_code,
            approval_id=row.approval_id,
            duration_ms=duration,
        )

    @staticmethod
    def empty_route() -> RouteDecision:
        return RouteDecision(
            intents=["UNCLASSIFIED"],
            confidence=0,
            risk_level="MEDIUM",
            agent_targets=[],
            required_inputs=[],
            missing_inputs=[],
            completion_rule="ALL_REQUIRED",
            warnings=[],
            route_version="orchestrator_route_v1.0.0",
        )

    @staticmethod
    def _subtask_input_summary(state: OrchestratorState, agent_type: AgentTarget) -> dict[str, Any]:
        if agent_type == AgentTarget.CUSTOMER_SERVICE:
            return {"order_id": state.get("order_id")}
        if agent_type == AgentTarget.OPERATIONS:
            return {
                "store_id": state.get("store_id"),
                "start_date": state.get("start_date"),
                "end_date": state.get("end_date"),
                "topic": state.get("topic"),
            }
        return {"asset_id": state.get("asset_id")}

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
        run_id: str,
        trace_id: str,
        event_type: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        session.add(
            AgentEvent(
                id=str(uuid.uuid4()),
                run_id=run_id,
                event_type=event_type,
                status=status,
                payload_summary=payload,
                trace_id=trace_id,
                created_at=datetime.now(UTC),
            )
        )
