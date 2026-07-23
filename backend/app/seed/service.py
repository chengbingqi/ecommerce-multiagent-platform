from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import models
from app.seed.generator import TABLE_ORDER, canonical_checksum, generate_dataset

MODEL_BY_TABLE = {
    "stores": models.Store,
    "principals": models.Principal,
    "products": models.Product,
    "skus": models.SKU,
    "inventories": models.Inventory,
    "orders": models.Order,
    "order_items": models.OrderItem,
    "logistics": models.Logistics,
    "refunds": models.Refund,
    "sales_daily": models.SalesDaily,
    "campaigns": models.Campaign,
    "knowledge_documents": models.KnowledgeDocument,
    "knowledge_chunks": models.KnowledgeChunk,
    "sops": models.SOP,
}
RESET_MODELS = (
    models.SeedManifest,
    models.IdempotencyRecord,
    models.Approval,
    models.ToolCallAudit,
    models.SOP,
    models.KnowledgeChunk,
    models.KnowledgeDocument,
    models.Campaign,
    models.SalesDaily,
    models.Refund,
    models.Logistics,
    models.OrderItem,
    models.Order,
    models.Inventory,
    models.SKU,
    models.Product,
    models.Principal,
    models.Store,
)


def _db_row(model: type[Any], row: dict[str, Any]) -> dict[str, Any]:
    converted = dict(row)
    for column in model.__table__.columns:
        value = converted.get(column.name)
        if value is None:
            continue
        python_type = column.type.python_type
        if python_type is datetime and isinstance(value, str):
            converted[column.name] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif python_type is date and isinstance(value, str):
            converted[column.name] = date.fromisoformat(value)
        elif python_type is Decimal and not isinstance(value, Decimal):
            converted[column.name] = Decimal(value)
    return converted


async def seed_manifest(
    session_factory: async_sessionmaker[AsyncSession], manifest: dict[str, Any]
) -> tuple[str, bool]:
    dataset = generate_dataset(manifest)
    checksum = canonical_checksum(dataset)
    async with session_factory() as session, session.begin():
        existing = await session.get(models.SeedManifest, manifest["manifest_version"])
        if existing:
            if existing.checksum != checksum:
                raise ValueError("manifest version exists with a different checksum")
            return checksum, False
        for table_name in TABLE_ORDER:
            rows = dataset[table_name]
            if rows:
                model = MODEL_BY_TABLE[table_name]
                await session.execute(insert(model), [_db_row(model, row) for row in rows])
        clock = datetime.fromisoformat(manifest["simulated_clock"].replace("Z", "+00:00"))
        session.add(
            models.SeedManifest(
                manifest_version=manifest["manifest_version"],
                seed=int(manifest["seed"]),
                simulated_clock=clock,
                rules_version=manifest["rules_version"],
                checksum=checksum,
                counts=manifest["counts"],
                applied_at=clock,
            )
        )
    return checksum, True


async def verify_manifest(
    session_factory: async_sessionmaker[AsyncSession], manifest: dict[str, Any]
) -> dict[str, Any]:
    expected_checksum = canonical_checksum(generate_dataset(manifest))
    actual_counts: dict[str, int] = {}
    async with session_factory() as session:
        record = await session.get(models.SeedManifest, manifest["manifest_version"])
        if record is None:
            raise ValueError("manifest is not applied")
        for table_name in TABLE_ORDER:
            model = MODEL_BY_TABLE[table_name]
            actual_counts[table_name] = int(
                await session.scalar(select(func.count()).select_from(model)) or 0
            )
    expected_counts = {name: int(value) for name, value in manifest["counts"].items()}
    counts_match = all(actual_counts[name] == count for name, count in expected_counts.items())
    return {
        "manifest_version": manifest["manifest_version"],
        "checksum": expected_checksum,
        "checksum_match": record.checksum == expected_checksum,
        "counts_match": counts_match,
        "actual_counts": actual_counts,
    }


async def reset_stage2_data(
    session_factory: async_sessionmaker[AsyncSession], *, app_env: str, confirmed: bool
) -> None:
    if app_env not in {"development", "test"}:
        raise ValueError("reset is allowed only in development or test")
    if not confirmed:
        raise ValueError("reset requires --confirm-reset")
    async with session_factory() as session, session.begin():
        for model in RESET_MODELS:
            await session.execute(delete(model))
