from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from mcp_servers.common.contracts import CallContext


class OrderInput(BaseModel):
    context: CallContext
    order_id: str = Field(min_length=1, max_length=64)


class InventoryInput(BaseModel):
    context: CallContext
    store_id: str = Field(min_length=1, max_length=64)
    sku_id: str | None = Field(default=None, max_length=64)
    low_stock_threshold: int | None = Field(default=None, ge=0, le=100000)


class SalesMetricsInput(BaseModel):
    context: CallContext
    store_id: str = Field(min_length=1, max_length=64)
    start_at: datetime
    end_at: datetime


class OperationsMetricsInput(BaseModel):
    context: CallContext
    store_id: str = Field(min_length=1, max_length=64)
    start_at: datetime
    end_at: datetime
    granularity: Literal["day", "week"] = "day"
    comparison_mode: Literal["previous_period", "none"] = "none"


class RefundRequestInput(BaseModel):
    context: CallContext
    order_id: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    reason_code: str = Field(min_length=1, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=128)
    approval_id: str | None = Field(default=None, max_length=64)
