import time
from decimal import Decimal
from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph

from app.agents.customer_service.llm import LLMFailure
from app.agents.customer_service.repository import RepositoryFailure
from app.agents.customer_service.schemas import AgentRunStatus
from app.agents.operations.anomaly_rules import detect_anomalies
from app.agents.operations.mcp_client import OPERATIONS_TOOL_WHITELIST, OperationsToolClient
from app.agents.operations.provider import OperationsLLM
from app.agents.operations.repository import OperationsRepository
from app.agents.operations.schemas import OperationsState, OperationsTopic
from app.core.config import Settings
from mcp_servers.common.contracts import CallContext

FORBIDDEN_MARKERS = (
    "create_refund_request",
    "check_refund_eligibility",
    "get_order",
    "probe_media",
    "media-mcp",
    "submit_transcription",
    "sql",
    "shell",
    "docker socket",
    "/var/run/docker.sock",
    "文件系统",
    "执行命令",
    "退款工具",
    "客服工具",
)


def _failure(
    state: OperationsState, code: str, message: str, retryable: bool = False
) -> dict[str, Any]:
    return {
        "error": {
            "error_code": code,
            "message": message,
            "retryable": retryable,
            "trace_id": state["trace_id"],
        },
        "explanation": message,
        "need_human": retryable,
        "warnings": [code],
    }


