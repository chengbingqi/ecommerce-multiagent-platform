from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from app.agents.customer_service.graph import build_customer_service_graph
from app.agents.customer_service.llm import FakeCustomerLLM
from app.agents.customer_service.mcp_client import FakeMCPToolClient
from app.agents.customer_service.repository import CustomerServiceRepository
from app.agents.customer_service.schemas import (
    AgentState,
    GroundedClaim,
    GroundedDraft,
)
from app.core.config import Settings
from app.main import app
from mcp_servers.common.contracts import (
    Evidence,
    ToolErrorData,
    ToolMeta,
    ToolResult,
)


def result(
    tool: str,
    *,
    data: dict[str, Any] | None = None,
    evidence: list[Evidence] | None = None,
    error: tuple[str, bool] | None = None,
) -> ToolResult:
    meta = ToolMeta(
        tool_name=tool,
        tool_version="1.0.0",
        run_id="run-placeholder",
        task_id="task-placeholder",
        trace_id="trace-placeholder",
        latency_ms=1,
    )
    if error:
        return ToolResult(
            ok=False,
            error=ToolErrorData(
                error_code=error[0],
                message="controlled tool failure",
                retryable=error[1],
                trace_id="trace-placeholder",
            ),
            meta=meta,
        )
    return ToolResult(ok=True, data=data or {}, evidence=evidence or [], meta=meta)


async def fake_case(
    factory,
    settings: Settings,
    *,
    message: str,
    order_id: str | None,
    responses: dict[str, ToolResult | list[ToolResult]],
    suffix: str,
    llm: FakeCustomerLLM | None = None,
):
    state: AgentState = {
        "message": message,
        "principal_id": "admin-001",
        "order_id": order_id,
        "run_id": f"run-failure-{suffix}",
        "thread_id": f"thread-failure-{suffix}",
        "trace_id": f"trace-failure-{suffix}",
    }
    repository = CustomerServiceRepository(factory)
    tools = FakeMCPToolClient(responses)
    graph = build_customer_service_graph(
        repository=repository,
        llm=llm or FakeCustomerLLM(),
        tools=tools,
        settings=settings,
        checkpointer=InMemorySaver(),
    )
    await repository.create_run(state)
    await graph.ainvoke(state, {"configurable": {"thread_id": state["thread_id"]}})
    return await repository.get_output(state["run_id"], "admin-001"), tools


def order_success(tool: str = "get_order") -> ToolResult:
    return result(
        tool,
        data={
            "order_id": "order-001",
            "store_id": "store-001",
            "status": "shipped",
            "total_amount": "10.00",
            "currency": "CNY",
            "items": [],
        },
        evidence=[
            Evidence(
                evidence_ref="db://orders/order-001",
                source_type="database",
                record_id="order-001",
            )
        ],
    )


@pytest.mark.asyncio
async def test_transient_read_retries_once_but_permanent_error_does_not(
    stage3_factory, stage3_settings
) -> None:
    recovered, recovered_tools = await fake_case(
        stage3_factory,
        stage3_settings,
        message="查询订单",
        order_id="order-001",
        responses={"get_order": [result("get_order", error=("TIMEOUT", True)), order_success()]},
        suffix="transient",
    )
    assert recovered.error is None
    assert recovered_tools.call_counts["get_order"] == 2
    failed, failed_tools = await fake_case(
        stage3_factory,
        stage3_settings,
        message="查询订单",
        order_id="order-001",
        responses={"get_order": result("get_order", error=("NOT_FOUND", False))},
        suffix="permanent",
    )
    assert failed.error and failed.error.error_code == "NOT_FOUND"
    assert failed_tools.call_counts["get_order"] == 1


@pytest.mark.asyncio
async def test_no_current_policy_evidence_hands_off(stage3_factory, stage3_settings) -> None:
    output, tools = await fake_case(
        stage3_factory,
        stage3_settings,
        message="退款政策",
        order_id=None,
        responses={"search_policy": result("search_policy", data={"results": []})},
        suffix="no-policy",
    )
    assert output.need_human is True
    assert output.handoff_reason == "NO_CURRENT_POLICY_EVIDENCE"
    assert tools.calls == ["search_policy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "invalid"])
async def test_llm_failure_falls_back_to_human(stage3_factory, stage3_settings, mode) -> None:
    llm = FakeCustomerLLM(timeout=mode == "timeout", invalid_json=mode == "invalid")
    output, tools = await fake_case(
        stage3_factory,
        stage3_settings,
        message="查询订单",
        order_id="order-001",
        responses={"get_order": order_success()},
        suffix=f"llm-{mode}",
        llm=llm,
    )
    assert output.need_human is True
    assert output.error is not None
    assert tools.calls == []


@pytest.mark.asyncio
async def test_max_steps_terminates_graph(stage3_factory, stage3_settings) -> None:
    limited = stage3_settings.model_copy(update={"agent_max_steps": 6})
    output, _ = await fake_case(
        stage3_factory,
        limited,
        message="查询订单",
        order_id="order-001",
        responses={"get_order": order_success()},
        suffix="max-steps",
    )
    assert output.error and output.error.error_code == "MAX_STEPS_EXCEEDED"


class InvalidEvidenceLLM(FakeCustomerLLM):
    async def generate(self, payload: dict[str, Any]) -> GroundedDraft:
        del payload
        self.call_count += 1
        return GroundedDraft(
            answer="伪造结论",
            claims=[GroundedClaim(text="伪造结论", evidence_refs=["db://orders/missing"])],
        )


class ConflictingAnswerLLM(FakeCustomerLLM):
    async def generate(self, payload: dict[str, Any]) -> GroundedDraft:
        self.call_count += 1
        reference = payload["evidence"][0]["evidence_ref"]
        return GroundedDraft(
            answer="订单已经取消。",
            claims=[GroundedClaim(text="订单已经取消。", evidence_refs=[reference])],
        )


@pytest.mark.asyncio
async def test_invalid_reference_is_rejected_and_tool_data_wins_conflict(
    stage3_factory, stage3_settings
) -> None:
    invalid, _ = await fake_case(
        stage3_factory,
        stage3_settings,
        message="查询订单",
        order_id="order-001",
        responses={"get_order": order_success()},
        suffix="invalid-reference",
        llm=InvalidEvidenceLLM(),
    )
    assert invalid.need_human and invalid.handoff_reason == "INVALID_EVIDENCE_REFERENCE"
    conflict, _ = await fake_case(
        stage3_factory,
        stage3_settings,
        message="查询订单",
        order_id="order-001",
        responses={"get_order": order_success()},
        suffix="conflict",
        llm=ConflictingAnswerLLM(),
    )
    assert conflict.answer == "订单当前状态为shipped。"


def test_openapi_contract_and_production_identity_gate() -> None:
    schema = app.openapi()
    assert "/api/v1/runs" in schema["paths"]
    assert "/api/v1/runs/{run_id}" in schema["paths"]
    assert "/api/v1/approvals/{approval_id}/decision" in schema["paths"]
    with pytest.raises(ValidationError, match="development principal input"):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://example.invalid/db",
            redis_url="redis://example.invalid/0",
            enable_dev_principal_input=True,
        )
