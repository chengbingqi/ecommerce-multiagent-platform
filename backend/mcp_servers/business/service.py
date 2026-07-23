import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    SKU,
    Approval,
    AuthUser,
    IdempotencyRecord,
    Inventory,
    KnowledgeChunk,
    KnowledgeDocument,
    Logistics,
    Order,
    OrderItem,
    Principal,
    Refund,
    SalesDaily,
    SeedManifest,
)
from app.security.demo import load_demo_manifest
from mcp_servers.business.schemas import (
    InventoryInput,
    OperationsMetricsInput,
    OrderInput,
    RefundRequestInput,
    SalesMetricsInput,
)
from mcp_servers.common.contracts import Evidence, ToolResult
from mcp_servers.common.errors import ToolFailure
from mcp_servers.common.governance import action_digest, execute_tool
from mcp_servers.common.registry import BUSINESS_SPECS


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _db_evidence(table: str, record_id: str) -> Evidence:
    return Evidence(
        evidence_ref=f"db://{table}/{record_id}",
        source_type="database",
        record_id=record_id,
    )


async def _clock(session: AsyncSession) -> datetime:
    value = await session.scalar(
        select(SeedManifest.simulated_clock).order_by(SeedManifest.manifest_version)
    )
    if value is None:
        raise ToolFailure("TRANSIENT_ERROR", "simulated clock is unavailable", retryable=True)
    return _aware(value)


async def _authorized_order(session: AsyncSession, principal: Principal, order_id: str) -> Order:
    order = await session.get(Order, order_id)
    if order is None:
        raise ToolFailure("NOT_FOUND", "order was not found")
    if principal.role == "admin":
        return order
    auth_user = await session.scalar(select(AuthUser).where(AuthUser.principal_id == principal.id))
    if auth_user and auth_user.account_type == "public_demo":
        manifest = load_demo_manifest()
        manifest_case = next((case for case in manifest.orders if case.order_id == order.id), None)
        if (
            manifest_case is not None
            and order.store_id == principal.store_id
            and manifest_case.store_id == principal.store_id
            and order.store_id == manifest.operations_store_id
        ):
            return order
        raise ToolFailure("FORBIDDEN", "order is outside the public demo scope")
    if principal.role == "customer:self":
        if order.customer_ref != principal.customer_ref:
            raise ToolFailure("FORBIDDEN", "order is outside the customer scope")
    elif order.store_id != principal.store_id:
        raise ToolFailure("FORBIDDEN", "order is outside the store scope")
    return order


def _authorize_store(principal: Principal, store_id: str) -> None:
    if principal.role != "admin" and principal.store_id != store_id:
        raise ToolFailure("FORBIDDEN", "store is outside the principal scope")


async def _deny_public_demo_write(session: AsyncSession, principal: Principal) -> None:
    auth_user = await session.scalar(select(AuthUser).where(AuthUser.principal_id == principal.id))
    if auth_user and auth_user.account_type == "public_demo":
        raise ToolFailure("FORBIDDEN", "public demo account is read-only")


