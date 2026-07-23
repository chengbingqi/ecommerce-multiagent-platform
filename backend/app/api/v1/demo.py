from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Logistics, Order, Principal
from app.db.session import get_sessionmaker
from app.security.demo import DEMO_ACCOUNT_TYPE, is_public_demo, load_demo_manifest
from app.security.dependencies import SecurityContextDependency

router = APIRouter(prefix="/demo", tags=["public-demo"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoOrderOption(StrictModel):
    demo_case_id: str
    order_id: str
    masked_order_id: str
    safe_label: str
    order_status: str
    logistics_status: str
    demo_scenario: str
    description: str
    scenario_description: str
    store_id: str
    ordered_at: datetime
    allowed_actions: list[str]


class DemoOrdersResponse(StrictModel):
    manifest_version: str
    account_type: Literal["normal", "public_demo"]
    operations_store_id: str
    orders: list[DemoOrderOption]


def _mask_order_id(order_id: str) -> str:
    compact = order_id.replace("-", "")
    return f"****{compact[-4:]}"


def _allowed_roles(context_role: str) -> bool:
    return context_role in {"store_operator", "admin"}


def _validate_manifest_for_public_demo(context_store_id: str | None) -> None:
    manifest = load_demo_manifest()
    if context_store_id is None or context_store_id not in manifest.allowed_store_ids:
        raise HTTPException(status_code=403, detail={"error_code": "FORBIDDEN"})
    bad_cases = [
        case.demo_case_id
        for case in manifest.orders
        if case.store_id != context_store_id or case.store_id != manifest.operations_store_id
    ]
    if bad_cases:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "PUBLIC_DEMO_MANIFEST_INVALID",
                "message": "公共演示订单配置与授权店铺不一致。",
            },
        )


async def _principal(session: AsyncSession, principal_id: str) -> Principal:
    principal = await session.get(Principal, principal_id)
    if principal is None or not principal.active:
        raise HTTPException(status_code=401, detail={"error_code": "AUTH_REQUIRED"})
    return principal


@router.get("/orders", response_model=DemoOrdersResponse)
async def demo_orders(context: SecurityContextDependency) -> DemoOrdersResponse:
    if context is None:
        raise HTTPException(status_code=401, detail={"error_code": "AUTH_REQUIRED"})
    if not is_public_demo(context) and not _allowed_roles(context.role):
        raise HTTPException(status_code=403, detail={"error_code": "FORBIDDEN"})
    if is_public_demo(context):
        _validate_manifest_for_public_demo(context.store_id)

    manifest = load_demo_manifest()
    cases_by_id = {case.order_id: case for case in manifest.orders}
    async with get_sessionmaker()() as session:
        principal = await _principal(session, context.principal_id)
        statement = (
            select(Order, Logistics)
            .join(Logistics, Logistics.order_id == Order.id)
            .where(Order.id.in_(sorted(cases_by_id)))
            .order_by(Order.id)
        )
        rows = (await session.execute(statement)).all()

    visible: list[DemoOrderOption] = []
    for order, logistics in rows:
        case = cases_by_id[order.id]
        if is_public_demo(context):
            if order.id not in cases_by_id or order.store_id != context.store_id:
                continue
        elif principal.role != "admin" and principal.store_id != order.store_id:
            continue
        visible.append(
            DemoOrderOption(
                demo_case_id=case.demo_case_id,
                order_id=order.id,
                masked_order_id=_mask_order_id(order.id),
                safe_label=case.safe_label,
                order_status=order.status,
                logistics_status=logistics.status,
                demo_scenario=case.demo_case_id,
                description=case.scenario_description,
                scenario_description=case.scenario_description,
                store_id=order.store_id,
                ordered_at=order.ordered_at,
                allowed_actions=list(case.expected_read_only_capability),
            )
        )
    visible.sort(key=lambda item: [case.order_id for case in manifest.orders].index(item.order_id))
    return DemoOrdersResponse(
        manifest_version=manifest.manifest_version,
        account_type=DEMO_ACCOUNT_TYPE if is_public_demo(context) else "normal",
        operations_store_id=manifest.operations_store_id,
        orders=visible,
    )
