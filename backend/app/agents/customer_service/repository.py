import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.customer_service.schemas import (
    AgentRunStatus,
    AgentState,
    ApprovalView,
    CustomerIntent,
    CustomerServiceOutput,
    RiskLevel,
)
from app.db.models import (
    AgentCheckpointLink,
    AgentEvent,
    AgentRun,
    Approval,
    AuthUser,
    CustomerConversation,
    Principal,
    ToolCallAudit,
)


class RepositoryFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CustomerServiceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_run(self, state: AgentState) -> None:
        now = datetime.now(UTC)
        message_hash = hashlib.sha256(state["message"].encode()).hexdigest()
        async with self.session_factory() as session, session.begin():
            if await session.get(AgentRun, state["run_id"]):
                raise RepositoryFailure("IDEMPOTENCY_CONFLICT", "run_id already exists")
            conversation = await session.get(CustomerConversation, state["thread_id"])
            if conversation and conversation.principal_id != state["principal_id"]:
                raise RepositoryFailure("FORBIDDEN", "thread belongs to another principal")
            run = AgentRun(
                run_id=state["run_id"],
                thread_id=state["thread_id"],
                principal_id=state["principal_id"],
                auth_session_digest=state.get("auth_session_digest"),
                agent_type="customer_service",
                status=AgentRunStatus.PENDING.value,
                intent=None,
                input_summary={
                    "message_sha256": message_hash,
                    "message_length": len(state["message"]),
                    "order_id": state.get("order_id"),
                },
                output_summary={},
                need_human=False,
                error_code=None,
                approval_id=None,
                trace_id=state["trace_id"],
                created_at=now,
                updated_at=now,
            )
            session.add(run)
            if conversation:
                conversation.last_run_id = run.run_id
                conversation.updated_at = now
            else:
                session.add(
                    CustomerConversation(
                        thread_id=state["thread_id"],
                        principal_id=state["principal_id"],
                        last_run_id=state["run_id"],
                        created_at=now,
                        updated_at=now,
                    )
                )
            link = await session.get(AgentCheckpointLink, state["thread_id"])
            if link:
                link.run_id = state["run_id"]
                link.status = AgentRunStatus.PENDING.value
                link.updated_at = now
            else:
                session.add(
                    AgentCheckpointLink(
                        thread_id=state["thread_id"],
                        run_id=state["run_id"],
                        checkpoint_namespace="customer_service",
                        status=AgentRunStatus.PENDING.value,
                        updated_at=now,
                    )
                )
            self._add_event(session, state, "run_created", AgentRunStatus.PENDING.value, {})

    async def resolve_principal(self, principal_id: str) -> Principal:
        async with self.session_factory() as session:
            principal = await session.get(Principal, principal_id)
            if principal is None or not principal.active:
                raise RepositoryFailure("UNAUTHENTICATED", "principal is not active")
            return principal

    async def persist_state(self, state: AgentState, status: AgentRunStatus) -> None:
        now = datetime.now(UTC)
        output = self.output_from_state(state, status)
        async with self.session_factory() as session, session.begin():
            run = await session.get(AgentRun, state["run_id"])
            if run is None:
                raise RepositoryFailure("NOT_FOUND", "run was not found")
            run.status = status.value
            run.intent = state.get("intent")
            run.output_summary = output.model_dump(mode="json")
            run.need_human = state.get("need_human", False)
            error = state.get("error") or {}
            run.error_code = error.get("error_code")
            approval = state.get("approval")
            run.approval_id = str(approval["approval_id"]) if approval else None
            run.updated_at = now
            link = await session.get(AgentCheckpointLink, state["thread_id"])
            if link:
                link.status = status.value
                link.updated_at = now
            self._add_event(
                session,
                state,
                "run_state",
                status.value,
                {
                    "intent": state.get("intent"),
                    "need_human": state.get("need_human", False),
                    "error_code": run.error_code,
                },
            )

    async def get_output(
        self, run_id: str, principal_id: str, auth_session_digest: str | None = None
    ) -> CustomerServiceOutput:
        async with self.session_factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None or run.agent_type != "customer_service":
                raise RepositoryFailure("NOT_FOUND", "run was not found")
            await self._authorize_run(session, run, principal_id, auth_session_digest)
            if run.output_summary:
                return CustomerServiceOutput.model_validate(run.output_summary)
            return CustomerServiceOutput(
                answer="任务正在处理。",
                intent=CustomerIntent.UNKNOWN,
                evidence=[],
                order_summary=None,
                refund_eligibility=None,
                risk=RiskLevel.LOW,
                need_human=False,
                handoff_reason=None,
                approval=None,
                error=None,
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                status=AgentRunStatus(run.status),
            )

    async def ensure_approval(self, state: AgentState, action_digest: str) -> ApprovalView:
        approval_id = f"approval-{hashlib.sha256(state['run_id'].encode()).hexdigest()[:32]}"
        async with self.session_factory() as session, session.begin():
            approval = await session.get(Approval, approval_id)
            if approval is None:
                now = datetime.now(UTC)
                approval = Approval(
                    id=approval_id,
                    principal_id=state["principal_id"],
                    tool_name="create_refund_request",
                    action_digest=action_digest,
                    status="pending",
                    run_id=state["run_id"],
                    thread_id=state["thread_id"],
                    approved_at=None,
                    decided_at=None,
                    decision_by=None,
                    used_at=None,
                    expires_at=now + timedelta(minutes=15),
                )
                session.add(approval)
            elif (
                approval.run_id != state["run_id"]
                or approval.thread_id != state["thread_id"]
                or approval.action_digest != action_digest
            ):
                raise RepositoryFailure("IDEMPOTENCY_CONFLICT", "approval binding mismatch")
            return self._approval_view(approval)

    async def decide_approval(
        self, approval_id: str, decision: str, actor_id: str
    ) -> tuple[str, str, str]:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            approval = await session.scalar(
                select(Approval).where(Approval.id == approval_id).with_for_update()
            )
            if approval is None or approval.run_id is None or approval.thread_id is None:
                raise RepositoryFailure("NOT_FOUND", "approval was not found")
            actor = await session.get(Principal, actor_id)
            if actor is None or not actor.active:
                raise RepositoryFailure("UNAUTHENTICATED", "approval actor is not active")
            owner = await session.get(Principal, approval.principal_id)
            same_approval_scope = bool(
                actor.role == "approver"
                and owner is not None
                and actor.store_id is not None
                and actor.store_id == owner.store_id
            )
            if (
                actor.role != "admin"
                and actor.id != approval.principal_id
                and not same_approval_scope
            ):
                raise RepositoryFailure("FORBIDDEN", "approval is outside the principal scope")
            if approval.expires_at.replace(tzinfo=UTC) <= now:
                raise RepositoryFailure("APPROVAL_REQUIRED", "approval has expired")
            if approval.status in {"approved", "rejected", "used"}:
                if approval.status == "used" and decision == "approved":
                    return approval.run_id, approval.thread_id, "approved"
                if approval.status != decision:
                    raise RepositoryFailure("IDEMPOTENCY_CONFLICT", "approval was already decided")
                return approval.run_id, approval.thread_id, decision
            approval.status = decision
            approval.decided_at = now
            approval.decision_by = actor.id
            approval.approved_at = now if decision == "approved" else None
            run = await session.get(AgentRun, approval.run_id)
            assert run is not None
            state: AgentState = {
                "run_id": run.run_id,
                "thread_id": run.thread_id,
                "principal_id": run.principal_id,
                "trace_id": run.trace_id,
            }
            self._add_event(session, state, "approval_decision", run.status, {"decision": decision})
            return run.run_id, run.thread_id, decision

    async def approval_owner(self, approval_id: str) -> str:
        async with self.session_factory() as session:
            approval = await session.get(Approval, approval_id)
            if approval is None:
                raise RepositoryFailure("NOT_FOUND", "approval was not found")
            return approval.principal_id

    async def run_principal(self, run_id: str) -> str:
        async with self.session_factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RepositoryFailure("NOT_FOUND", "run was not found")
            return run.principal_id

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

    async def mark_approval_used(self, approval_id: str) -> None:
        async with self.session_factory() as session, session.begin():
            approval = await session.get(Approval, approval_id)
            if approval:
                approval.status = "used"
                approval.used_at = datetime.now(UTC)

    async def audit_forbidden_tool(self, state: AgentState, requested_tool: str) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            session.add(
                ToolCallAudit(
                    id=str(uuid.uuid4()),
                    tool_name=requested_tool[:100],
                    tool_version="1.0.0",
                    run_id=state["run_id"],
                    task_id=f"{state['run_id']}-forbidden"[:80],
                    agent="customer_service",
                    principal_id=state["principal_id"],
                    store_scope_summary=state.get("store_id"),
                    input_summary={
                        "request_sha256": hashlib.sha256(state["message"].encode()).hexdigest()
                    },
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
            self._add_event(
                session,
                state,
                "permission_denied",
                AgentRunStatus.FAILED.value,
                {"tool": requested_tool},
            )

    @staticmethod
    def output_from_state(state: AgentState, status: AgentRunStatus) -> CustomerServiceOutput:
        return CustomerServiceOutput(
            answer=state.get("answer", ""),
            intent=CustomerIntent(state.get("intent", CustomerIntent.UNKNOWN.value)),
            evidence=state.get("evidence", []),
            order_summary=state.get("order_summary"),
            refund_eligibility=state.get("refund_eligibility"),
            risk=RiskLevel(state.get("risk", RiskLevel.LOW.value)),
            need_human=state.get("need_human", False),
            handoff_reason=state.get("handoff_reason"),
            approval=state.get("approval"),
            error=state.get("error"),
            run_id=state["run_id"],
            thread_id=state["thread_id"],
            trace_id=state["trace_id"],
            status=status,
        )

    @staticmethod
    def _approval_view(approval: Approval) -> ApprovalView:
        status = "used" if approval.used_at else approval.status
        return ApprovalView(
            approval_id=approval.id,
            status=status,
            action_digest=approval.action_digest,
            expires_at=approval.expires_at.isoformat(),
        )

    @staticmethod
    def _add_event(
        session: AsyncSession,
        state: AgentState,
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
