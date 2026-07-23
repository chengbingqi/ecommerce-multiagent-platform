from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from app.core.config import get_settings

if TYPE_CHECKING:
    from app.security.service import SecurityContext

DEMO_ACCOUNT_TYPE = "public_demo"
DEMO_RESTRICTED_MESSAGE = "公共演示账号仅支持只读查询，不允许执行退款申请或审批。"
DEMO_MEDIA_RESTRICTED_MESSAGE = "公共演示账号不允许上传视频、创建媒体任务或审核直播切片。"
DEMO_ADMIN_RESTRICTED_MESSAGE = "公共演示账号不允许访问管理员功能或运行评测。"


@dataclass(frozen=True)
class DemoOrderCase:
    demo_case_id: str
    order_id: str
    store_id: str
    safe_label: str
    scenario_description: str
    expected_read_only_capability: tuple[str, ...]


@dataclass(frozen=True)
class DemoManifest:
    manifest_version: str
    source_manifest_version: str
    allowed_store_ids: tuple[str, ...]
    operations_store_id: str
    orders: tuple[DemoOrderCase, ...]

    @property
    def allowed_order_ids(self) -> set[str]:
        return {order.order_id for order in self.orders}


def is_public_demo(context: SecurityContext | None) -> bool:
    return bool(context and context.account_type == DEMO_ACCOUNT_TYPE)


def forbid_public_demo(
    context: SecurityContext | None, message: str = DEMO_RESTRICTED_MESSAGE
) -> None:
    if is_public_demo(context):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "PUBLIC_DEMO_READ_ONLY", "message": message},
        )


def require_demo_order(context: SecurityContext | None, order_id: str | None) -> None:
    if not is_public_demo(context):
        return
    if not order_id or order_id not in load_demo_manifest().allowed_order_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "PUBLIC_DEMO_ORDER_FORBIDDEN",
                "message": "公共演示账号只能访问预设的固定演示订单。",
            },
        )


def require_demo_store(context: SecurityContext | None, store_id: str | None) -> None:
    if not is_public_demo(context):
        return
    manifest = load_demo_manifest()
    if store_id and store_id != manifest.operations_store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "PUBLIC_DEMO_STORE_FORBIDDEN",
                "message": "公共演示账号只能分析授权的模拟店铺经营数据。",
            },
        )


def reject_demo_write_text(context: SecurityContext | None, text: str) -> None:
    if not is_public_demo(context):
        return
    lowered = text.lower()
    negations = ("不要", "不发起", "不申请", "不要发起", "不要申请", "仅判断", "只查询", "只读")
    refund_read_only = "退款" in lowered and any(marker in lowered for marker in negations)
    markers = (
        "申请退款",
        "发起退款",
        "创建退款",
        "批准退款",
        "审批",
        "上传视频",
        "创建用户",
        "禁用用户",
        "修改角色",
        "评测",
        "全量评测",
        "运行评测",
        "直播切片",
        "视频上传",
        "发布",
    )
    write_markers = [marker for marker in markers if not (refund_read_only and "退款" in marker)]
    if any(marker in lowered for marker in write_markers):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "PUBLIC_DEMO_READ_ONLY", "message": DEMO_RESTRICTED_MESSAGE},
        )


@lru_cache
def load_demo_manifest() -> DemoManifest:
    path = Path(get_settings().public_demo_orders_manifest)
    payload = json.loads(path.read_text(encoding="utf-8"))
    orders = tuple(
        DemoOrderCase(
            demo_case_id=str(row["demo_case_id"]),
            order_id=str(row["order_id"]),
            store_id=str(row["store_id"]),
            safe_label=str(row["safe_label"]),
            scenario_description=str(row["scenario_description"]),
            expected_read_only_capability=tuple(row["expected_read_only_capability"]),
        )
        for row in payload["orders"]
    )
    return DemoManifest(
        manifest_version=str(payload["manifest_version"]),
        source_manifest_version=str(payload["source_manifest_version"]),
        allowed_store_ids=tuple(payload["allowed_store_ids"]),
        operations_store_id=str(payload["operations_store_id"]),
        orders=orders,
    )


def public_demo_claims() -> dict[str, Any]:
    manifest = load_demo_manifest()
    return {
        "account_type": DEMO_ACCOUNT_TYPE,
        "operations_store_id": manifest.operations_store_id,
        "allowed_order_count": len(manifest.orders),
        "policy_version": get_settings().public_demo_policy_version,
    }
