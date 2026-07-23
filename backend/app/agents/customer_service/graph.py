import time
from decimal import Decimal
from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.customer_service.llm import CustomerLLM, LLMFailure
from app.agents.customer_service.mcp_client import (
    CUSTOMER_SERVICE_TOOL_WHITELIST,
    READ_ONLY_TOOLS,
    MCPToolClient,
)
from app.agents.customer_service.repository import (
    CustomerServiceRepository,
    RepositoryFailure,
)
from app.agents.customer_service.schemas import (
    AgentRunStatus,
    AgentState,
    CustomerIntent,
    RiskLevel,
)
from app.core.config import Settings
from mcp_servers.common.contracts import CallContext, ToolResult
from mcp_servers.common.governance import action_digest

FORBIDDEN_MARKERS = (
    "get_sales_metrics",
    "query_inventory",
    "销售分析",
    "运营工具",
    "库存分析",
    "media-mcp",
    "probe_media",
    "submit_transcription",
    "sql",
    "shell",
    "docker socket",
    "/var/run/docker.sock",
    "执行命令",
)


def _error(state: AgentState, code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {
        "error": {
            "error_code": code,
            "message": message,
            "retryable": retryable,
            "trace_id": state["trace_id"],
        },
        "answer": message,
        "need_human": retryable,
        "handoff_reason": code if retryable else None,
        "risk": RiskLevel.HIGH.value if code == "FORBIDDEN" else RiskLevel.MEDIUM.value,
    }


def build_customer_service_graph(
    *,
    repository: CustomerServiceRepository,
    llm: CustomerLLM,
    tools: MCPToolClient,
    settings: Settings,
    checkpointer: Any = None,
) -> Any:
    def advance(state: AgentState) -> dict[str, Any] | None:
        steps = state.get("step_count", 0) + 1
        if steps > settings.agent_max_steps:
            return {
                **_error(state, "MAX_STEPS_EXCEEDED", "客服流程已达到最大步骤数。"),
                "step_count": steps,
            }
        started = state.get("started_monotonic", time.monotonic())
        if time.monotonic() - started > settings.agent_deadline_seconds:
            return {
                **_error(state, "DEADLINE_EXCEEDED", "客服流程处理超时。", True),
                "step_count": steps,
            }
        return None

    async def validate_input(state: AgentState) -> dict[str, Any]:
        if not state.get("message", "").strip():
            return _error(state, "INVALID_ARGUMENT", "message不能为空。")
        return {
            "step_count": 1,
            "started_monotonic": state.get("started_monotonic", time.monotonic()),
            "status": AgentRunStatus.RUNNING.value,
            "need_human": False,
            "risk": RiskLevel.LOW.value,
            "llm_calls": 0,
            "evidence": [],
            "tool_results": {},
        }

    async def resolve_principal(state: AgentState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        try:
            principal = await repository.resolve_principal(state["principal_id"])
        except RepositoryFailure as exc:
            return {**_error(state, exc.code, exc.message), "step_count": state["step_count"] + 1}
        return {
            "role": principal.role,
            "store_id": principal.store_id,
            "customer_ref": principal.customer_ref,
            "step_count": state["step_count"] + 1,
        }

    async def classify_customer_intent(state: AgentState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        lowered = state["message"].lower()
        if any(marker in lowered for marker in FORBIDDEN_MARKERS):
            await repository.audit_forbidden_tool(state, "forbidden_customer_request")
            return {
                "intent": CustomerIntent.FORBIDDEN_REQUEST.value,
                **_error(state, "FORBIDDEN", "该请求超出客服Agent允许的工具范围。"),
                "step_count": state["step_count"] + 1,
            }
        try:
            classification = await llm.classify(state["message"], state.get("order_id"))
        except LLMFailure as exc:
            return {
                "intent": CustomerIntent.UNKNOWN.value,
                **_error(state, exc.code, "意图识别失败，建议转人工处理。", exc.retryable),
                "need_human": True,
                "handoff_reason": exc.code,
                "step_count": state["step_count"] + 1,
                "llm_calls": llm.call_count,
            }
        return {
            "intent": classification.intent.value,
            "order_id": classification.order_id or state.get("order_id"),
            "refund_amount": classification.refund_amount,
            "reason_code": classification.reason_code or "CUSTOMER_REQUEST",
            "step_count": state["step_count"] + 1,
            "llm_calls": llm.call_count,
        }

    async def plan_allowed_tools(state: AgentState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        intent = CustomerIntent(state.get("intent", CustomerIntent.UNKNOWN.value))
        plans = {
            CustomerIntent.ORDER_STATUS: ["get_order"],
            CustomerIntent.LOGISTICS_STATUS: ["get_order", "get_logistics"],
            CustomerIntent.POLICY_QUERY: ["search_policy", "get_document_chunk"],
            CustomerIntent.REFUND_ELIGIBILITY: ["check_refund_eligibility"],
            CustomerIntent.REFUND_REQUEST: ["get_order", "check_refund_eligibility"],
        }
        planned = plans.get(intent, [])
        if not set(planned).issubset(CUSTOMER_SERVICE_TOOL_WHITELIST):
            await repository.audit_forbidden_tool(state, "illegal_planner_output")
            return {
                **_error(state, "FORBIDDEN", "工具计划超出客服白名单。"),
                "step_count": state["step_count"] + 1,
            }
        if intent in {
            CustomerIntent.ORDER_STATUS,
            CustomerIntent.LOGISTICS_STATUS,
            CustomerIntent.REFUND_ELIGIBILITY,
            CustomerIntent.REFUND_REQUEST,
        } and not state.get("order_id"):
            return {
                **_error(state, "INVALID_ARGUMENT", "该请求需要有效的order_id。"),
                "step_count": state["step_count"] + 1,
            }
        if intent == CustomerIntent.UNKNOWN:
            return {
                "planned_tools": [],
                "need_human": True,
                "handoff_reason": "UNKNOWN_INTENT",
                "answer": "当前问题超出自动客服已验证能力，建议转人工处理。",
                "step_count": state["step_count"] + 1,
            }
        return {"planned_tools": planned, "step_count": state["step_count"] + 1}

    async def execute_mcp_tools(state: AgentState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        summaries: dict[str, dict[str, Any]] = dict(state.get("tool_results", {}))
        evidence: list[dict[str, Any]] = list(state.get("evidence", []))
        planned = state.get("planned_tools", [])
        for index, tool_name in enumerate(planned):
            if tool_name == "get_document_chunk":
                search = summaries.get("search_policy", {})
                results = cast(list[dict[str, Any]], search.get("results", []))
                if not results:
                    return {
                        "tool_results": summaries,
                        "evidence": evidence,
                        "need_human": True,
                        "handoff_reason": "NO_CURRENT_POLICY_EVIDENCE",
                        "answer": "没有找到当前有效的政策证据，建议转人工处理。",
                        "step_count": state["step_count"] + 1,
                    }
                arguments = {"chunk_id": results[0]["chunk_id"]}
            elif tool_name == "search_policy":
                arguments = {
                    "query": "退款" if "退款" in state["message"] else state["message"][:40],
                    "store_id": state.get("store_id"),
                    "limit": 5,
                }
            else:
                arguments = {"order_id": state["order_id"]}
            context = CallContext(
                principal_id=state["principal_id"],
                agent="customer_service",
                run_id=state["run_id"],
                task_id=f"{state['run_id']}-{index}-{tool_name}"[:80],
                trace_id=state["trace_id"],
            )
            result = await tools.call(tool_name, arguments, context)
            if (
                not result.ok
                and result.error
                and result.error.retryable
                and tool_name in READ_ONLY_TOOLS
            ):
                result = await tools.call(tool_name, arguments, context)
            if not result.ok:
                assert result.error is not None
                return {
                    "tool_results": summaries,
                    "evidence": evidence,
                    **_error(
                        state,
                        result.error.error_code,
                        result.error.message,
                        result.error.retryable,
                    ),
                    "step_count": state["step_count"] + 1,
                }
            summaries[tool_name] = _summarize_tool_data(tool_name, result)
            evidence.extend(item.model_dump(mode="json") for item in result.evidence)
        update: dict[str, Any] = {
            "tool_results": summaries,
            "evidence": _unique_evidence(evidence),
            "step_count": state["step_count"] + 1,
        }
        if "get_order" in summaries:
            update["order_summary"] = summaries["get_order"]
        if "check_refund_eligibility" in summaries:
            update["refund_eligibility"] = summaries["check_refund_eligibility"]
        return update

    async def validate_evidence(state: AgentState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        if state.get("error") or state.get("need_human"):
            return {"step_count": state["step_count"] + 1}
        intent = CustomerIntent(state.get("intent", CustomerIntent.UNKNOWN.value))
        factual = intent not in {CustomerIntent.UNKNOWN, CustomerIntent.FORBIDDEN_REQUEST}
        if factual and not state.get("evidence"):
            return {
                "need_human": True,
                "handoff_reason": "MISSING_EVIDENCE",
                "answer": "缺少可验证证据，无法给出业务结论。",
                "step_count": state["step_count"] + 1,
            }
        return {"step_count": state["step_count"] + 1}

    async def generate_grounded_answer(state: AgentState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        if state.get("error") or state.get("need_human"):
            return {"step_count": state["step_count"] + 1}
        payload = {
            "intent": state["intent"],
            "order_summary": state.get("order_summary"),
            "refund_eligibility": state.get("refund_eligibility"),
            "tool_summaries": state.get("tool_results", {}),
            "policy_excerpt": state.get("tool_results", {})
            .get("get_document_chunk", {})
            .get("content", ""),
            "evidence": state.get("evidence", []),
        }
        try:
            draft = await llm.generate(payload)
        except LLMFailure as exc:
            return {
                **_error(state, exc.code, "回答生成失败，建议转人工处理。", exc.retryable),
                "need_human": True,
                "handoff_reason": exc.code,
                "step_count": state["step_count"] + 1,
                "llm_calls": llm.call_count,
            }
        allowed_refs = {str(item["evidence_ref"]) for item in state.get("evidence", [])}
        claims = [claim.model_dump(mode="json") for claim in draft.claims]
        invalid_claim = any(
            not claim["evidence_refs"]
            or not set(cast(list[str], claim["evidence_refs"])).issubset(allowed_refs)
            for claim in claims
        )
        if invalid_claim:
            return {
                "answer": "模型回答缺少有效证据引用，建议转人工处理。",
                "need_human": True,
                "handoff_reason": "INVALID_EVIDENCE_REFERENCE",
                "step_count": state["step_count"] + 1,
                "llm_calls": llm.call_count,
            }
        return {
            "answer": _deterministic_answer(state, draft.answer),
            "claims": claims,
            "step_count": state["step_count"] + 1,
            "llm_calls": llm.call_count,
        }

    async def evaluate_risk(state: AgentState) -> dict[str, Any]:
        if guarded := advance(state):
            return guarded
        intent = CustomerIntent(state.get("intent", CustomerIntent.UNKNOWN.value))
        if intent != CustomerIntent.REFUND_REQUEST or state.get("error") or state.get("need_human"):
            return {
                "risk": state.get("risk", RiskLevel.LOW.value),
                "step_count": state["step_count"] + 1,
            }
        eligibility = state.get("refund_eligibility") or {}
        if not eligibility.get("eligible"):
            return {
                "risk": RiskLevel.MEDIUM.value,
                "answer": "该订单不符合当前模拟退款资格，未创建退款申请。",
                "step_count": state["step_count"] + 1,
            }
        order = state.get("order_summary") or {}
        amount = state.get("refund_amount") or str(order.get("total_amount", ""))
        try:
            normalized_amount = str(Decimal(amount).quantize(Decimal("0.01")))
        except Exception:
            return {
                **_error(state, "INVALID_ARGUMENT", "退款金额格式无效。"),
                "step_count": state["step_count"] + 1,
            }
        payload = {
            "order_id": state["order_id"],
            "amount": normalized_amount,
            "reason_code": state.get("reason_code") or "CUSTOMER_REQUEST",
        }
        digest = action_digest(payload)
        try:
            approval = await repository.ensure_approval(state, digest)
        except RepositoryFailure as exc:
            return {**_error(state, exc.code, exc.message), "step_count": state["step_count"] + 1}
        return {
            "refund_amount": normalized_amount,
            "reason_code": payload["reason_code"],
            "approval": approval.model_dump(mode="json"),
            "risk": RiskLevel.HIGH.value,
            "step_count": state["step_count"] + 1,
        }

    async def request_approval_or_handoff(state: AgentState) -> dict[str, Any]:
        approval = state.get("approval")
        if not approval or state.get("error"):
            return {}
        await repository.persist_state(state, AgentRunStatus.WAITING_APPROVAL)
        decision = interrupt(
            {
                "approval_id": approval["approval_id"],
                "run_id": state["run_id"],
                "thread_id": state["thread_id"],
                "action_digest": approval["action_digest"],
            }
        )
        if decision != "approved":
            return {
                "answer": "模拟退款申请已被拒绝，未创建退款请求。",
                "approval": {**approval, "status": "rejected"},
                "risk": RiskLevel.LOW.value,
            }
        arguments = {
            "order_id": state["order_id"],
            "amount": state["refund_amount"],
            "reason_code": state["reason_code"],
            "approval_id": approval["approval_id"],
            "idempotency_key": f"stage3-refund-{state['run_id']}"[:128],
        }
        context = CallContext(
            principal_id=state["principal_id"],
            agent="customer_service",
            run_id=state["run_id"],
            task_id=f"{state['run_id']}-refund"[:80],
            trace_id=state["trace_id"],
        )
        result = await tools.call("create_refund_request", arguments, context)
        if not result.ok:
            assert result.error is not None
            return _error(
                state,
                result.error.error_code,
                result.error.message,
                result.error.retryable,
            )
        await repository.mark_approval_used(str(approval["approval_id"]))
        return {
            "refund_request": result.data,
            "evidence": _unique_evidence(
                [
                    *state.get("evidence", []),
                    *(item.model_dump(mode="json") for item in result.evidence),
                ]
            ),
            "approval": {**approval, "status": "used"},
            "answer": "模拟退款请求已创建，状态为simulated_requested。",
            "risk": RiskLevel.LOW.value,
        }

    async def finalize_result(state: AgentState) -> dict[str, Any]:
        status = AgentRunStatus.FAILED if state.get("error") else AgentRunStatus.SUCCEEDED
        await repository.persist_state(state, status)
        return {"status": status.value}

    builder = StateGraph(AgentState)
    builder.add_node("validate_input", validate_input)
    builder.add_node("resolve_principal", resolve_principal)
    builder.add_node("classify_customer_intent", classify_customer_intent)
    builder.add_node("plan_allowed_tools", plan_allowed_tools)
    builder.add_node("execute_mcp_tools", execute_mcp_tools)
    builder.add_node("validate_evidence", validate_evidence)
    builder.add_node("generate_grounded_answer", generate_grounded_answer)
    builder.add_node("evaluate_risk", evaluate_risk)
    builder.add_node("request_approval_or_handoff", request_approval_or_handoff)
    builder.add_node("finalize_result", finalize_result)
    builder.add_edge(START, "validate_input")
    builder.add_conditional_edges(
        "validate_input",
        _route_error,
        {"continue": "resolve_principal", "finalize": "finalize_result"},
    )
    builder.add_conditional_edges(
        "resolve_principal",
        _route_error,
        {"continue": "classify_customer_intent", "finalize": "finalize_result"},
    )
    builder.add_edge("classify_customer_intent", "plan_allowed_tools")
    builder.add_conditional_edges(
        "plan_allowed_tools",
        _route_execution,
        {"execute": "execute_mcp_tools", "skip": "validate_evidence"},
    )
    builder.add_edge("execute_mcp_tools", "validate_evidence")
    builder.add_edge("validate_evidence", "generate_grounded_answer")
    builder.add_edge("generate_grounded_answer", "evaluate_risk")
    builder.add_edge("evaluate_risk", "request_approval_or_handoff")
    builder.add_edge("request_approval_or_handoff", "finalize_result")
    builder.add_edge("finalize_result", END)
    return builder.compile(checkpointer=checkpointer, name="customer_service_graph")


def _route_error(state: AgentState) -> Literal["continue", "finalize"]:
    return "finalize" if state.get("error") else "continue"


def _route_execution(state: AgentState) -> Literal["execute", "skip"]:
    return "execute" if state.get("planned_tools") and not state.get("error") else "skip"


def _summarize_tool_data(tool_name: str, result: ToolResult) -> dict[str, Any]:
    data = cast(dict[str, Any], result.data or {})
    if tool_name == "get_document_chunk":
        return {**data, "content": str(data.get("content", ""))[:800]}
    if tool_name == "search_policy":
        rows = cast(list[dict[str, Any]], data.get("results", []))
        return {
            "results": [{**row, "content": str(row.get("content", ""))[:500]} for row in rows[:5]]
        }
    return data


def _unique_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique[str(row["evidence_ref"])] = row
    return list(unique.values())


def _deterministic_answer(state: AgentState, draft: str) -> str:
    intent = CustomerIntent(state["intent"])
    summaries = state.get("tool_results", {})
    if intent == CustomerIntent.ORDER_STATUS:
        return f"订单当前状态为{summaries['get_order']['status']}。"
    if intent == CustomerIntent.LOGISTICS_STATUS:
        logistics = summaries["get_logistics"]
        return f"物流当前状态为{logistics['status']}，最近事件：{logistics['last_event']}。"
    if intent == CustomerIntent.POLICY_QUERY:
        excerpt = str(summaries["get_document_chunk"].get("content", ""))
        return f"当前有效政策说明：{excerpt}"
    if intent in {CustomerIntent.REFUND_ELIGIBILITY, CustomerIntent.REFUND_REQUEST}:
        eligible = bool(summaries["check_refund_eligibility"]["eligible"])
        return "该订单符合当前模拟退款资格。" if eligible else "该订单不符合当前模拟退款资格。"
    return draft
