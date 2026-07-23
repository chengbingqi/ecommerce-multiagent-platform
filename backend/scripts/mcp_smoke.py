import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from app.db.models import Order
from app.db.session import get_sessionmaker


def context(principal_id: str) -> dict[str, str]:
    return {
        "principal_id": principal_id,
        "agent": "stage2-smoke",
        "run_id": "run-stage2-smoke",
        "task_id": "task-stage2-smoke",
        "trace_id": f"trace-{principal_id}",
    }


def structured(result: object) -> dict:
    value = getattr(result, "structuredContent", None)
    if isinstance(value, dict):
        return value
    content = getattr(result, "content", [])
    if content and hasattr(content[0], "text"):
        return json.loads(content[0].text)
    raise RuntimeError("MCP result did not contain structured data")


async def call(url: str, tool: str, arguments: dict) -> tuple[list[str], dict]:
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool(tool, arguments)
            return [item.name for item in tools.tools], structured(result)


async def main() -> None:
    async with get_sessionmaker()() as session:
        order = await session.scalar(select(Order).order_by(Order.id))
        if order is None:
            raise RuntimeError("seed data is missing")
    business_tools, business = await call(
        "http://business-mcp:8101/mcp",
        "get_order",
        {"request": {"context": context("admin-001"), "order_id": order.id}},
    )
    knowledge_tools, knowledge = await call(
        "http://knowledge-mcp:8102/mcp",
        "search_policy",
        {"request": {"context": context("admin-001"), "query": "退款", "limit": 5}},
    )
    media_tools, media = await call(
        "http://media-mcp:8103/mcp",
        "probe_media",
        {
            "request": {
                "context": context("agent-media"),
                "media_id": "media-fixture-001",
            }
        },
    )
    _, disabled = await call(
        "http://media-mcp:8103/mcp",
        "submit_transcription",
        {
            "request": {
                "context": context("agent-media"),
                "media_id": "media-fixture-001",
            }
        },
    )
    assertions = {
        "business_discovery": len(business_tools) == 6,
        "business_call": business.get("ok") is True,
        "knowledge_discovery": len(knowledge_tools) == 3,
        "knowledge_call": knowledge.get("ok") is True,
        "media_discovery": len(media_tools) == 6,
        "media_probe": media.get("ok") is True,
        "media_disabled": disabled.get("error", {}).get("error_code") == "FEATURE_NOT_ENABLED",
    }
    if not all(assertions.values()):
        raise RuntimeError(f"MCP smoke assertions failed: {assertions}")
    print(json.dumps(assertions, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