class BusinessTools:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def get_order(self, request: OrderInput) -> ToolResult:
        async def handler(
            session: AsyncSession, principal: Principal
        ) -> tuple[dict[str, Any], list[Evidence]]:
            order = await _authorized_order(session, principal, request.order_id)
            items = (
                await session.scalars(
                    select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
                )
            ).all()
            evidence = [_db_evidence("orders", order.id)] + [
                _db_evidence("order_items", item.id) for item in items
            ]
            return (
                {
                    "order_id": order.id,
                    "store_id": order.store_id,
                    "status": order.status,
                    "total_amount": _money(order.total_amount),
                    "currency": order.currency,
                    "ordered_at": _aware(order.ordered_at).isoformat(),
                    "items": [
                        {
                            "sku_id": item.sku_id,
                            "quantity": item.quantity,
                            "unit_price": _money(item.unit_price),
                            "line_total": _money(item.line_total),
                        }
                        for item in items
                    ],
                },
                evidence,
            )

        return await execute_tool(
            self.session_factory,
            BUSINESS_SPECS["get_order"],
            request.context,
            request.model_dump(exclude={"context"}, mode="json"),
            handler,
        )

    async def get_logistics(self, request: OrderInput) -> ToolResult:
        async def handler(
            session: AsyncSession, principal: Principal
        ) -> tuple[dict[str, Any], list[Evidence]]:
            order = await _authorized_order(session, principal, request.order_id)
            logistics = await session.scalar(
                select(Logistics).where(Logistics.order_id == order.id)
            )
            if logistics is None:
                raise ToolFailure("NOT_FOUND", "logistics record was not found")
            return (
                {
                    "order_id": order.id,
                    "carrier": logistics.carrier,
                    "status": logistics.status,
                    "last_event": logistics.last_event,
                    "estimated_delivery_at": _aware(logistics.estimated_delivery_at).isoformat(),
                    "scenario": logistics.scenario,
                },
                [_db_evidence("logistics", logistics.id)],
            )

        return await execute_tool(
            self.session_factory,
            BUSINESS_SPECS["get_logistics"],
            request.context,
            request.model_dump(exclude={"context"}, mode="json"),
            handler,
        )

    async def query_inventory(self, request: InventoryInput) -> ToolResult:
        async def handler(
            session: AsyncSession, principal: Principal
        ) -> tuple[dict[str, Any], list[Evidence]]:
            _authorize_store(principal, request.store_id)
            statement = (
                select(Inventory, SKU)
                .join(SKU, SKU.id == Inventory.sku_id)
                .where(Inventory.store_id == request.store_id)
                .order_by(SKU.code, Inventory.id)
            )
            if request.sku_id:
                statement = statement.where(Inventory.sku_id == request.sku_id)
            if request.low_stock_threshold is not None:
                statement = statement.where(
                    Inventory.on_hand - Inventory.reserved <= request.low_stock_threshold
                )
            rows = (await session.execute(statement)).all()
            evidence = [_db_evidence("inventories", inventory.id) for inventory, _ in rows]
            return (
                {
                    "store_id": request.store_id,
                    "items": [
                        {
                            "inventory_id": inventory.id,
                            "sku_id": inventory.sku_id,
                            "sku_code": sku.code,
                            "on_hand": inventory.on_hand,
                            "reserved": inventory.reserved,
                            "available": inventory.on_hand - inventory.reserved,
                            "scenario": inventory.scenario,
                            "evidence_ref": f"db://inventories/{inventory.id}",
                        }
                        for inventory, sku in rows
                    ],
                },
                evidence,
            )

        return await execute_tool(
            self.session_factory,
            BUSINESS_SPECS["query_inventory"],
            request.context,
            request.model_dump(exclude={"context"}, mode="json"),
            handler,
        )

    async def get_sales_metrics(self, request: SalesMetricsInput) -> ToolResult:
        async def handler(
            session: AsyncSession, principal: Principal
        ) -> tuple[dict[str, Any], list[Evidence]]:
            _authorize_store(principal, request.store_id)
            start_at = _aware(request.start_at)
            end_at = _aware(request.end_at)
            if start_at >= end_at:
                raise ToolFailure("INVALID_ARGUMENT", "start_at must be before end_at")
            gmv, order_count = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(Order.total_amount), 0), func.count(Order.id)
                    ).where(
                        Order.store_id == request.store_id,
                        Order.ordered_at >= start_at,
                        Order.ordered_at < end_at,
                    )
                )
            ).one()
            refund_count = int(
                await session.scalar(
                    select(func.count(func.distinct(Refund.order_id))).where(
                        Refund.store_id == request.store_id,
                        Refund.created_at >= start_at,
                        Refund.created_at < end_at,
                    )
                )
                or 0
            )
            sessions, conversions = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(SalesDaily.sessions), 0),
                        func.coalesce(func.sum(SalesDaily.conversions), 0),
                    ).where(
                        SalesDaily.store_id == request.store_id,
                        SalesDaily.sales_date >= start_at.date(),
                        SalesDaily.sales_date < end_at.date(),
                    )
                )
            ).one()
            gmv_decimal = Decimal(gmv)
            orders = int(order_count)
            aov = gmv_decimal / orders if orders else Decimal("0")
            refund_rate = Decimal(refund_count) / orders if orders else Decimal("0")
            conversion_rate = Decimal(conversions) / int(sessions) if sessions else Decimal("0")
            return (
                {
                    "store_id": request.store_id,
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                    "gmv": _money(gmv_decimal),
                    "order_count": orders,
                    "average_order_value": _money(aov),
                    "refund_rate": str(refund_rate.quantize(Decimal("0.000001"))),
                    "sessions": int(sessions),
                    "conversions": int(conversions),
                    "conversion_rate": str(conversion_rate.quantize(Decimal("0.000001"))),
                },
                [
                    Evidence(
                        evidence_ref=f"db-query://sales/{request.store_id}",
                        source_type="database",
                        record_id=request.store_id,
                    )
                ],
            )

        return await execute_tool(
            self.session_factory,
            BUSINESS_SPECS["get_sales_metrics"],
            request.context,
            request.model_dump(exclude={"context"}, mode="json"),
            handler,
        )

    async def get_operations_metrics(self, request: OperationsMetricsInput) -> ToolResult:
        from mcp_servers.business.operations import collect_operations_metrics

        async def handler(
            session: AsyncSession, principal: Principal
        ) -> tuple[dict[str, Any], list[Evidence]]:
            _authorize_store(principal, request.store_id)
            return await collect_operations_metrics(session, request)

        return await execute_tool(
            self.session_factory,
            BUSINESS_SPECS["get_operations_metrics"],
            request.context,
            request.model_dump(exclude={"context"}, mode="json"),
            handler,
        )

    async def check_refund_eligibility(self, request: OrderInput) -> ToolResult:
        async def handler(
            session: AsyncSession, principal: Principal
        ) -> tuple[dict[str, Any], list[Evidence]]:
            order = await _authorized_order(session, principal, request.order_id)
            clock = await _clock(session)
            age = clock - _aware(order.ordered_at)
            eligible = order.status in {"delivered", "shipped"} and age <= timedelta(days=7)
            document = await session.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.version == "policy_v1",
                    KnowledgeDocument.effective_at <= clock,
                    (
                        KnowledgeDocument.expires_at.is_(None)
                        | (KnowledgeDocument.expires_at > clock)
                    ),
                )
            )
            if document is None:
                raise ToolFailure("TRANSIENT_ERROR", "refund policy is unavailable")
            chunk = await session.scalar(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document.id)
                .order_by(KnowledgeChunk.ordinal)
            )
            assert chunk is not None
            evidence = Evidence(
                evidence_ref=f"doc://{document.id}/{document.version}/{chunk.id}",
                source_type="policy",
                record_id=chunk.id,
                version=document.version,
            )
            return (
                {
                    "order_id": order.id,
                    "eligible": eligible,
                    "reason_codes": ["WITHIN_7_DAYS"] if eligible else ["OUTSIDE_POLICY_WINDOW"],
                    "rules_version": "refund_rules_v1",
                    "policy_evidence": evidence.model_dump(),
                },
                [evidence],
            )

        return await execute_tool(
            self.session_factory,
            BUSINESS_SPECS["check_refund_eligibility"],
            request.context,
            request.model_dump(exclude={"context"}, mode="json"),
            handler,
        )

    async def create_refund_request(self, request: RefundRequestInput) -> ToolResult:
        payload = request.model_dump(
            exclude={"context", "approval_id", "idempotency_key"}, mode="json"
        )
        digest = action_digest(payload)

        async def handler(
            session: AsyncSession, principal: Principal
        ) -> tuple[dict[str, Any], list[Evidence]]:
            await _deny_public_demo_write(session, principal)
            order = await _authorized_order(session, principal, request.order_id)
            existing = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.tool_name == "create_refund_request",
                    IdempotencyRecord.idempotency_key == request.idempotency_key,
                )
            )
            if existing:
                if existing.action_digest != digest:
                    raise ToolFailure(
                        "IDEMPOTENCY_CONFLICT",
                        "idempotency key was already used for a different action",
                    )
                refund_id = str(existing.result_payload["refund_request_id"])
                return existing.result_payload, [_db_evidence("refunds", refund_id)]
            if not request.approval_id:
                raise ToolFailure("APPROVAL_REQUIRED", "an approved action is required")
            approval = await session.get(Approval, request.approval_id)
            now = datetime.now(UTC)
            if (
                approval is None
                or approval.principal_id != principal.id
                or approval.tool_name != "create_refund_request"
                or approval.action_digest != digest
                or approval.status != "approved"
                or _aware(approval.expires_at) <= now
            ):
                raise ToolFailure("APPROVAL_REQUIRED", "approval is missing, invalid, or expired")
            if request.amount > order.total_amount:
                raise ToolFailure("INVALID_ARGUMENT", "refund amount exceeds order total")
            refund_id = str(uuid.uuid4())
            clock = await _clock(session)
            session.add(
                Refund(
                    id=refund_id,
                    store_id=order.store_id,
                    order_id=order.id,
                    status="simulated_requested",
                    amount=request.amount,
                    reason_code=request.reason_code,
                    scenario=None,
                    created_at=clock,
                )
            )
            result: dict[str, Any] = {
                "refund_request_id": refund_id,
                "order_id": order.id,
                "status": "simulated_requested",
                "amount": _money(request.amount),
            }
            session.add(
                IdempotencyRecord(
                    id=str(uuid.uuid4()),
                    tool_name="create_refund_request",
                    idempotency_key=request.idempotency_key,
                    action_digest=digest,
                    result_payload=result,
                    created_at=now,
                )
            )
            return result, [_db_evidence("refunds", refund_id)]

        return await execute_tool(
            self.session_factory,
            BUSINESS_SPECS["create_refund_request"],
            request.context,
            payload,
            handler,
            approval_id=request.approval_id,
            idempotency_key=request.idempotency_key,
        )
