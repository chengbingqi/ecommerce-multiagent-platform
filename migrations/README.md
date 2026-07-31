# Alembic migrations

迁移使用 Alembic 管理。新增或修改数据模型时，通过 `alembic revision --autogenerate` 生成候选迁移，并在提交前人工检查 upgrade、downgrade、索引和外键顺序。
