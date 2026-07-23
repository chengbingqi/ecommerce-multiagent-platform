from app.core.config import Settings, get_settings


def test_settings_load_from_environment() -> None:
    settings = get_settings()
    assert settings.app_env == "test"
    assert settings.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert settings.redis_url.get_secret_value().startswith("redis://")
    assert "test_password" not in repr(settings)


def test_explicit_production_settings() -> None:
    # secret-scan: allow-test-fixture
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://user:secret@db/name",
        redis_url="redis://:secret@redis/0",
        agent_llm_mode="deepseek",
        enable_dev_principal_input=False,
    )
    assert settings.app_env == "production"
    assert "secret" not in repr(settings)
