import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.customer_service.repository import RepositoryFailure
from app.db.models import MediaAsset, Principal, Store
from app.media.repository import MediaRepository
from app.media.schemas import UploadCreateRequest
from app.media.security import confined_task_path, safe_object_prefix
from app.media.service import MediaService


class StreamRequest:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class InterruptedRequest:
    async def stream(self) -> AsyncIterator[bytes]:
        yield b"partial"
        raise ConnectionError("controlled upload interruption")


@pytest.mark.parametrize(
    ("identifier", "valid"),
    [("safe-upload-1", True), ("../escape", False), ("/absolute", False), ("a/b", False)],
)
def test_workspace_confinement(tmp_path: Path, identifier: str, valid: bool) -> None:
    if valid:
        assert confined_task_path(tmp_path, identifier).parent == tmp_path.resolve()
    else:
        with pytest.raises(ValueError):
            confined_task_path(tmp_path, identifier)


def test_object_namespace_rejects_path_control() -> None:
    assert safe_object_prefix("store-001", "owner-001") == "store-001/owner-001"
    with pytest.raises(ValueError):
        safe_object_prefix("../store", "owner")


@pytest.mark.asyncio
async def test_streamed_upload_is_idempotent_and_filename_is_not_a_path(
    stage5_factory: async_sessionmaker[AsyncSession], stage5_settings, fake_storage
) -> None:
    service = MediaService(
        repository=MediaRepository(stage5_factory),
        storage=fake_storage,
        settings=stage5_settings,
        enqueue=lambda *_: None,
    )
    async with stage5_factory() as session:
        store_id = await session.scalar(
            select(Principal.store_id).where(Principal.id == "content-operator-001")
        )
    request = UploadCreateRequest(
        principal_id="content-operator-001",
        store_id=str(store_id),
        display_filename="../../not-used-as-path.mp4",
        idempotency_key="upload-safe-001",
    )
    first = await service.create_upload(request)
    second = await service.create_upload(request)
    assert first == second
    progress = await service.receive_upload(
        first.upload_id,
        request.principal_id,
        StreamRequest([b"abc", b"def"]),  # type: ignore[arg-type]
    )
    assert progress.received_bytes == 6
    files = list(service.upload_root.glob("*.part"))
    assert [path.name for path in files] == [f"{first.upload_id}.part"]
    assert (
        hashlib.sha256(files[0].read_bytes()).hexdigest() == hashlib.sha256(b"abcdef").hexdigest()
    )


@pytest.mark.asyncio
async def test_upload_permission_and_store_scope_fail_closed(
    stage5_factory: async_sessionmaker[AsyncSession], stage5_settings
) -> None:
    repository = MediaRepository(stage5_factory)
    async with stage5_factory() as session:
        own_store = await session.scalar(
            select(Principal.store_id).where(Principal.id == "content-operator-001")
        )
        other_store = await session.scalar(select(Store.id).where(Store.id != own_store))
    with pytest.raises(RepositoryFailure) as role_error:
        await repository.create_upload(
            UploadCreateRequest(
                principal_id="customer-001",
                store_id=str(own_store),
                display_filename="x.mp4",
                idempotency_key="upload-denied-role",
            ),
            3,
        )
    assert role_error.value.code == "FORBIDDEN"
    with pytest.raises(RepositoryFailure) as scope_error:
        await repository.create_upload(
            UploadCreateRequest(
                principal_id="content-operator-001",
                store_id=str(other_store),
                display_filename="x.mp4",
                idempotency_key="upload-denied-store",
            ),
            3,
        )
    assert scope_error.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_upload_size_interruption_and_pending_quota_are_enforced(
    stage5_factory: async_sessionmaker[AsyncSession], stage5_settings, fake_storage
) -> None:
    service = MediaService(
        repository=MediaRepository(stage5_factory),
        storage=fake_storage,
        settings=stage5_settings,
        enqueue=lambda *_: None,
    )
    async with stage5_factory() as session:
        store_id = str(
            await session.scalar(
                select(Principal.store_id).where(Principal.id == "content-operator-001")
            )
        )
    oversized = await service.create_upload(
        UploadCreateRequest(
            principal_id="content-operator-001",
            store_id=store_id,
            display_filename="oversized.mp4",
            idempotency_key="upload-size-limit-001",
        )
    )
    with pytest.raises(RepositoryFailure) as size_error:
        await service.receive_upload(
            oversized.upload_id,
            "content-operator-001",
            StreamRequest([b"x" * (stage5_settings.media_upload_max_bytes + 1)]),  # type: ignore[arg-type]
        )
    assert size_error.value.code == "INVALID_ARGUMENT"
    assert not (service.upload_root / f"{oversized.upload_id}.part").exists()

    interrupted = await service.create_upload(
        UploadCreateRequest(
            principal_id="content-operator-001",
            store_id=store_id,
            display_filename="interrupted.mp4",
            idempotency_key="upload-interrupted-001",
        )
    )
    with pytest.raises(ConnectionError):
        await service.receive_upload(
            interrupted.upload_id,
            "content-operator-001",
            InterruptedRequest(),  # type: ignore[arg-type]
        )
    async with stage5_factory() as session:
        row = await session.get(MediaAsset, interrupted.asset_id)
    assert row and row.status == "PENDING_UPLOAD"

    await service.create_upload(
        UploadCreateRequest(
            principal_id="content-operator-001",
            store_id=store_id,
            display_filename="third-pending.mp4",
            idempotency_key="upload-third-pending-001",
        )
    )
    with pytest.raises(RepositoryFailure) as quota_error:
        await service.create_upload(
            UploadCreateRequest(
                principal_id="content-operator-001",
                store_id=store_id,
                display_filename="quota.mp4",
                idempotency_key="upload-pending-quota-001",
            )
        )
    assert quota_error.value.code == "TRANSIENT_ERROR"


@pytest.mark.asyncio
async def test_duplicate_hash_reuses_server_object_without_client_key_control(
    stage5_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = MediaRepository(stage5_factory)
    async with stage5_factory() as session:
        store_id = str(
            await session.scalar(
                select(Principal.store_id).where(Principal.id == "content-operator-001")
            )
        )
    rows = []
    for index in (1, 2):
        upload = await repository.create_upload(
            UploadCreateRequest(
                principal_id="content-operator-001",
                store_id=store_id,
                display_filename=f"duplicate-{index}.mp4",
                idempotency_key=f"duplicate-upload-{index:03}",
            ),
            3,
        )
        await repository.mark_uploaded(upload.upload_id, "content-operator-001", 100)
        rows.append(
            await repository.finalize_asset(
                upload.upload_id,
                "content-operator-001",
                content_hash="f" * 64,
                bucket="media-original",
                object_key=f"server/generated/{index}.mp4",
                mime_type="video/mp4",
                duration_ms=10_000,
                width=320,
                height=180,
                fps=25,
                video_codec="h264",
                audio_codec="aac",
            )
        )
    assert rows[0].id != rows[1].id
    assert rows[0].object_key == rows[1].object_key == "server/generated/1.mp4"
