from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.customer_service.repository import RepositoryFailure
from app.agents.customer_service.schemas import AgentRunStatus
from app.agents.orchestrator.adapters import (
    DispatchOutcome,
    OrchestratorAdapters,
    request_from_state,
)
from app.agents.orchestrator.repository import OrchestratorRepository
from app.agents.orchestrator.routing import (
    DeterministicOrchestratorRouter,
    OrchestratorRouter,
)
from app.agents.orchestrator.schemas import (
    AgentTarget,
    OrchestratorRunRequest,
    OrchestratorState,
    RetryPolicy,
    RouteDecision,
    SubTaskPlan,
)
from app.core.config import Settings

CUSTOMER_TOOL_SCOPE = [
    "get_order",
    "get_logistics",
    "check_refund_eligibility",
    "search_policy",
    "get_document_chunk",
    "create_refund_request",
]
OPERATIONS_TOOL_SCOPE = ["get_operations_metrics", "retrieve_sop"]
MEDIA_TOOL_SCOPE = ["create_existing_media_task"]
ALLOWED_TOOL_SCOPES = {
    AgentTarget.CUSTOMER_SERVICE: frozenset(CUSTOMER_TOOL_SCOPE),
    AgentTarget.OPERATIONS: frozenset(OPERATIONS_TOOL_SCOPE),
    AgentTarget.MEDIA: frozenset(MEDIA_TOOL_SCOPE),
}
EXPECTED_SCHEMAS = {
    AgentTarget.CUSTOMER_SERVICE: "CustomerServiceOutput@1",
    AgentTarget.OPERATIONS: "OperationsOutput@1",
    AgentTarget.MEDIA: "MediaResult@1",
}
FORBIDDEN_REQUEST_MARKERS = (
    "忽略系统",
    "忽略以上",
    "绕过权限",
    "扩大工具",
    "伪造agent_target",
    "伪造 agent_target",
    "执行shell",
    "执行 shell",
    "docker socket",
    "/var/run/docker.sock",
    "直接执行sql",
    "直接执行 sql",
)
TRANSIENT_CODES = frozenset({"TIMEOUT", "TRANSIENT_ERROR", "MCP_UNAVAILABLE"})


def _failure(
    state: OrchestratorState,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    need_human: bool = False,
) -> dict[str, Any]:
    return {
        "status": AgentRunStatus.FAILED.value,
        "need_human": need_human,
        "error": {
            "error_code": code,
            "message": message,
            "retryable": retryable,
            "trace_id": state["trace_id"],
        },
    }


def _stable_token(*parts: str, length: int = 24) -> str:
    value = "\x1f".join(parts).encode()
    return hashlib.sha256(value).hexdigest()[:length]


def validate_plan(plans: list[SubTaskPlan], settings: Settings, route: RouteDecision) -> None:
    if len(plans) > settings.orchestrator_max_subtasks:
        raise ValueError("MAX_SUBTASKS_EXCEEDED")
    ids = {plan.task_id for plan in plans}
    if len(ids) != len(plans):
        raise ValueError("DUPLICATE_SUBTASK")
    if {plan.agent_type for plan in plans} != set(route.agent_targets):
        raise ValueError("PLAN_ROUTE_MISMATCH")
    for plan in plans:
        if plan.parent_run_id == plan.child_run_id:
            raise ValueError("MAX_HOP_EXCEEDED")
        if plan.dependency_ids and not set(plan.dependency_ids).issubset(ids):
            raise ValueError("UNKNOWN_DEPENDENCY")
        if plan.task_id in plan.dependency_ids:
            raise ValueError("CYCLIC_DEPENDENCY")
        if not set(plan.tool_scope).issubset(ALLOWED_TOOL_SCOPES[plan.agent_type]):
            raise ValueError("FORBIDDEN_TOOL_SCOPE")
        if plan.retry_policy.side_effect_retry:
            raise ValueError("UNSAFE_RETRY_POLICY")

    visiting: set[str] = set()
    visited: set[str] = set()
    dependencies = {plan.task_id: plan.dependency_ids for plan in plans}

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError("CYCLIC_DEPENDENCY")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency_id in dependencies[task_id]:
            visit(dependency_id)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(ids):
        visit(task_id)


