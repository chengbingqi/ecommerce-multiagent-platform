from pydantic import BaseModel, Field

from mcp_servers.common.contracts import CallContext


class PolicySearchInput(BaseModel):
    context: CallContext
    query: str = Field(min_length=1, max_length=200)
    store_id: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=5, ge=1, le=20)


class SOPInput(BaseModel):
    context: CallContext
    name: str = Field(min_length=1, max_length=160)
    store_id: str | None = Field(default=None, max_length=64)


class ChunkInput(BaseModel):
    context: CallContext
    chunk_id: str = Field(min_length=1, max_length=64)
