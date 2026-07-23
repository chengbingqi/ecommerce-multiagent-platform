import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-at-least-thirty-two-characters")
os.environ.setdefault("CSRF_SECRET", "test-csrf-secret-at-least-thirty-two-characters")
