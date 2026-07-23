from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from app.agents.customer_service.schemas import (
    AgentRunStatus,
    CustomerServiceRunRequest,
)
from app.agents.customer_service.service import CustomerServiceAgentService
from app.agents.live_video.service import LiveVideoAgentService
from app.agents.operations.schemas import OperationsRunRequest, OperationsTopic
from app.agents.operations.service import OperationsAgentService
from app.agents.orchestrator.schemas import (
    AgentTarget,
    MediaResult,
    OrchestratorRunRequest,
    OrchestratorState,
    SubTaskPlan,
)
from app.media.schemas import ClipTaskCreateRequest


@dataclass(frozen=True)
class DispatchOutcome:
    status: AgentRunStatus
    output: dict[str, Any]
    output_ref: str | None
    evidence_refs: list[str]
    error_code: str | None = None
    approval_id: str | None = None
    external_task_id: str | None = None


class OrchestratorAdapters(Protocol):
    async def dispatch(
        self, target: AgentTarget, request: OrchestratorRunRequest, plan: SubTaskPlan
    ) -> DispatchOutcome: ...

    async def refresh_customer(self, child_run_id: str, principal_id: str) -> DispatchOutcome: ...


class DefaultOrchestratorAdapters:
    def __init__(
        self,
        *,
        customer_service: CustomerServiceAgentService | None = None,
        operations: OperationsAgentService | None = None,
        live_video: LiveVideoAgentService | None = None,
        trusted_identity: bool = False,
    ) -> None:
        self.customer_service = customer_service or CustomerServiceAgentService(
            trusted_identity=trusted_identity
        )
        self.operations = operations or OperationsAgentService(trusted_identity=trusted_identity)
        self.live_video = live_video or LiveVideoAgentService(trusted_identity=trusted_identity)

    async def dispatch(
        self, target: AgentTarget, request: OrchestratorRunRequest, plan: SubTaskPlan
    ) -> DispatchOutcome:
        assert request.principal_id is not None
        if target == AgentTarget.CUSTOMER_SERVICE:
            customer_output = await self.customer_service.create_run(
                CustomerServiceRunRequest(
                    agent_type="customer_service",
                    message=request.request_text,
                    principal_id=request.principal_id,
                    order_id=request.order_id,
                    run_id=plan.child_run_id,
                    thread_id=plan.child_thread_id,
                    trace_id=request.trace_id,
                    auth_session_digest=request.auth_session_digest,
                )
            )
            return self._customer_outcome(customer_output.model_dump(mode="json"))
        if target == AgentTarget.OPERATIONS:
            assert request.store_id and request.start_date and request.end_date
            try:
                topic = OperationsTopic((request.topic or "OVERVIEW").upper())
            except ValueError:
                topic = OperationsTopic.OVERVIEW
            operations_output = await self.operations.create_run(
                OperationsRunRequest(
                    agent_type="operations",
                    store_id=request.store_id,
                    start_date=request.start_date,
                    end_date=request.end_date,
                    topic=topic,
                    granularity="day",
                    comparison_mode="previous_period",
                    user_question=request.request_text,
                    principal_id=request.principal_id,
                    run_id=plan.child_run_id,
                    thread_id=plan.child_thread_id,
                    trace_id=request.trace_id,
                    auth_session_digest=request.auth_session_digest,
                )
            )
            payload = operations_output.model_dump(mode="json")
            evidence_refs = sorted(
                {
                    str(row["evidence_ref"])
                    for row in payload.get("evidence_rows", [])
                    if row.get("evidence_ref")
                }
            )
            return DispatchOutcome(
                status=operations_output.status,
                output=payload,
                output_ref=f"agent-run://{operations_output.run_id}",
                evidence_refs=evidence_refs,
                error_code=(
                    operations_output.error.error_code if operations_output.error else None
                ),
            )
        assert request.asset_id
        task = await self.live_video.create_run(
            request.asset_id,
            ClipTaskCreateRequest(
                principal_id=request.principal_id,
                product_keywords=request.product_keywords,
                min_clip_seconds=8,
                max_clip_seconds=30,
                max_candidates=10,
                subtitle_language="zh",
                idempotency_key=plan.idempotency_key,
                run_id=plan.child_run_id,
                thread_id=plan.child_thread_id,
                trace_id=request.trace_id,
            ),
        )
        result = MediaResult(
            task_id=task.task_id,
            asset_id=task.asset_id,
            status=task.status,
            pipeline_version="media_pipeline_v1.2.1",
            review_status="PENDING",
            evidence_refs=[f"media-task://{task.task_id}", f"media-asset://{task.asset_id}"],
        )
        return DispatchOutcome(
            status=AgentRunStatus.SUCCEEDED,
            output=result.model_dump(mode="json"),
            output_ref=f"media-task://{task.task_id}",
            evidence_refs=result.evidence_refs,
            external_task_id=task.task_id,
        )

    async def refresh_customer(self, child_run_id: str, principal_id: str) -> DispatchOutcome:
        output = await self.customer_service.get_run(child_run_id, principal_id)
        return self._customer_outcome(output.model_dump(mode="json"))

    @staticmethod
    def _customer_outcome(payload: dict[str, Any]) -> DispatchOutcome:
        status = AgentRunStatus(str(payload["status"]))
        evidence_refs = sorted(
            {
                str(row["evidence_ref"])
                for row in payload.get("evidence", [])
                if row.get("evidence_ref")
            }
        )
        approval = payload.get("approval") or {}
        error = payload.get("error") or {}
        return DispatchOutcome(
            status=status,
            output=payload,
            output_ref=f"agent-run://{payload['run_id']}",
            evidence_refs=evidence_refs,
            error_code=error.get("error_code"),
            approval_id=approval.get("approval_id"),
        )


def request_from_state(state: OrchestratorState) -> OrchestratorRunRequest:
    start_date = state.get("start_date")
    end_date = state.get("end_date")
    return OrchestratorRunRequest(
        agent_type="orchestrator",
        request_text=str(state["request_text"]),
        principal_id=str(state["principal_id"]),
        run_id=str(state["run_id"]),
        thread_id=str(state["thread_id"]),
        trace_id=str(state["trace_id"]),
        auth_session_digest=state.get("auth_session_digest"),
        order_id=state.get("order_id"),
        store_id=state.get("store_id"),
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
        topic=state.get("topic"),
        asset_id=state.get("asset_id"),
        product_keywords=list(state.get("product_keywords", [])),
    )
