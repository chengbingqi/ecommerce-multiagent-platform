from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.customer_service.mcp_client import MCPToolClient
from app.core.config import Settings
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models import Order, Principal
from app.seed.generator import load_manifest
from app.seed.service import seed_manifest
from mcp_servers.business.schemas import OrderInput, RefundRequestInput
from mcp_servers.business.service import BusinessTools
from mcp_servers.common.contracts import CallContext, ToolResult
from mcp_servers.knowledge.schemas import ChunkInput, PolicySearchInput
from mcp_servers.knowledge.service import KnowledgeTools

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/manifests/stage2_base_v1.json"


@pytest_asyncio.fixture
async def stage3_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage3.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await seed_manifest(factory, load_manifest(MANIFEST))
    yield factory
    await engine.dispose()


@pytest.fixture
def stage3_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'unused.db'}",
        redis_url="redis://localhost:6379/15",
        deepseek_api_key="",
        agent_max_steps=12,
        agent_deadline_seconds=25,
        enable_dev_principal_input=True,
    )


@pytest_asyncio.fixture
async def own_order(stage3_factory: async_sessionmaker[AsyncSession]) -> Order:
    async with stage3_factory() as session:
        order = await session.scalar(
            select(Order).where(Order.customer_ref == "customer-001").order_by(Order.id)
        )
        assert order is not None
        return order


@pytest_asyncio.fixture
async def other_order(stage3_factory: async_sessionmaker[AsyncSession]) -> Order:
    async with stage3_factory() as session:
        store = await session.scalar(
            select(Principal.store_id).where(Principal.id == "operator-store-001")
        )
        order = await session.scalar(
            select(Order).where(Order.store_id != store).order_by(Order.id)
        )
        assert order is not None
        return order


class DirectMCPClient(MCPToolClient):
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.business = BusinessTools(factory)
        self.knowledge = KnowledgeTools(factory)
        self.calls: list[str] = []

    async def call(
        self, tool_name: str, arguments: dict[str, Any], context: CallContext
    ) -> ToolResult:
        self.calls.append(tool_name)
        if tool_name in {"get_order", "get_logistics", "check_refund_eligibility"}:
            request = OrderInput(context=context, order_id=str(arguments["order_id"]))
            return await getattr(self.business, tool_name)(request)
        if tool_name == "create_refund_request":
            return await self.business.create_refund_request(
                RefundRequestInput(context=context, **arguments)
            )
        if tool_name == "search_policy":
            return await self.knowledge.search_policy(
                PolicySearchInput(context=context, **arguments)
            )
        if tool_name == "get_document_chunk":
            return await self.knowledge.get_document_chunk(
                ChunkInput(context=context, chunk_id=str(arguments["chunk_id"]))
            )
        raise AssertionError(f"unexpected tool: {tool_name}")
