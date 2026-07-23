from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SKU, Campaign, Inventory, Logistics, Order, Refund, SalesDaily
from mcp_servers.business.schemas import OperationsMetricsInput
from mcp_servers.common.contracts import Evidence
from mcp_servers.common.errors import ToolFailure

METRICS_VERSION = "operations_metrics_v1.0.0"
GMV_STATUSES = frozenset({"shipped", "delivered"})
LOW_STOCK_THRESHOLD = 10
MONEY_QUANTUM = Decimal("0.01")
RATIO_QUANTUM = Decimal("0.000001")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _money(value: Decimal) -> str:
    return str(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def _ratio(numerator: Decimal | int, denominator: Decimal | int) -> str:
    if not denominator:
        return "0.000000"
    value = Decimal(numerator) / Decimal(denominator)
    return str(value.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_UP))


def _evidence(table: str, record_id: str) -> Evidence:
    return Evidence(
        evidence_ref=f"db://{table}/{record_id}",
        source_type="database",
        record_id=record_id,
        version=METRICS_VERSION,
    )


def _bucket(value: date, granularity: str) -> date:
    return value if granularity == "day" else value - timedelta(days=value.weekday())


def _metric(
    key: str,
    display_name: str,
    value: int | str,
    unit: str,
    period: dict[str, str],
    refs: list[str],
) -> dict[str, Any]:
    return {
        "metric_key": key,
        "display_name": display_name,
        "value": value,
        "unit": unit,
        "period": period,
        "comparison_value": None,
        "change": None,
        "evidence_refs": refs,
    }