def build_operations_graph(
    *,
    repository: OperationsRepository,
    llm: OperationsLLM,
    tools: OperationsToolClient,
    settings: Settings,
    checkpointer: Any = None,
) -> Any:
    def advance(state: OperationsState) -> dict[str, Any] | None:
        steps = state.get("step_count", 0) + 1
        if steps > settings.agent_max_steps:
            return {
                **_failure(state, "MAX_STEPS_EXCEEDED", "运营分析已达到最大步骤数。"),
                "step_count": steps,
            }
        started = state.get("started_monotonic", time.monotonic())
        if time.monotonic() - started > settings.agent_deadline_seconds:
            return {
                **_failure(state, "DEADLINE_EXCEEDED", "运营分析处理超时。", True),
                "step_count": steps,
            }
        return None

    async def validate_request(state: OperationsState) -> dict[str, Any]:
        return {
            "step_count": 1,
            "started_monotonic": time.monotonic(),
            "metrics": [],
            "trend_series": [],
            "evidence": [],
            "evidence_rows": [],
            "anomalies": [],
            "recommendations": [],
            "warnings": [],
            "need_human": False,
            "no_data": False,
            "llm_calls": 0,
        }

    async def resolve_principal(state: OperationsState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        try:
            principal = await repository.resolve_principal(state["principal_id"])
        except RepositoryFailure as exc:
            return {**_failure(state, exc.code, exc.message), "step_count": state["step_count"] + 1}
        allowed = {"store_operator", "agent:operations", "admin"}
        if principal.role not in allowed or (
            principal.role != "admin" and principal.store_id != state["requested_store_id"]
        ):
            await repository.audit_forbidden(state, "operations_scope_denied")
            return {
                **_failure(state, "FORBIDDEN", "主体没有该店铺的运营分析权限。"),
                "step_count": state["step_count"] + 1,
            }
        return {
            "role": principal.role,
            "store_id": state["requested_store_id"],
            "step_count": state["step_count"] + 1,
        }

    async def normalize_store_and_period(state: OperationsState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        return {"step_count": state["step_count"] + 1}

    async def classify_operations_topic(state: OperationsState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        text = (state.get("user_question") or "").lower()
        if any(marker in text for marker in FORBIDDEN_MARKERS):
            await repository.audit_forbidden(state, "forbidden_operations_request")
            return {
                "topic": OperationsTopic.FORBIDDEN_REQUEST.value,
                **_failure(state, "FORBIDDEN", "请求超出只读运营Agent工具范围。"),
                "step_count": state["step_count"] + 1,
            }
        return {"step_count": state["step_count"] + 1}

    async def plan_allowed_tools(state: OperationsState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        if state.get("error"):
            return {"planned_tools": [], "step_count": state["step_count"] + 1}
        planned = ["get_operations_metrics"]
        if not set(planned).issubset(OPERATIONS_TOOL_WHITELIST):
            await repository.audit_forbidden(state, "illegal_operations_plan")
            return {
                **_failure(state, "FORBIDDEN", "工具计划超出运营白名单。"),
                "step_count": state["step_count"] + 1,
            }
        return {"planned_tools": planned, "step_count": state["step_count"] + 1}

    async def execute_operations_tools(state: OperationsState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        if state.get("error"):
            return {"step_count": state["step_count"] + 1}
        context = CallContext(
            principal_id=state["principal_id"],
            agent="operations",
            run_id=state["run_id"],
            task_id=f"{state['run_id']}-metrics"[:80],
            trace_id=state["trace_id"],
        )
        result = await tools.call(
            "get_operations_metrics",
            {
                "store_id": state["store_id"],
                "start_at": state["start_at"],
                "end_at": state["end_at"],
                "granularity": state["granularity"],
                "comparison_mode": state["comparison_mode"],
            },
            context,
        )
        if not result.ok and result.error and result.error.retryable:
            result = await tools.call(
                "get_operations_metrics",
                {
                    "store_id": state["store_id"],
                    "start_at": state["start_at"],
                    "end_at": state["end_at"],
                    "granularity": state["granularity"],
                    "comparison_mode": state["comparison_mode"],
                },
                context,
            )
        if not result.ok:
            assert result.error is not None
            return {
                **_failure(
                    state, result.error.error_code, result.error.message, result.error.retryable
                ),
                "step_count": state["step_count"] + 1,
            }
        data = cast(dict[str, Any], result.data)
        return {
            "metrics": data["metrics"],
            "trend_series": data["trend_series"],
            "scenario_counts": data["scenario_counts"],
            "evidence_rows": data["evidence_rows"],
            "evidence": [row.model_dump(mode="json") for row in result.evidence],
            "step_count": state["step_count"] + 1,
        }

    async def validate_metric_evidence(state: OperationsState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        if state.get("error"):
            return {"step_count": state["step_count"] + 1}
        allowed = {row["evidence_ref"] for row in state.get("evidence", [])}
        invalid = any(
            not set(row["evidence_refs"]).issubset(allowed)
            for row in [*state.get("metrics", []), *state.get("trend_series", [])]
        ) or any(
            row["store_id"] != state["store_id"] or row["evidence_ref"] not in allowed
            for row in state.get("evidence_rows", [])
        )
        if invalid:
            return {
                **_failure(state, "INVALID_EVIDENCE_REFERENCE", "运营指标证据校验失败。"),
                "step_count": state["step_count"] + 1,
            }
        temporal_keys = {
            "order_count",
            "refund_count",
            "visitor_count",
            "delayed_logistics_count",
            "campaign_count",
        }
        values = {row["metric_key"]: Decimal(str(row["value"])) for row in state["metrics"]}
        return {
            "no_data": all(values[key] == 0 for key in temporal_keys),
            "step_count": state["step_count"] + 1,
        }

    async def detect_deterministic_anomalies(state: OperationsState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        if state.get("error"):
            return {"step_count": state["step_count"] + 1}
        if state.get("no_data"):
            return {"anomalies": [], "step_count": state["step_count"] + 1}
        anomalies = detect_anomalies(state["metrics"], state.get("scenario_counts", {}))
        return {
            "anomalies": [row.model_dump(mode="json") for row in anomalies],
            "step_count": state["step_count"] + 1,
        }

    async def retrieve_relevant_sop(state: OperationsState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        if state.get("error") or not state.get("anomalies"):
            return {"step_count": state["step_count"] + 1}
        context = CallContext(
            principal_id=state["principal_id"],
            agent="operations",
            run_id=state["run_id"],
            task_id=f"{state['run_id']}-sop"[:80],
            trace_id=state["trace_id"],
        )
        result = await tools.call(
            "retrieve_sop", {"name": "退款处理", "store_id": state["store_id"]}, context
        )
        if not result.ok:
            return {
                "warnings": [*state.get("warnings", []), "SOP_UNAVAILABLE"],
                "step_count": state["step_count"] + 1,
            }
        data = cast(dict[str, Any], result.data)
        evidence = [
            *state.get("evidence", []),
            *(row.model_dump(mode="json") for row in result.evidence),
        ]
        return {"sop": data, "evidence": evidence, "step_count": state["step_count"] + 1}

    async def generate_grounded_explanation(state: OperationsState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        if state.get("error"):
            return {"step_count": state["step_count"] + 1}
        metric_values = {row["metric_key"]: row["value"] for row in state["metrics"]}
        try:
            draft = await llm.generate(
                {
                    "metric_values": metric_values,
                    "anomalies": state["anomalies"],
                    "sop": state.get("sop"),
                }
            )
        except LLMFailure as exc:
            return {
                "explanation": "确定性指标已生成，但模型解释不可用。",
                "recommendations": [],
                "need_human": True,
                "warnings": [*state.get("warnings", []), exc.code],
                "step_count": state["step_count"] + 1,
                "llm_calls": llm.call_count,
            }
        return {
            "explanation": draft.explanation,
            "recommendations": [row.model_dump(mode="json") for row in draft.recommendations],
            "metric_echo": draft.metric_echo,
            "step_count": state["step_count"] + 1,
            "llm_calls": llm.call_count,
        }

    async def validate_final_output(state: OperationsState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        if state.get("error"):
            return {"step_count": state["step_count"] + 1}
        expected = {row["metric_key"]: row["value"] for row in state["metrics"]}
        metric_echo = cast(dict[str, Any], state.get("metric_echo", expected))
        allowed_refs = {row["evidence_ref"] for row in state.get("evidence", [])}
        anomaly_ids = {row["anomaly_id"] for row in state.get("anomalies", [])}
        invalid = metric_echo != expected or any(
            not set(row["evidence_refs"]).issubset(allowed_refs)
            or not set(row["based_on_anomaly_ids"]).issubset(anomaly_ids)
            for row in state.get("recommendations", [])
        )
        if invalid:
            return {
                "explanation": "模型输出未通过数值或证据校验，已保留确定性事实。",
                "recommendations": [],
                "need_human": True,
                "warnings": [*state.get("warnings", []), "LLM_GROUNDING_REJECTED"],
                "step_count": state["step_count"] + 1,
            }
        return {"step_count": state["step_count"] + 1}

    async def finalize_result(state: OperationsState) -> dict[str, Any]:
        status = AgentRunStatus.FAILED if state.get("error") else AgentRunStatus.SUCCEEDED
        await repository.persist_state(state, status)
        return {}

    builder = StateGraph(OperationsState)
    nodes = [
        validate_request,
        resolve_principal,
        normalize_store_and_period,
        classify_operations_topic,
        plan_allowed_tools,
        execute_operations_tools,
        validate_metric_evidence,
        detect_deterministic_anomalies,
        retrieve_relevant_sop,
        generate_grounded_explanation,
        validate_final_output,
        finalize_result,
    ]
    for node in nodes:
        builder.add_node(node.__name__, node)
    builder.add_edge(START, "validate_request")
    builder.add_conditional_edges(
        "validate_request", _route, {"continue": "resolve_principal", "finalize": "finalize_result"}
    )
    builder.add_conditional_edges(
        "resolve_principal",
        _route,
        {"continue": "normalize_store_and_period", "finalize": "finalize_result"},
    )
    ordered = [
        "normalize_store_and_period",
        "classify_operations_topic",
        "plan_allowed_tools",
        "execute_operations_tools",
        "validate_metric_evidence",
        "detect_deterministic_anomalies",
        "retrieve_relevant_sop",
        "generate_grounded_explanation",
        "validate_final_output",
        "finalize_result",
    ]
    for current, following in zip(ordered, ordered[1:], strict=False):
        builder.add_edge(current, following)
    builder.add_edge("finalize_result", END)
    return builder.compile(checkpointer=checkpointer, name="operations_graph")


def _route(state: OperationsState) -> Literal["continue", "finalize"]:
    return "finalize" if state.get("error") else "continue"