def build_orchestrator_graph(
    *,
    repository: OrchestratorRepository,
    router: OrchestratorRouter,
    adapters: OrchestratorAdapters,
    settings: Settings,
    checkpointer: Any = None,
) -> Any:
    deterministic_router = DeterministicOrchestratorRouter(settings.orchestrator_route_version)

    async def node_event(
        state: OrchestratorState, node: str, payload: dict[str, Any] | None = None
    ) -> None:
        await repository.record_node(state, node, payload)

    def advance(state: OrchestratorState) -> dict[str, Any] | None:
        steps = state.get("step_count", 0) + 1
        if steps > settings.orchestrator_max_steps:
            return {
                **_failure(state, "MAX_STEPS_EXCEEDED", "主控流程已达到最大步骤数。"),
                "step_count": steps,
            }
        started = state.get("started_monotonic", time.monotonic())
        if time.monotonic() - started > settings.orchestrator_deadline_seconds:
            return {
                **_failure(
                    state,
                    "DEADLINE_EXCEEDED",
                    "主控流程已超过总处理时限。",
                    retryable=True,
                    need_human=True,
                ),
                "step_count": steps,
            }
        return None

    async def validate_request(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "validate_request")
        try:
            await repository.mark_run_running(state)
        except RepositoryFailure as exc:
            return _failure(state, exc.code, exc.message)
        text = state.get("request_text", "").strip()
        if not text:
            return _failure(state, "INVALID_ARGUMENT", "request_text不能为空。")
        lowered = text.lower()
        if any(marker in lowered for marker in FORBIDDEN_REQUEST_MARKERS):
            return _failure(
                state,
                "FORBIDDEN",
                "请求试图绕过主控权限、路由或工具边界。",
                need_human=True,
            )
        return {
            "status": AgentRunStatus.RUNNING.value,
            "started_monotonic": state.get("started_monotonic", time.monotonic()),
            "step_count": 1,
            "warnings": [],
            "evidence_refs": [],
            "partial_failures": [],
            "child_outputs": {},
            "need_human": False,
        }

    async def resolve_principal(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "resolve_principal")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        try:
            principal = await repository.resolve_principal(state["principal_id"])
        except RepositoryFailure as exc:
            return {
                **_failure(state, exc.code, exc.message),
                "step_count": state["step_count"] + 1,
            }
        requested_store = state.get("store_id")
        if requested_store and principal.role != "admin" and principal.store_id != requested_store:
            return {
                **_failure(state, "FORBIDDEN", "请求的店铺超出当前主体范围。"),
                "step_count": state["step_count"] + 1,
            }
        return {
            "role": principal.role,
            "principal_store_id": principal.store_id,
            "step_count": state["step_count"] + 1,
        }

    async def classify_intents(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "classify_intents")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        request = request_from_state(state)
        warnings = list(state.get("warnings", []))
        safe_route = await deterministic_router.classify(request)
        try:
            route = await router.classify(request)
        except Exception:
            route = safe_route
            warnings.append("结构化模型路由失败，已使用确定性安全降级。")
        if (
            AgentTarget.MEDIA in route.agent_targets
            and AgentTarget.MEDIA not in safe_route.agent_targets
        ):
            route = safe_route
            warnings.append("模型推断了非显式媒体目标，已由服务端安全路由覆盖。")
        required = set(route.required_inputs)
        if AgentTarget.OPERATIONS in route.agent_targets:
            required.update({"store_id", "start_date", "end_date"})
        if AgentTarget.MEDIA in route.agent_targets:
            required.add("asset_id")
        if AgentTarget.CUSTOMER_SERVICE in route.agent_targets:
            required.update(field for field in safe_route.required_inputs if field == "order_id")
        route = route.model_copy(
            update={
                "required_inputs": sorted(required),
                "missing_inputs": sorted(
                    field for field in required if getattr(request, field) is None
                ),
            }
        )
        if len(route.agent_targets) > settings.orchestrator_max_subtasks:
            return {
                **_failure(state, "MAX_SUBTASKS_EXCEEDED", "主控子任务数量超过服务端上限。"),
                "step_count": state["step_count"] + 1,
            }
        role_targets = {
            "customer": {AgentTarget.CUSTOMER_SERVICE},
            "customer:self": {AgentTarget.CUSTOMER_SERVICE},
            "store_operator": {AgentTarget.OPERATIONS},
            "content_operator": {AgentTarget.MEDIA},
            "approver": set(),
            "agent:customer_service": {AgentTarget.CUSTOMER_SERVICE},
            "agent:operations": {AgentTarget.OPERATIONS},
            "agent:media": {AgentTarget.MEDIA},
            "admin": set(AgentTarget),
        }
        unauthorized = set(route.agent_targets) - role_targets.get(state.get("role", ""), set())
        if unauthorized:
            return {
                **_failure(state, "FORBIDDEN", "主控计划包含当前角色无权调用的Agent。"),
                "step_count": state["step_count"] + 1,
            }
        return {
            "route": route.model_dump(mode="json"),
            "warnings": [*warnings, *route.warnings],
            "step_count": state["step_count"] + 1,
        }

    async def validate_required_inputs(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "validate_required_inputs")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        route = RouteDecision.model_validate(state["route"])
        if not route.agent_targets:
            return {
                **_failure(
                    state,
                    "UNSUPPORTED_REQUEST",
                    "无法安全识别可执行的专业Agent目标。",
                    need_human=True,
                ),
                "step_count": state["step_count"] + 1,
            }
        if route.missing_inputs:
            return {
                **_failure(
                    state,
                    "MISSING_REQUIRED_INPUTS",
                    f"缺少必要参数：{', '.join(route.missing_inputs)}。",
                    need_human=True,
                ),
                "step_count": state["step_count"] + 1,
            }
        return {"step_count": state["step_count"] + 1}

    async def build_structured_plan(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "build_structured_plan")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        route = RouteDecision.model_validate(state["route"])
        plans: list[SubTaskPlan] = []
        for target in route.agent_targets:
            token = _stable_token(state["run_id"], target.value, settings.orchestrator_plan_version)
            child_run_id = f"child-{target.value[:8]}-{token}"[:80]
            plan = SubTaskPlan(
                task_id=f"task-{target.value[:8]}-{token}"[:80],
                parent_run_id=state["run_id"],
                child_run_id=child_run_id,
                child_thread_id=f"thread-{target.value[:8]}-{token}"[:80],
                agent_type=target,
                dependency_ids=[],
                required_inputs=[
                    field
                    for field in route.required_inputs
                    if (target == AgentTarget.CUSTOMER_SERVICE and field == "order_id")
                    or (
                        target == AgentTarget.OPERATIONS
                        and field in {"store_id", "start_date", "end_date"}
                    )
                    or (target == AgentTarget.MEDIA and field == "asset_id")
                ],
                expected_output_schema=EXPECTED_SCHEMAS[target],
                tool_scope=sorted(ALLOWED_TOOL_SCOPES[target]),
                timeout_seconds=int(settings.orchestrator_subtask_timeout_seconds),
                retry_policy=RetryPolicy(max_retries=0 if target == AgentTarget.MEDIA else 1),
                idempotency_key=f"orch-{token}-{target.value}"[:128],
            )
            plans.append(plan)
        return {
            "plan": [plan.model_dump(mode="json") for plan in plans],
            "step_count": state["step_count"] + 1,
        }

    async def validate_plan_and_tool_scopes(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "validate_plan_and_tool_scopes")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        plans = [SubTaskPlan.model_validate(item) for item in state.get("plan", [])]
        route = RouteDecision.model_validate(state["route"])
        try:
            validate_plan(plans, settings, route)
        except (ValueError, KeyError) as exc:
            return {
                **_failure(state, str(exc), "结构化计划未通过权限或循环校验。"),
                "step_count": state["step_count"] + 1,
            }
        return {"step_count": state["step_count"] + 1}

    async def create_subtasks(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "create_subtasks")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        plans = [SubTaskPlan.model_validate(item) for item in state["plan"]]
        try:
            rows = await repository.sync_subtasks(state, plans)
        except RepositoryFailure as exc:
            return {
                **_failure(state, exc.code, exc.message),
                "step_count": state["step_count"] + 1,
            }
        return {
            "subtasks": [repository.subtask_view(row).model_dump(mode="json") for row in rows],
            "step_count": state["step_count"] + 1,
        }

    async def _dispatch_one(
        request: OrchestratorRunRequest, plan: SubTaskPlan
    ) -> tuple[DispatchOutcome, int]:
        last_error: Exception | None = None
        attempts = plan.retry_policy.max_retries + 1
        for attempt in range(attempts):
            try:
                outcome = await asyncio.wait_for(
                    adapters.dispatch(plan.agent_type, request, plan),
                    timeout=plan.timeout_seconds,
                )
                return outcome, attempt
            except TimeoutError as exc:
                last_error = exc
                code = "TIMEOUT"
            except RepositoryFailure as exc:
                last_error = exc
                code = exc.code
            except Exception as exc:
                last_error = exc
                code = "TRANSIENT_ERROR"
            can_retry = (
                attempt < plan.retry_policy.max_retries
                and code in TRANSIENT_CODES
                and plan.agent_type != AgentTarget.MEDIA
            )
            if not can_retry:
                return (
                    DispatchOutcome(
                        status=AgentRunStatus.FAILED,
                        output={},
                        output_ref=None,
                        evidence_refs=[],
                        error_code=code,
                    ),
                    attempt,
                )
        raise RuntimeError("unreachable dispatch retry state") from last_error

    async def dispatch_ready_subtasks(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "dispatch_ready_subtasks")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        request = request_from_state(state)
        child_outputs = dict(state.get("child_outputs", {}))
        for plan_data in state.get("plan", []):
            plan = SubTaskPlan.model_validate(plan_data)
            await repository.mark_subtask_running(plan.task_id)
            outcome, retry_count = await _dispatch_one(request, plan)
            await repository.complete_subtask(
                plan.task_id,
                status=outcome.status,
                output=outcome.output,
                output_ref=outcome.output_ref,
                evidence_refs=outcome.evidence_refs,
                error_code=outcome.error_code,
                approval_id=outcome.approval_id,
                external_task_id=outcome.external_task_id,
                retry_count=retry_count,
            )
            child_outputs[plan.agent_type.value] = outcome.output
        return {
            "child_outputs": child_outputs,
            "step_count": state["step_count"] + 1,
        }

    async def collect_subtask_results(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "collect_subtask_results")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        rows = await repository.list_subtasks(state["run_id"])
        evidence: set[str] = set(state.get("evidence_refs", []))
        failures: list[dict[str, str]] = []
        approval_id = None
        for row in rows:
            evidence.update(row.evidence_refs)
            if row.status == AgentRunStatus.FAILED.value:
                failures.append(
                    {
                        "subtask_id": row.subtask_id,
                        "agent_type": row.agent_type,
                        "error_code": row.error_code or "SUBTASK_FAILED",
                    }
                )
            if row.status == AgentRunStatus.WAITING_APPROVAL.value:
                approval_id = row.approval_id
        child = state.get("child_outputs", {})
        return {
            "subtasks": [repository.subtask_view(row).model_dump(mode="json") for row in rows],
            "customer_service_result": child.get(AgentTarget.CUSTOMER_SERVICE.value),
            "operations_result": child.get(AgentTarget.OPERATIONS.value),
            "media_result": child.get(AgentTarget.MEDIA.value),
            "evidence_refs": sorted(evidence),
            "partial_failures": failures,
            "approval_id": approval_id,
            "step_count": state["step_count"] + 1,
        }

    async def resolve_waiting_or_partial_state(
        state: OrchestratorState,
    ) -> dict[str, Any]:
        await node_event(state, "resolve_waiting_or_partial_state")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        waiting = [
            item
            for item in state.get("subtasks", [])
            if item["status"] == AgentRunStatus.WAITING_APPROVAL.value
        ]
        if not waiting:
            return {"step_count": state["step_count"] + 1}
        wait_state: OrchestratorState = {
            **state,
            "status": AgentRunStatus.WAITING_APPROVAL.value,
            "need_human": True,
            "approval_id": waiting[0].get("approval_id"),
        }
        await repository.persist_state(wait_state, AgentRunStatus.WAITING_APPROVAL)
        interrupt(
            {
                "run_id": state["run_id"],
                "child_run_id": waiting[0].get("child_run_id"),
                "approval_id": waiting[0].get("approval_id"),
            }
        )
        plan = next(
            SubTaskPlan.model_validate(item)
            for item in state["plan"]
            if item["agent_type"] == AgentTarget.CUSTOMER_SERVICE.value
        )
        outcome = await adapters.refresh_customer(plan.child_run_id, state["principal_id"])
        await repository.complete_subtask(
            plan.task_id,
            status=outcome.status,
            output=outcome.output,
            output_ref=outcome.output_ref,
            evidence_refs=outcome.evidence_refs,
            error_code=outcome.error_code,
            approval_id=outcome.approval_id,
        )
        child = dict(state.get("child_outputs", {}))
        child[AgentTarget.CUSTOMER_SERVICE.value] = outcome.output
        return {
            "child_outputs": child,
            "customer_service_result": outcome.output,
            "approval_id": None,
            "need_human": outcome.status == AgentRunStatus.WAITING_APPROVAL,
            "step_count": state["step_count"] + 1,
        }

    async def aggregate_grounded_result(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "aggregate_grounded_result")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        rows = await repository.list_subtasks(state["run_id"])
        child = dict(state.get("child_outputs", {}))
        for row in rows:
            if row.output_summary:
                child[row.agent_type] = row.output_summary
        evidence = sorted({ref for row in rows for ref in row.evidence_refs})
        failures = [
            {
                "subtask_id": row.subtask_id,
                "agent_type": row.agent_type,
                "error_code": row.error_code or "SUBTASK_FAILED",
            }
            for row in rows
            if row.status == AgentRunStatus.FAILED.value
        ]
        return {
            "subtasks": [repository.subtask_view(row).model_dump(mode="json") for row in rows],
            "child_outputs": child,
            "customer_service_result": child.get(AgentTarget.CUSTOMER_SERVICE.value),
            "operations_result": child.get(AgentTarget.OPERATIONS.value),
            "media_result": child.get(AgentTarget.MEDIA.value),
            "evidence_refs": evidence,
            "partial_failures": failures,
            "step_count": state["step_count"] + 1,
        }

    async def validate_final_result(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "validate_final_result")
        if state.get("error"):
            return {}
        if guarded := advance(state):
            return guarded
        statuses = [AgentRunStatus(item["status"]) for item in state.get("subtasks", [])]
        failed = statuses.count(AgentRunStatus.FAILED)
        if statuses and failed == len(statuses):
            status = AgentRunStatus.FAILED
        elif failed:
            status = AgentRunStatus.PARTIAL_SUCCESS
        else:
            status = AgentRunStatus.SUCCEEDED
        route = RouteDecision.model_validate(state["route"])
        warnings = list(state.get("warnings", []))
        if AgentTarget.MEDIA in route.agent_targets and len(route.agent_targets) > 1:
            status = AgentRunStatus.PARTIAL_SUCCESS
            warnings.append("媒体子任务已异步调度并等待人工审核；其他已完成结果仍然保留。")
        return {
            "status": status.value,
            "warnings": list(dict.fromkeys(warnings)),
            "need_human": bool(state.get("need_human")) or status == AgentRunStatus.PARTIAL_SUCCESS,
            "step_count": state["step_count"] + 1,
        }

    async def finalize_run(state: OrchestratorState) -> dict[str, Any]:
        await node_event(state, "finalize_run")
        status = AgentRunStatus(state.get("status", AgentRunStatus.FAILED.value))
        if state.get("error"):
            status = AgentRunStatus.FAILED
        await repository.persist_state(state, status)
        return {"status": status.value}

    graph = StateGraph(OrchestratorState)
    nodes = [
        ("validate_request", validate_request),
        ("resolve_principal", resolve_principal),
        ("classify_intents", classify_intents),
        ("validate_required_inputs", validate_required_inputs),
        ("build_structured_plan", build_structured_plan),
        ("validate_plan_and_tool_scopes", validate_plan_and_tool_scopes),
        ("create_subtasks", create_subtasks),
        ("dispatch_ready_subtasks", dispatch_ready_subtasks),
        ("collect_subtask_results", collect_subtask_results),
        ("resolve_waiting_or_partial_state", resolve_waiting_or_partial_state),
        ("aggregate_grounded_result", aggregate_grounded_result),
        ("validate_final_result", validate_final_result),
        ("finalize_run", finalize_run),
    ]
    for name, node in nodes:
        graph.add_node(name, node)
    graph.add_edge(START, nodes[0][0])
    for current, following in zip(nodes, nodes[1:], strict=False):
        graph.add_edge(current[0], following[0])
    graph.add_edge(nodes[-1][0], END)
    return graph.compile(checkpointer=checkpointer)
