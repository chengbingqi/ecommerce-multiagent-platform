from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import models  # noqa: F401
from app.db.base import Base
from app.seed.generator import load_manifest
from app.seed.service import seed_manifest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "data/manifests/stage2_base_v1.json"


@pytest.fixture
def manifest() -> dict:
    return load_manifest(MANIFEST_PATH)


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage2.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await seed_manifest(factory, load_manifest(MANIFEST_PATH))
    yield factory
    await engine.dispose()
