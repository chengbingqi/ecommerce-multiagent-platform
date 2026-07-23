from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db import models  # noqa: F401
from app.db.base import Base
from app.seed.generator import load_manifest
from app.seed.service import seed_manifest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/manifests/stage2_base_v1.json"


@pytest_asyncio.fixture
async def stage6_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage6.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await seed_manifest(factory, load_manifest(MANIFEST))
    yield factory
    await engine.dispose()


@pytest.fixture
def stage6_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'unused.db'}",
        redis_url="redis://localhost:6379/15",
        deepseek_api_key="",
        agent_llm_mode="fake",
        enable_dev_principal_input=True,
        orchestrator_max_subtasks=3,
        orchestrator_max_hops=1,
        orchestrator_max_steps=16,
        orchestrator_deadline_seconds=55,
        orchestrator_subtask_timeout_seconds=5,
    )
