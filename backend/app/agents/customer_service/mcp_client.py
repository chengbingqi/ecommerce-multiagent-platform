import asyncio
import json
from collections import defaultdict
from typing import Any, Protocol

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_servers.common.contracts import (
    CallContext,
    ToolErrorData,
    ToolMeta,
    ToolResult,
)

CUSTOMER_SERVICE_TOOL_WHITELIST = frozenset(
    {
        "get_order",
        "get_logistics",
        "search_policy",
        "get_document_chunk",
        "check_refund_eligibility",
        "create_refund_request",
    }
)
READ_ONLY_TOOLS = CUSTOMER_SERVICE_TOOL_WHITELIST - {"create_refund_request"}


class MCPToolClient(Protocol):
    async def call(
        self, tool_name: str, arguments: dict[str, Any], context: CallContext
    ) -> ToolResult: ...


def _structured(result: object) -> dict[str, Any]:
    value = getattr(result, "structuredContent", None)
    if isinstance(value, dict):
        return value
    content = getattr(result, "content", [])
    if content and hasattr(content[0], "text"):
        parsed = json.loads(content[0].text)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("MCP response did not contain structured content")


class StreamableMCPToolClient:
    def __init__(self, business_url: str, knowledge_url: str) -> None:
        self.urls = {"business": business_url, "knowledge": knowledge_url}

    async def call(
        self, tool_name: str, arguments: dict[str, Any], context: CallContext
    ) -> ToolResult:
        if tool_name not in CUSTOMER_SERVICE_TOOL_WHITELIST:
            return self._local_error(tool_name, context, "FORBIDDEN", "tool is not allowed")
        domain = "knowledge" if tool_name in {"search_policy", "get_document_chunk"} else "business"
        request = {"context": context.model_dump(mode="json"), **arguments}
        try:
            async with asyncio.timeout(10):
                async with streamable_http_client(self.urls[domain]) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, {"request": request})
            return ToolResult.model_validate(_structured(result))
        except TimeoutError:
            return self._local_error(tool_name, context, "TIMEOUT", "tool call timed out", True)
        except Exception:
            return self._local_error(
                tool_name, context, "TRANSIENT_ERROR", "tool service is unavailable", True
            )

    @staticmethod
    def _local_error(
        tool_name: str,
        context: CallContext,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> ToolResult:
        return ToolResult(
            ok=False,
            error=ToolErrorData(
                error_code=code,
                message=message,
                retryable=retryable,
                trace_id=context.trace_id,
            ),
            meta=ToolMeta(
                tool_name=tool_name,
                tool_version="1.0.0",
                run_id=context.run_id,
                task_id=context.task_id,
                trace_id=context.trace_id,
                latency_ms=0,
            ),
        )


class FakeMCPToolClient:
    def __init__(self, responses: dict[str, ToolResult | list[ToolResult]]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.call_counts: defaultdict[str, int] = defaultdict(int)

    async def call(
        self, tool_name: str, arguments: dict[str, Any], context: CallContext
    ) -> ToolResult:
        del arguments
        self.calls.append(tool_name)
        index = self.call_counts[tool_name]
        self.call_counts[tool_name] += 1
        configured = self.responses[tool_name]
        result = (
            configured[min(index, len(configured) - 1)]
            if isinstance(configured, list)
            else configured
        )
        return result.model_copy(
            update={
                "meta": result.meta.model_copy(
                    update={
                        "run_id": context.run_id,
                        "task_id": context.task_id,
                        "trace_id": context.trace_id,
                    }
                )
            }
        )
