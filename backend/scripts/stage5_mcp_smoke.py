import argparse
import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def context(tool: str) -> dict[str, str]:
    return {
        "principal_id": "admin-001",
        "agent": "agent:media",
        "run_id": "stage5-mcp-smoke-run",
        "task_id": "stage5-mcp-smoke-task",
        "trace_id": f"stage5-mcp-smoke-{tool}",
    }


def structured(result: object) -> dict[str, Any]:
    value = getattr(result, "structuredContent", None)
    if isinstance(value, dict):
        return value
    content = getattr(result, "content", [])
    if content and hasattr(content[0], "text"):
        parsed = json.loads(content[0].text)
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("MCP result did not contain structured data")


async def main(asset_id: str, task_id: str) -> None:
    url = "http://media-mcp:8103/mcp"
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            discovered = {item.name for item in (await session.list_tools()).tools}
            expected = {
                "probe_media",
                "submit_transcription",
                "detect_scenes",
                "score_highlights",
                "render_clip",
                "save_asset",
            }
            results: dict[str, bool] = {"discovery": discovered == expected}
            for tool in sorted(expected):
                request: dict[str, Any] = {
                    "context": context(tool),
                    "asset_id": asset_id,
                }
                if tool != "probe_media":
                    request["media_task_id"] = task_id
                if tool in {"render_clip", "save_asset"}:
                    request["idempotency_key"] = f"stage5-mcp-smoke-{tool}"
                response = structured(await session.call_tool(tool, {"request": request}))
                results[tool] = response.get("ok") is True
    if not all(results.values()):
        raise RuntimeError(f"stage5 MCP smoke failed: {results}")
    print(json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("asset_id")
    parser.add_argument("task_id")
    args = parser.parse_args()
    asyncio.run(main(args.asset_id, args.task_id))
