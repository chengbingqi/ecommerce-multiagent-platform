import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models import Principal, Store
from app.seed.generator import load_manifest
from app.seed.service import seed_manifest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/manifests/stage2_base_v1.json"


@pytest_asyncio.fixture
async def stage5_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage5.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await seed_manifest(factory, load_manifest(MANIFEST))
    async with factory() as session, session.begin():
        store_id = await session.scalar(
            select(Principal.store_id).where(Principal.id == "operator-store-001")
        )
        session.add(
            Principal(
                id="content-operator-001",
                role="content_operator",
                store_id=store_id,
                customer_ref=None,
                active=True,
            )
        )
        other_store = await session.scalar(
            select(Store.id).where(Store.id != store_id).order_by(Store.id)
        )
        session.add(
            Principal(
                id="content-operator-002",
                role="content_operator",
                store_id=other_store,
                customer_ref=None,
                active=True,
            )
        )
    yield factory
    await engine.dispose()


@pytest.fixture
def stage5_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'unused.db'}",
        redis_url="redis://localhost:6379/15",
        data_root=tmp_path / "data",
        minio_access_key="test-access",
        minio_secret_key="test-password-not-a-real-secret",
        media_enabled=True,
        media_upload_max_bytes=2 * 1024 * 1024,
        media_max_duration_seconds=60,
        media_subprocess_timeout_seconds=30,
    )


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    async def ensure_buckets(self) -> None:
        return None

    async def put_file(self, bucket: str, key: str, path: Path, content_type: str) -> None:
        del content_type
        self.objects[(bucket, key)] = path.read_bytes()  # noqa: ASYNC240

    async def put_bytes(self, bucket: str, key: str, payload: bytes, content_type: str) -> None:
        del content_type
        self.objects[(bucket, key)] = payload

    async def get_file(self, bucket: str, key: str, destination: Path) -> None:
        destination.write_bytes(self.objects[(bucket, key)])  # noqa: ASYNC240

    async def stat(self, bucket: str, key: str) -> tuple[int, str | None]:
        return len(self.objects[(bucket, key)]), None


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture(scope="session")
def fixed_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("stage5-video")
    path = root / "fixed.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    yield path
    shutil.rmtree(root)
