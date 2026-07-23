"""Export only terms exposed by the approved business MCP."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


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


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("refusing to overwrite lexicon evidence")
    context = {
        "principal_id": "admin-001",
        "agent": "live_video",
        "run_id": "run-stage5-asr-config-comparison",
        "task_id": "task-stage5-asr-config-comparison",
        "trace_id": "trace-stage5-asr-config-comparison",
    }
    async with streamable_http_client("http://business-mcp:8101/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "query_inventory",
                {
                    "request": {
                        "context": context,
                        "store_id": "b39e17d7-c6aa-5aaf-adfc-b84c80d0f8f1",
                        "sku_id": None,
                        "low_stock_threshold": None,
                    }
                },
            )
    payload = structured(result)
    if not payload.get("ok"):
        raise RuntimeError("business MCP inventory query failed")
    data = payload.get("data", {})
    document = {
        "schema_version": "stage5_official_lexicon_v1",
        "created_at": datetime.now(UTC).isoformat(),
        "source": "business-mcp",
        "tool": "query_inventory",
        "tool_version": payload.get("meta", {}).get("tool_version"),
        "store_id": data.get("store_id"),
        "terms": sorted(
            {
                str(item["sku_code"])
                for item in data.get("items", [])
                if str(item.get("sku_code", "")).strip()
            }
        ),
        "evidence_refs": [
            item.get("evidence_ref") for item in data.get("items", []) if item.get("evidence_ref")
        ],
        "limitation": (
            "approved MCP exposes SKU codes, not product names; ground truth was not read"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(document, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    print(
        json.dumps({"term_count": len(document["terms"]), "tool_version": document["tool_version"]})
    )


if __name__ == "__main__":
    asyncio.run(main())
