import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Approval,
    KnowledgeChunk,
    KnowledgeDocument,
    Order,
    Principal,
    Refund,
    ToolCallAudit,
)
from mcp_servers.business.schemas import (
    InventoryInput,
    OrderInput,
    RefundRequestInput,
    SalesMetricsInput,
)
from mcp_servers.business.service import BusinessTools
from mcp_servers.common.contracts import CallContext
from mcp_servers.common.governance import action_digest
from mcp_servers.knowledge.schemas import ChunkInput, PolicySearchInput, SOPInput
from mcp_servers.knowledge.service import KnowledgeTools
from mcp_servers.media.schemas import MediaInput
from mcp_servers.media.service import MediaTools


def context(principal: str, trace: str = "trace-test") -> CallContext:
    return CallContext(
        principal_id=principal,
        agent="pytest",
        run_id="run-test",
        task_id="task-test",
        trace_id=trace,
    )


async def order_for(
    factory: async_sessionmaker[AsyncSession], *, customer: bool = False, second_store: bool = False
) -> Order:
    async with factory() as session:
        statement = select(Order).order_by(Order.id)
        if customer:
            statement = statement.where(Order.customer_ref == "customer-001")
        if second_store:
            operator_store = await session.scalar(
                select(Principal.store_id).where(Principal.id == "operator-store-001")
            )
            statement = statement.where(Order.store_id != operator_store)
        order = await session.scalar(statement)
        assert order is not None
        return order


@pytest.mark.asyncio
async def test_business_success_permissions_and_cross_store_denial(session_factory) -> None:
    service = BusinessTools(session_factory)
    own = await order_for(session_factory, customer=True)
    result = await service.get_order(OrderInput(context=context("customer-001"), order_id=own.id))
    assert result.ok and result.data["order_id"] == own.id
    other = await order_for(session_factory, second_store=True)
    denied = await service.get_order(
        OrderInput(context=context("operator-store-001", "trace-cross-store"), order_id=other.id)
    )
    assert not denied.ok and denied.error.error_code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_tool_whitelist_denial_is_audited(session_factory) -> None:
    order = await order_for(session_factory)
    service = BusinessTools(session_factory)
    denied = await service.query_inventory(
        InventoryInput(
            context=context("agent-customer-service", "trace-whitelist"),
            store_id=order.store_id,
        )
    )
    assert denied.error.error_code == "FORBIDDEN"
    async with session_factory() as session:
        audit = await session.scalar(
            select(ToolCallAudit).where(ToolCallAudit.trace_id == "trace-whitelist")
        )
        assert audit is not None
        assert audit.permission_result == "denied"
        assert audit.error_code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_sales_metrics_matches_direct_sql(session_factory) -> None:
    order = await order_for(session_factory)
    start = datetime(2026, 6, 24, tzinfo=UTC)
    end = datetime(2026, 7, 2, tzinfo=UTC)
    result = await BusinessTools(session_factory).get_sales_metrics(
        SalesMetricsInput(
            context=context("admin-001", "trace-metrics"),
            store_id=order.store_id,
            start_at=start,
            end_at=end,
        )
    )
    assert result.ok
    async with session_factory() as session:
        gmv, count = (
            await session.execute(
                select(func.sum(Order.total_amount), func.count(Order.id)).where(
                    Order.store_id == order.store_id,
                    Order.ordered_at >= start,
                    Order.ordered_at < end,
                )
            )
        ).one()
    assert Decimal(result.data["gmv"]) == Decimal(gmv)
    assert result.data["order_count"] == count


@pytest.mark.asyncio
async def test_refund_requires_approval_then_is_idempotent_and_conflict_safe(
    session_factory,
) -> None:
    order = await order_for(session_factory, customer=True)
    service = BusinessTools(session_factory)
    base = {
        "context": context("customer-001", "trace-refund-missing"),
        "order_id": order.id,
        "amount": Decimal("1.00"),
        "reason_code": "SIMULATED_TEST",
        "idempotency_key": "idempotency-stage2-test",
    }
    missing = await service.create_refund_request(RefundRequestInput(**base))
    assert missing.error.error_code == "APPROVAL_REQUIRED"

    payload = {"order_id": order.id, "amount": "1.00", "reason_code": "SIMULATED_TEST"}
    digest = action_digest(payload)
    approval_id = "approval-stage2-test"
    async with session_factory() as session, session.begin():
        session.add(
            Approval(
                id=approval_id,
                principal_id="customer-001",
                tool_name="create_refund_request",
                action_digest=digest,
                status="approved",
                approved_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    approved = RefundRequestInput(
        **{**base, "context": context("customer-001", "trace-refund-ok")},
        approval_id=approval_id,
    )
    first = await service.create_refund_request(approved)
    second = await service.create_refund_request(approved)
    assert first.ok and second.ok
    assert first.data == second.data
    conflict = await service.create_refund_request(
        approved.model_copy(update={"amount": Decimal("2.00"), "context": context("customer-001")})
    )
    assert conflict.error.error_code == "IDEMPOTENCY_CONFLICT"
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Refund)
                .where(Refund.id == first.data["refund_request_id"])
            )
        ) == 1


