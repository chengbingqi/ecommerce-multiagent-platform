from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.operations.mcp_client import OperationsToolClient
from app.core.config import Settings
from app.db import models  # noqa: F401
from app.db.base import Base
from app.seed.generator import load_manifest
from app.seed.service import seed_manifest
from mcp_servers.business.schemas import OperationsMetricsInput
from mcp_servers.business.service import BusinessTools
from mcp_servers.common.contracts import CallContext, ToolResult
from mcp_servers.knowledge.schemas import SOPInput
from mcp_servers.knowledge.service import KnowledgeTools

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/manifests/stage2_base_v1.json"


@pytest_asyncio.fixture
async def stage4_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage4.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await seed_manifest(factory, load_manifest(MANIFEST))
    yield factory
    await engine.dispose()


@pytest.fixture
def stage4_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'unused.db'}",
        redis_url="redis://localhost:6379/15",
        deepseek_api_key="",
        agent_max_steps=20,
        agent_deadline_seconds=25,
        enable_dev_principal_input=True,
    )


class DirectOperationsClient(OperationsToolClient):
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.business = BusinessTools(factory)
        self.knowledge = KnowledgeTools(factory)
        self.calls: list[str] = []
        self.call_counts: dict[str, int] = {}

    async def call(
        self, tool_name: str, arguments: dict[str, Any], context: CallContext
    ) -> ToolResult:
        self.calls.append(tool_name)
        self.call_counts[tool_name] = self.call_counts.get(tool_name, 0) + 1
        if tool_name == "get_operations_metrics":
            return await self.business.get_operations_metrics(
                OperationsMetricsInput(context=context, **arguments)
            )
        if tool_name == "retrieve_sop":
            return await self.knowledge.retrieve_sop(SOPInput(context=context, **arguments))
        raise AssertionError(f"unexpected operations tool: {tool_name}")