async def _window(
    session: AsyncSession,
    *,
    store_id: str,
    start_at: datetime,
    end_at: datetime,
    granularity: str,
    include_rows: bool,
) -> tuple[dict[str, Any], list[Evidence]]:
    orders = (
        await session.scalars(
            select(Order)
            .where(
                Order.store_id == store_id,
                Order.ordered_at >= start_at,
                Order.ordered_at < end_at,
                Order.status.in_(GMV_STATUSES),
            )
            .order_by(Order.ordered_at, Order.id)
        )
    ).all()
    refunds = (
        await session.scalars(
            select(Refund)
            .where(
                Refund.store_id == store_id,
                Refund.created_at >= start_at,
                Refund.created_at < end_at,
            )
            .order_by(Refund.created_at, Refund.id)
        )
    ).all()
    sales = (
        await session.scalars(
            select(SalesDaily)
            .where(
                SalesDaily.store_id == store_id,
                SalesDaily.sales_date >= start_at.date(),
                SalesDaily.sales_date < end_at.date(),
            )
            .order_by(SalesDaily.sales_date, SalesDaily.id)
        )
    ).all()
    logistics_rows = (
        await session.execute(
            select(Logistics, Order)
            .join(Order, Order.id == Logistics.order_id)
            .where(
                Order.store_id == store_id,
                Order.ordered_at >= start_at,
                Order.ordered_at < end_at,
            )
            .order_by(Order.ordered_at, Logistics.id)
        )
    ).all()
    campaigns = (
        await session.scalars(
            select(Campaign)
            .where(
                Campaign.store_id == store_id,
                Campaign.starts_at < end_at,
                Campaign.ends_at >= start_at,
            )
            .order_by(Campaign.starts_at, Campaign.id)
        )
    ).all()
    inventory_rows = (
        await session.execute(
            select(Inventory, SKU)
            .join(SKU, SKU.id == Inventory.sku_id)
            .where(Inventory.store_id == store_id)
            .order_by(SKU.code, Inventory.id)
        )
    ).all()
    refs = {
        "orders": [f"db://orders/{row.id}" for row in orders],
        "refunds": [f"db://refunds/{row.id}" for row in refunds],
        "sales": [f"db://sales_daily/{row.id}" for row in sales],
        "logistics": [f"db://logistics/{row.id}" for row, _ in logistics_rows],
        "campaigns": [f"db://campaigns/{row.id}" for row in campaigns],
    }
    evidence = [
        *(_evidence("orders", row.id) for row in orders),
        *(_evidence("refunds", row.id) for row in refunds),
        *(_evidence("sales_daily", row.id) for row in sales),
        *(_evidence("logistics", row.id) for row, _ in logistics_rows),
        *(_evidence("campaigns", row.id) for row in campaigns),
        *(_evidence("inventories", row.id) for row, _ in inventory_rows),
    ]
    gmv = sum((row.total_amount for row in orders), Decimal("0"))
    order_count = len(orders)
    refund_amount = sum((row.amount for row in refunds), Decimal("0"))
    refund_orders = len({row.order_id for row in refunds})
    sessions = sum(row.sessions for row in sales)
    conversions = sum(row.conversions for row in sales)
    available = [(row, row.on_hand - row.reserved) for row, _ in inventory_rows]
    stockouts = [(row, count) for row, count in available if count <= 0]
    low_stock = [(row, count) for row, count in available if 0 < count <= LOW_STOCK_THRESHOLD]
    delayed = [(row, order) for row, order in logistics_rows if row.status == "delayed"]
    period = {
        "start": start_at.isoformat(),
        "end": end_at.isoformat(),
        "semantics": "[start,end)",
    }
    metrics = [
        _metric("order_count", "订单数", order_count, "count", period, refs["orders"]),
        _metric("gmv", "成交金额", _money(gmv), "CNY", period, refs["orders"]),
        _metric(
            "average_order_value",
            "客单价",
            _money(gmv / order_count if order_count else Decimal("0")),
            "CNY/order",
            period,
            refs["orders"],
        ),
        _metric("refund_count", "退款订单数", refund_orders, "count", period, refs["refunds"]),
        _metric("refund_amount", "退款金额", _money(refund_amount), "CNY", period, refs["refunds"]),
        _metric(
            "refund_rate",
            "退款率",
            _ratio(refund_orders, order_count),
            "ratio",
            period,
            [*refs["refunds"], *refs["orders"]],
        ),
        _metric("visitor_count", "访客会话数", sessions, "count", period, refs["sales"]),
        _metric(
            "conversion_rate",
            "转化率",
            _ratio(conversions, sessions),
            "ratio",
            period,
            refs["sales"],
        ),
        _metric(
            "stockout_sku_count",
            "缺货SKU数",
            len(stockouts),
            "count",
            period,
            [f"db://inventories/{row.id}" for row, _ in stockouts],
        ),
        _metric(
            "low_stock_sku_count",
            "低库存SKU数",
            len(low_stock),
            "count",
            period,
            [f"db://inventories/{row.id}" for row, _ in low_stock],
        ),
        _metric(
            "delayed_logistics_count",
            "延迟物流数",
            len(delayed),
            "count",
            period,
            [f"db://logistics/{row.id}" for row, _ in delayed],
        ),
        _metric(
            "delayed_logistics_rate",
            "物流延迟率",
            _ratio(len(delayed), len(logistics_rows)),
            "ratio",
            period,
            refs["logistics"],
        ),
        _metric("campaign_count", "活动数", len(campaigns), "count", period, refs["campaigns"]),
        _metric(
            "live_campaign_count",
            "直播活动数",
            sum(row.campaign_type == "live" for row in campaigns),
            "count",
            period,
            refs["campaigns"],
        ),
    ]
    buckets: dict[date, dict[str, Any]] = defaultdict(
        lambda: {"gmv": Decimal("0"), "orders": 0, "sessions": 0, "conversions": 0, "refs": []}
    )
    for row in sales:
        bucket = buckets[_bucket(row.sales_date, granularity)]
        bucket["gmv"] += row.gmv
        bucket["orders"] += row.order_count
        bucket["sessions"] += row.sessions
        bucket["conversions"] += row.conversions
        bucket["refs"].append(f"db://sales_daily/{row.id}")
    trend = [
        {
            "bucket_start": key.isoformat(),
            "granularity": granularity,
            "order_count": row["orders"],
            "gmv": _money(row["gmv"]),
            "visitor_count": row["sessions"],
            "conversion_rate": _ratio(row["conversions"], row["sessions"]),
            "evidence_refs": row["refs"],
        }
        for key, row in sorted(buckets.items())
    ]
    scenario_counts = {
        "stockout_spike": sum(row.scenario == "stockout_spike" for row, _ in stockouts),
        "refund_spike": sum(row.scenario == "refund_spike" for row in refunds),
        "logistics_delay": sum(row.scenario == "logistics_delay" for row, _ in delayed),
        "conversion_drop": sum(row.scenario == "conversion_drop" for row in sales),
        "live_campaign": sum(row.scenario == "live_campaign" for row in campaigns),
    }
    evidence_rows: list[dict[str, Any]] = []
    if include_rows:
        for record_type, rows in (
            ("order", [(row, _money(row.total_amount)) for row in orders]),
            ("inventory", [(row, count) for row, count in available]),
            ("refund", [(row, _money(row.amount)) for row in refunds]),
            ("logistics", [(row, row.status) for row, _ in logistics_rows]),
            ("sales_daily", [(row, _ratio(row.conversions, row.sessions)) for row in sales]),
            ("campaign", [(row, row.campaign_type) for row in campaigns]),
        ):
            table = {
                "order": "orders",
                "inventory": "inventories",
                "refund": "refunds",
                "logistics": "logistics",
                "sales_daily": "sales_daily",
                "campaign": "campaigns",
            }[record_type]
            evidence_rows.extend(
                {
                    "evidence_ref": f"db://{table}/{row.id}",
                    "record_type": record_type,
                    "record_id": row.id,
                    "store_id": store_id,
                    "observed": observed,
                    "scenario": getattr(row, "scenario", None),
                }
                for row, observed in rows
            )
    return {
        "metrics": metrics,
        "trend_series": trend,
        "scenario_counts": scenario_counts,
        "evidence_rows": evidence_rows,
    }, evidence