@pytest.mark.asyncio
async def test_current_knowledge_filter_empty_result_and_reverse_lookup(session_factory) -> None:
    service = KnowledgeTools(session_factory)
    current = await service.search_policy(
        PolicySearchInput(context=context("admin-001"), query="退款", limit=10)
    )
    assert current.ok and current.data["results"]
    assert all(item["version"] != "policy_v0" for item in current.data["results"])
    empty = await service.search_policy(
        PolicySearchInput(context=context("admin-001"), query="不存在的知识", limit=10)
    )
    assert empty.ok and empty.data["results"] == []
    chunk_id = current.data["results"][0]["chunk_id"]
    reverse = await service.get_document_chunk(
        ChunkInput(context=context("admin-001"), chunk_id=chunk_id)
    )
    assert reverse.ok
    assert reverse.data["content"] == current.data["results"][0]["content"]
    repeat = await service.search_policy(
        PolicySearchInput(context=context("admin-001"), query="退款", limit=10)
    )
    assert current.data["results"] == repeat.data["results"]


@pytest.mark.asyncio
async def test_expired_chunk_is_not_current(session_factory) -> None:
    async with session_factory() as session:
        chunk_id = await session.scalar(
            select(KnowledgeChunk.id)
            .join(KnowledgeDocument)
            .where(KnowledgeDocument.version == "policy_v0")
        )
    result = await KnowledgeTools(session_factory).get_document_chunk(
        ChunkInput(context=context("admin-001"), chunk_id=chunk_id)
    )
    assert result.error.error_code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_knowledge_store_scope_and_sop_permissions(session_factory) -> None:
    order = await order_for(session_factory, second_store=True)
    denied = await KnowledgeTools(session_factory).search_policy(
        PolicySearchInput(
            context=context("operator-store-001"), query="退款", store_id=order.store_id
        )
    )
    assert denied.error.error_code == "FORBIDDEN"
    sop = await KnowledgeTools(session_factory).retrieve_sop(
        SOPInput(context=context("agent-customer-service"), name="退款处理")
    )
    assert sop.ok and sop.data["version"] == "sop_v1"


@pytest.mark.asyncio
async def test_media_contract_success_disabled_and_permission_audits(session_factory) -> None:
    service = MediaTools(session_factory)
    success = await service.probe_media(
        MediaInput(context=context("agent-media", "trace-media-ok"), media_id="media-fixture-001")
    )
    assert success.ok and success.data["simulated"] is True
    disabled = await service.feature_not_enabled(
        "submit_transcription",
        MediaInput(
            context=context("agent-media", "trace-media-disabled"), media_id="media-fixture-001"
        ),
    )
    assert disabled.error.error_code == "FEATURE_NOT_ENABLED"
    forbidden = await service.probe_media(
        MediaInput(
            context=context("agent-operations", "trace-media-forbidden"),
            media_id="media-fixture-001",
        )
    )
    assert forbidden.error.error_code == "FORBIDDEN"
    async with session_factory() as session:
        audits = (
            await session.scalars(
                select(ToolCallAudit).where(
                    ToolCallAudit.trace_id.in_(
                        ["trace-media-ok", "trace-media-disabled", "trace-media-forbidden"]
                    )
                )
            )
        ).all()
    assert len(audits) == 3


@pytest.mark.asyncio
async def test_sensitive_query_is_hashed_in_audit(session_factory) -> None:
    # secret-scan: allow-test-fixture
    secret = "phone-13800000000-token-secret"
    await KnowledgeTools(session_factory).search_policy(
        PolicySearchInput(context=context("admin-001", "trace-sensitive"), query=secret, limit=2)
    )
    async with session_factory() as session:
        audit = await session.scalar(
            select(ToolCallAudit).where(ToolCallAudit.trace_id == "trace-sensitive")
        )
    assert audit is not None
    assert secret not in str(audit.input_summary)
    assert set(audit.input_summary) == {"query", "limit"}


@pytest.mark.asyncio
async def test_every_call_has_required_audit_fields(session_factory) -> None:
    order = await order_for(session_factory)
    trace_id = f"trace-{uuid.uuid4()}"
    await BusinessTools(session_factory).get_order(
        OrderInput(context=context("admin-001", trace_id), order_id=order.id)
    )
    async with session_factory() as session:
        audit = await session.scalar(
            select(ToolCallAudit).where(ToolCallAudit.trace_id == trace_id)
        )
    assert audit is not None
    for value in (
        audit.id,
        audit.tool_name,
        audit.tool_version,
        audit.run_id,
        audit.task_id,
        audit.agent,
        audit.principal_id,
        audit.started_at,
        audit.finished_at,
        audit.status,
        audit.trace_id,
    ):
        assert value is not None
