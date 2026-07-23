import os
from typing import Any

from app.db.session import get_sessionmaker
from mcp_servers.common.server import create_mcp
from mcp_servers.knowledge.schemas import ChunkInput, PolicySearchInput, SOPInput
from mcp_servers.knowledge.service import KnowledgeTools

service = KnowledgeTools(get_sessionmaker())
mcp = create_mcp("knowledge-mcp", int(os.getenv("MCP_PORT", "8102")))


@mcp.tool(structured_output=True)
async def search_policy(request: PolicySearchInput) -> dict[str, Any]:
    """Search current effective policy/FAQ chunks with stable citations."""
    return (await service.search_policy(request)).model_dump(mode="json")


@mcp.tool(structured_output=True)
async def retrieve_sop(request: SOPInput) -> dict[str, Any]:
    """Retrieve the current effective deterministic SOP."""
    return (await service.retrieve_sop(request)).model_dump(mode="json")


@mcp.tool(structured_output=True)
async def get_document_chunk(request: ChunkInput) -> dict[str, Any]:
    """Reverse-resolve a current chunk citation to source content."""
    return (await service.get_document_chunk(request)).model_dump(mode="json")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
