import asyncio
import json
from collections import defaultdict
from typing import Any, Protocol

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_servers.common.contracts import CallContext, ToolErrorData, ToolMeta, ToolResult

OPERATIONS_TOOL_WHITELIST = frozenset({"get_operations_metrics", "retrieve_sop"})
READ_ONLY_TOOLS = OPERATIONS_TOOL_WHITELIST


class OperationsToolClient(Protocol):
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


class StreamableOperationsToolClient:
    def __init__(self, business_url: str, knowledge_url: str) -> None:
        self.urls = {
            "get_operations_metrics": business_url,
            "retrieve_sop": knowledge_url,
        }

    async def call(
        self, tool_name: str, arguments: dict[str, Any], context: CallContext
    ) -> ToolResult:
        if tool_name not in OPERATIONS_TOOL_WHITELIST:
            return self._error(tool_name, context, "FORBIDDEN", "tool is not allowed")
        try:
            async with asyncio.timeout(10):
                async with streamable_http_client(self.urls[tool_name]) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        response = await session.call_tool(
                            tool_name,
                            {"request": {"context": context.model_dump(mode="json"), **arguments}},
                        )
            return ToolResult.model_validate(_structured(response))
        except TimeoutError:
            return self._error(tool_name, context, "TIMEOUT", "tool call timed out", True)
        except Exception:
            return self._error(
                tool_name, context, "TRANSIENT_ERROR", "tool service is unavailable", True
            )

    @staticmethod
    def _error(
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
                tool_version="1.1.0" if tool_name == "get_operations_metrics" else "1.0.0",
                run_id=context.run_id,
                task_id=context.task_id,
                trace_id=context.trace_id,
                latency_ms=0,
            ),
        )


class FakeOperationsToolClient:
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
