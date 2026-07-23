import os
from typing import Any

from app.db.session import get_sessionmaker
from mcp_servers.business.schemas import (
    InventoryInput,
    OperationsMetricsInput,
    OrderInput,
    RefundRequestInput,
    SalesMetricsInput,
)
from mcp_servers.business.service import BusinessTools
from mcp_servers.common.server import create_mcp

service = BusinessTools(get_sessionmaker())
mcp = create_mcp("business-mcp", int(os.getenv("MCP_PORT", "8101")))


@mcp.tool(structured_output=True)
async def get_order(request: OrderInput) -> dict[str, Any]:
    """Return a scoped deterministic order summary with database evidence."""
    return (await service.get_order(request)).model_dump(mode="json")


@mcp.tool(structured_output=True)
async def get_logistics(request: OrderInput) -> dict[str, Any]:
    """Return simulated logistics state for a scoped order."""
    return (await service.get_logistics(request)).model_dump(mode="json")


@mcp.tool(structured_output=True)
async def query_inventory(request: InventoryInput) -> dict[str, Any]:
    """Query deterministic scoped inventory and low-stock records."""
    return (await service.query_inventory(request)).model_dump(mode="json")


@mcp.tool(structured_output=True)
async def get_sales_metrics(request: SalesMetricsInput) -> dict[str, Any]:
    """Calculate deterministic sales metrics from database rows."""
    return (await service.get_sales_metrics(request)).model_dump(mode="json")


@mcp.tool(structured_output=True)
async def get_operations_metrics(request: OperationsMetricsInput) -> dict[str, Any]:
    """Return versioned read-only operations metrics, trends, and row evidence."""
    return (await service.get_operations_metrics(request)).model_dump(mode="json")


@mcp.tool(structured_output=True)
async def check_refund_eligibility(request: OrderInput) -> dict[str, Any]:
    """Evaluate versioned deterministic refund rules and policy evidence."""
    return (await service.check_refund_eligibility(request)).model_dump(mode="json")


@mcp.tool(structured_output=True)
async def create_refund_request(request: RefundRequestInput) -> dict[str, Any]:
    """Create only a simulated approved and idempotent refund request."""
    return (await service.create_refund_request(request)).model_dump(mode="json")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
