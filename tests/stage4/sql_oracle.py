from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Campaign, Inventory, Logistics, Order, Refund, SalesDaily


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def ratio(numerator: int, denominator: int) -> str:
    if not denominator:
        return "0.000000"
    return str(
        (Decimal(numerator) / Decimal(denominator)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
    )


async def metric_oracle(
    session: AsyncSession, store_id: str, start_at: datetime, end_at: datetime
) -> dict[str, int | str]:
    gmv, order_count = (
        await session.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0), func.count(Order.id)).where(
                Order.store_id == store_id,
                Order.ordered_at >= start_at,
                Order.ordered_at < end_at,
                Order.status.in_(("shipped", "delivered")),
            )
        )
    ).one()
    refund_count, refund_amount = (
        await session.execute(
            select(
                func.count(func.distinct(Refund.order_id)),
                func.coalesce(func.sum(Refund.amount), 0),
            ).where(
                Refund.store_id == store_id,
                Refund.created_at >= start_at,
                Refund.created_at < end_at,
            )
        )
    ).one()
    sessions, conversions = (
        await session.execute(
            select(
                func.coalesce(func.sum(SalesDaily.sessions), 0),
                func.coalesce(func.sum(SalesDaily.conversions), 0),
            ).where(
                SalesDaily.store_id == store_id,
                SalesDaily.sales_date >= start_at.date(),
                SalesDaily.sales_date < end_at.date(),
            )
        )
    ).one()
    available = Inventory.on_hand - Inventory.reserved
    stockout = int(
        await session.scalar(
            select(func.count())
            .select_from(Inventory)
            .where(Inventory.store_id == store_id, available <= 0)
        )
        or 0
    )
    low_stock = int(
        await session.scalar(
            select(func.count())
            .select_from(Inventory)
            .where(Inventory.store_id == store_id, available > 0, available <= 10)
        )
        or 0
    )
    logistics_total, delayed = (
        await session.execute(
            select(
                func.count(Logistics.id),
                func.sum(func.cast(Logistics.status == "delayed", Integer)),
            )
            .join(Order, Order.id == Logistics.order_id)
            .where(
                Order.store_id == store_id,
                Order.ordered_at >= start_at,
                Order.ordered_at < end_at,
            )
        )
    ).one()
    campaign_count, live_count = (
        await session.execute(
            select(
                func.count(Campaign.id),
                func.sum(func.cast(Campaign.campaign_type == "live", Integer)),
            ).where(
                Campaign.store_id == store_id,
                Campaign.starts_at < end_at,
                Campaign.ends_at >= start_at,
            )
        )
    ).one()
    orders = int(order_count)
    refunds = int(refund_count)
    logistics = int(logistics_total)
    return {
        "order_count": orders,
        "gmv": money(Decimal(gmv)),
        "average_order_value": money(Decimal(gmv) / orders if orders else Decimal("0")),
        "refund_count": refunds,
        "refund_amount": money(Decimal(refund_amount)),
        "refund_rate": ratio(refunds, orders),
        "visitor_count": int(sessions),
        "conversion_rate": ratio(int(conversions), int(sessions)),
        "stockout_sku_count": stockout,
        "low_stock_sku_count": low_stock,
        "delayed_logistics_count": int(delayed or 0),
        "delayed_logistics_rate": ratio(int(delayed or 0), logistics),
        "campaign_count": int(campaign_count),
        "live_campaign_count": int(live_count or 0),
    }
