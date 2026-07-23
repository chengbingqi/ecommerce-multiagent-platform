import os
import subprocess
from pathlib import Path

from sqlalchemy import create_engine, inspect


def test_migration_upgrade_and_downgrade_in_disposable_database(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "migration.db"
    url = f"sqlite+aiosqlite:///{database}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": url,
        "REDIS_URL": "redis://localhost:6379/15",
    }
    subprocess.run(
        [str(root / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    sync_engine = create_engine(f"sqlite:///{database}")
    assert "orders" in inspect(sync_engine).get_table_names()
    sync_engine.dispose()
    subprocess.run(
        [str(root / ".venv/bin/alembic"), "downgrade", "base"],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    sync_engine = create_engine(f"sqlite:///{database}")
    assert "orders" not in inspect(sync_engine).get_table_names()
    sync_engine.dispose()
