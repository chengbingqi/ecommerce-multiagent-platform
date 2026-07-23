from app.core.config import get_settings


def psycopg_database_url() -> str:
    value = get_settings().database_url.get_secret_value()
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)