async def collect_operations_metrics(
    session: AsyncSession, request: OperationsMetricsInput
) -> tuple[dict[str, Any], list[Evidence]]:
    start_at = _aware(request.start_at)
    end_at = _aware(request.end_at)
    if start_at >= end_at:
        raise ToolFailure("INVALID_ARGUMENT", "start_at must be before end_at")
    if end_at - start_at > timedelta(days=90):
        raise ToolFailure("INVALID_ARGUMENT", "time range exceeds 90 days")
    current, evidence = await _window(
        session,
        store_id=request.store_id,
        start_at=start_at,
        end_at=end_at,
        granularity=request.granularity,
        include_rows=True,
    )
    comparison_period: dict[str, str] | None = None
    if request.comparison_mode == "previous_period":
        width = end_at - start_at
        previous_start = start_at - width
        previous, previous_evidence = await _window(
            session,
            store_id=request.store_id,
            start_at=previous_start,
            end_at=start_at,
            granularity=request.granularity,
            include_rows=False,
        )
        previous_by_key = {row["metric_key"]: row for row in previous["metrics"]}
        for metric in current["metrics"]:
            old = previous_by_key[metric["metric_key"]]["value"]
            metric["comparison_value"] = old
            previous_value = Decimal(str(old))
            metric["change"] = (
                _ratio(Decimal(str(metric["value"])) - previous_value, previous_value)
                if previous_value
                else None
            )
        evidence.extend(previous_evidence)
        comparison_period = {
            "start": previous_start.isoformat(),
            "end": start_at.isoformat(),
            "semantics": "[start,end)",
        }
    unique = {item.evidence_ref: item for item in evidence}
    return (
        {
            "store_id": request.store_id,
            "period": {
                "start": start_at.isoformat(),
                "end": end_at.isoformat(),
                "semantics": "[start,end)",
            },
            "comparison_period": comparison_period,
            "granularity": request.granularity,
            "metrics_version": METRICS_VERSION,
            **current,
        },
        list(unique.values()),
    )
