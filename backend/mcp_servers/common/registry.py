from typing import Literal

from pydantic import BaseModel

from mcp_servers.business.schemas import (
    InventoryInput,
    OperationsMetricsInput,
    OrderInput,
    RefundRequestInput,
    SalesMetricsInput,
)
from mcp_servers.common.contracts import ToolResult, ToolSpec
from mcp_servers.common.errors import ERROR_CODES
from mcp_servers.knowledge.schemas import ChunkInput, PolicySearchInput, SOPInput
from mcp_servers.media.schemas import (
    MediaInput,
    MediaSideEffectV11Input,
    MediaTaskV11Input,
    ProbeMediaV11Input,
)

COMMON_ERRORS = tuple(ERROR_CODES)


def _spec(
    name: str,
    input_model: type[BaseModel],
    permissions: tuple[str, ...],
    *,
    side_effect: bool = False,
    timeout: int = 5,
    audit_fields: tuple[str, ...] = (),
    version: str = "1.0.0",
    retry_policy: Literal["none", "safe_once"] = "none",
) -> ToolSpec:
    return ToolSpec(
        tool_name=name,
        tool_version=version,
        input_schema=input_model.model_json_schema(),
        output_schema=ToolResult.model_json_schema(),
        permissions=permissions,
        side_effect=side_effect,
        timeout_seconds=timeout,
        retry_policy=retry_policy,
        error_codes=COMMON_ERRORS,
        audit_fields=audit_fields,
    )


BUSINESS_SPECS = {
    "get_order": _spec(
        "get_order",
        OrderInput,
        ("customer:self", "store_operator", "agent:customer_service", "agent:operations", "admin"),
        audit_fields=("order_id",),
    ),
    "get_logistics": _spec(
        "get_logistics",
        OrderInput,
        ("customer:self", "store_operator", "agent:customer_service", "agent:operations", "admin"),
        audit_fields=("order_id",),
    ),
    "query_inventory": _spec(
        "query_inventory",
        InventoryInput,
        ("store_operator", "agent:operations", "admin"),
        audit_fields=("store_id", "sku_id", "low_stock_threshold"),
    ),
    "get_sales_metrics": _spec(
        "get_sales_metrics",
        SalesMetricsInput,
        ("store_operator", "agent:operations", "admin"),
        audit_fields=("store_id", "start_at", "end_at"),
    ),
    "get_operations_metrics": _spec(
        "get_operations_metrics",
        OperationsMetricsInput,
        ("store_operator", "agent:operations", "admin"),
        timeout=8,
        audit_fields=("store_id", "start_at", "end_at", "granularity", "comparison_mode"),
        version="1.1.0",
        retry_policy="safe_once",
    ),
    "check_refund_eligibility": _spec(
        "check_refund_eligibility",
        OrderInput,
        ("customer:self", "store_operator", "agent:customer_service", "agent:operations", "admin"),
        audit_fields=("order_id",),
    ),
    "create_refund_request": _spec(
        "create_refund_request",
        RefundRequestInput,
        ("customer:self", "store_operator", "agent:customer_service", "admin"),
        side_effect=True,
        timeout=8,
        audit_fields=("order_id", "amount", "reason_code"),
    ),
}

KNOWLEDGE_SPECS = {
    "search_policy": _spec(
        "search_policy",
        PolicySearchInput,
        ("customer:self", "store_operator", "agent:customer_service", "agent:operations", "admin"),
        audit_fields=("query", "store_id", "limit"),
    ),
    "retrieve_sop": _spec(
        "retrieve_sop",
        SOPInput,
        ("store_operator", "agent:customer_service", "agent:operations", "admin"),
        audit_fields=("name", "store_id"),
    ),
    "get_document_chunk": _spec(
        "get_document_chunk",
        ChunkInput,
        ("customer:self", "store_operator", "agent:customer_service", "agent:operations", "admin"),
        audit_fields=("chunk_id",),
    ),
}

MEDIA_SPECS = {
    "probe_media": _spec(
        "probe_media", MediaInput, ("agent:media", "admin"), audit_fields=("media_id",)
    ),
    **{
        name: _spec(
            name,
            MediaInput,
            ("agent:media", "admin"),
            side_effect=name in {"render_clip", "save_asset"},
            audit_fields=("media_id",),
        )
        for name in (
            "submit_transcription",
            "detect_scenes",
            "score_highlights",
            "render_clip",
            "save_asset",
        )
    },
}

MEDIA_V11_SPECS = {
    "probe_media": _spec(
        "probe_media",
        ProbeMediaV11Input,
        ("content_operator", "agent:media", "admin"),
        audit_fields=("asset_id",),
        version="1.1.0",
    ),
    "submit_transcription": _spec(
        "submit_transcription",
        MediaTaskV11Input,
        ("content_operator", "agent:media", "admin"),
        audit_fields=("asset_id", "media_task_id"),
        version="1.1.0",
        retry_policy="safe_once",
    ),
    "detect_scenes": _spec(
        "detect_scenes",
        MediaTaskV11Input,
        ("content_operator", "agent:media", "admin"),
        audit_fields=("asset_id", "media_task_id"),
        version="1.1.0",
    ),
    "score_highlights": _spec(
        "score_highlights",
        MediaTaskV11Input,
        ("content_operator", "agent:media", "admin"),
        audit_fields=("asset_id", "media_task_id"),
        version="1.1.0",
    ),
    "render_clip": _spec(
        "render_clip",
        MediaSideEffectV11Input,
        ("content_operator", "agent:media", "admin"),
        side_effect=True,
        timeout=8,
        audit_fields=("asset_id", "media_task_id"),
        version="1.1.0",
    ),
    "save_asset": _spec(
        "save_asset",
        MediaSideEffectV11Input,
        ("content_operator", "agent:media", "admin"),
        side_effect=True,
        timeout=8,
        audit_fields=("asset_id", "media_task_id"),
        version="1.1.0",
    ),
}

ALL_SPECS = {**BUSINESS_SPECS, **KNOWLEDGE_SPECS, **MEDIA_SPECS}
