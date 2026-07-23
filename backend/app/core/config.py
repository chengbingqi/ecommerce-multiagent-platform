from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"] = "development"
    service_role: Literal["api", "mcp", "media_worker"] = "api"
    database_url: SecretStr = SecretStr("")
    database_url_file: Path | None = None
    redis_url: SecretStr = SecretStr("")
    redis_url_file: Path | None = None
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_api_key_file: Path | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    llm_max_tokens: int = Field(default=512, ge=64, le=4096)
    llm_temperature: float = Field(default=0.1, ge=0, le=0.3)
    llm_max_retries: int = Field(default=1, ge=0, le=2)
    llm_max_calls_per_run: int = Field(default=3, ge=1, le=4)
    agent_max_steps: int = Field(default=12, ge=6, le=30)
    agent_deadline_seconds: float = Field(default=25.0, gt=1, le=60)
    orchestrator_max_subtasks: int = Field(default=3, ge=1, le=3)
    orchestrator_max_hops: int = Field(default=1, ge=1, le=1)
    orchestrator_max_steps: int = Field(default=16, ge=13, le=20)
    orchestrator_deadline_seconds: float = Field(default=55.0, gt=1, le=60)
    orchestrator_subtask_timeout_seconds: float = Field(default=20.0, gt=1, le=60)
    orchestrator_route_version: str = "orchestrator_route_v1.0.0"
    orchestrator_plan_version: str = "orchestrator_plan_v1.0.0"
    agent_llm_mode: Literal["deepseek", "fake"] = "fake"
    enable_dev_principal_input: bool = True
    business_mcp_url: str = "http://business-mcp:8101/mcp"
    knowledge_mcp_url: str = "http://knowledge-mcp:8102/mcp"
    media_mcp_url: str = "http://media-mcp:8103/mcp"
    media_enabled: bool = False
    data_root: Path = Path(".local-data")
    minio_endpoint: str = "minio:9000"
    minio_access_key: SecretStr = SecretStr("CHANGE_ME")
    minio_access_key_file: Path | None = None
    minio_secret_key: SecretStr = Field(default=SecretStr("CHANGE_ME"), repr=False)
    minio_secret_key_file: Path | None = Field(default=None, repr=False)
    minio_secure: bool = False
    media_original_bucket: str = "media-original"
    media_derived_bucket: str = "media-derived"
    media_quarantine_bucket: str = "media-quarantine"
    media_upload_max_bytes: int = Field(default=209_715_200, ge=1, le=209_715_200)
    media_max_duration_seconds: int = Field(default=600, ge=1, le=600)
    media_max_pending_uploads: int = Field(default=3, ge=1, le=10)
    media_worker_concurrency: int = Field(default=1, ge=1, le=1)
    media_task_soft_limit_seconds: int = Field(default=1200, ge=60, le=1800)
    media_task_hard_limit_seconds: int = Field(default=1500, ge=90, le=1800)
    media_subprocess_timeout_seconds: int = Field(default=300, ge=10, le=600)
    media_pipeline_version: str = "media_pipeline_v1.2.1"
    asr_model: str = "Systran/faster-whisper-large-v3"
    asr_model_id: str = "large-v3"
    asr_model_version: str = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
    asr_model_revision: str = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
    asr_model_path: Path = Path(
        "/data/models/asr/large-v3-edaa852ec7e145841d8ffdb056a99866b5f0a478"
    )
    asr_device: Literal["cpu", "cuda"] = "cuda"
    asr_device_index: int = Field(default=0, ge=0, le=0)
    asr_compute_type: Literal["int8", "float16"] = "float16"
    asr_cpu_threads: int = Field(default=8, ge=1, le=8)

    # Stage 7 authentication and reverse-proxy boundary. Production values are
    # deliberately finite and must not be relaxed to an unlimited sentinel.
    session_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    session_secret_file: Path | None = Field(default=None, repr=False)
    csrf_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    csrf_secret_file: Path | None = Field(default=None, repr=False)
    session_cookie_name: str = "ecom_session"
    session_idle_seconds: int = Field(default=1800, ge=300, le=86_400)
    session_absolute_seconds: int = Field(default=28_800, ge=1800, le=604_800)
    auth_lock_threshold: int = Field(default=5, ge=3, le=20)
    auth_lock_seconds: int = Field(default=900, ge=60, le=86_400)
    allowed_hosts: str = "localhost,127.0.0.1,testserver,test"
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    trusted_proxy_cidrs: str = "127.0.0.1/32"
    public_base_url: str = "http://localhost:5173"
    secure_session_cookie: bool = False
    csrf_header_name: str = "X-CSRF-Token"
    security_policy_version: str = "stage7_security_v1.0.0"
    enforce_stage7_security: bool = False
    rate_limit_prefix: str = "stage7:rate:v1"
    rate_general_per_minute: int = Field(default=120, ge=1, le=10_000)
    rate_login_per_15_minutes: int = Field(default=5, ge=1, le=100)
    rate_run_per_minute: int = Field(default=10, ge=1, le=1000)
    rate_model_daily: int = Field(default=200, ge=1, le=100_000)
    rate_sse_concurrent: int = Field(default=3, ge=1, le=20)
    rate_upload_daily: int = Field(default=10, ge=1, le=1000)
    rate_media_daily: int = Field(default=5, ge=1, le=1000)
    rate_approval_per_minute: int = Field(default=10, ge=1, le=1000)
    rate_download_per_minute: int = Field(default=30, ge=1, le=1000)
    public_demo_policy_version: str = "public_demo_policy_v1.0.0"
    public_demo_orders_manifest: str = "data/manifests/public_demo_orders_v1.json"
    public_demo_ip_requests_per_day: int = Field(default=80, ge=1, le=10_000)
    public_demo_account_runs_per_day: int = Field(default=20, ge=1, le=1000)
    public_demo_session_requests_per_hour: int = Field(default=20, ge=1, le=1000)
    public_demo_session_concurrent_runs: int = Field(default=1, ge=1, le=3)
    public_demo_global_concurrent_runs: int = Field(default=1, ge=1, le=1)
    public_demo_sse_concurrent_per_session: int = Field(default=1, ge=1, le=3)
    public_demo_model_daily: int = Field(default=20, ge=1, le=1000)
    media_storage_quota_bytes: int = Field(default=5_368_709_120, ge=1)
    disk_upload_warn_percent: int = Field(default=70, ge=1, le=99)
    disk_upload_stop_percent: int = Field(default=85, ge=1, le=99)

    @property
    def allowed_host_list(self) -> list[str]:
        return [value.strip() for value in self.allowed_hosts.split(",") if value.strip()]

    @property
    def allowed_origin_list(self) -> list[str]:
        return [
            value.strip().rstrip("/") for value in self.allowed_origins.split(",") if value.strip()
        ]

    @property
    def trusted_proxy_cidr_list(self) -> list[str]:
        return [value.strip() for value in self.trusted_proxy_cidrs.split(",") if value.strip()]

    @staticmethod
    def _read_secret_file(path: Path, name: str) -> SecretStr:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"unable to read {name} secret file") from exc
        if not value:
            raise ValueError(f"{name} secret file is empty")
        return SecretStr(value)

    @model_validator(mode="after")
    def reject_development_identity_in_production(self) -> "Settings":
        for value_field, file_field in (
            ("database_url", "database_url_file"),
            ("redis_url", "redis_url_file"),
            ("deepseek_api_key", "deepseek_api_key_file"),
            ("minio_access_key", "minio_access_key_file"),
            ("minio_secret_key", "minio_secret_key_file"),
            ("session_secret", "session_secret_file"),
            ("csrf_secret", "csrf_secret_file"),
        ):
            secret_path = getattr(self, file_field)
            if secret_path is not None:
                setattr(self, value_field, self._read_secret_file(secret_path, value_field))
        if not self.database_url.get_secret_value() or not self.redis_url.get_secret_value():
            raise ValueError("database_url and redis_url must be configured")
        if self.asr_model_version != self.asr_model_revision:
            raise ValueError("ASR model version and revision must match")
        if self.asr_device == "cuda":
            if self.asr_device_index != 0:
                raise ValueError("the single-GPU worker must use container CUDA device 0")
            if self.asr_compute_type != "float16":
                raise ValueError("CUDA ASR requires float16 compute")
            if self.asr_model_id != "large-v3":
                raise ValueError("CUDA Stage 5 ASR requires the pinned large-v3 model")
        elif self.asr_compute_type != "int8":
            raise ValueError("CPU ASR requires int8 compute")
        if self.app_env == "production" and self.enable_dev_principal_input:
            raise ValueError("development principal input must be disabled in production")
        if (
            self.app_env == "production"
            and self.service_role == "api"
            and self.agent_llm_mode != "deepseek"
        ):
            raise ValueError("production must use the configured DeepSeek client")
        if (
            self.app_env == "production"
            and self.media_enabled
            and (
                self.minio_access_key.get_secret_value() in {"", "CHANGE_ME"}
                or self.minio_secret_key.get_secret_value() in {"", "CHANGE_ME"}
            )
        ):
            raise ValueError("production object storage credentials must be explicitly configured")
        if self.disk_upload_warn_percent >= self.disk_upload_stop_percent:
            raise ValueError("disk warning threshold must be below the upload stop threshold")
        if self.app_env == "production" and self.enforce_stage7_security:
            required_files: dict[str, Path | None] = {
                "database_url": self.database_url_file,
                "redis_url": self.redis_url_file,
            }
            if self.service_role == "api":
                required_files.update(
                    {
                        "deepseek_api_key": self.deepseek_api_key_file,
                        "session_secret": self.session_secret_file,
                        "csrf_secret": self.csrf_secret_file,
                    }
                )
            if self.media_enabled:
                required_files.update(
                    {
                        "minio_access_key": self.minio_access_key_file,
                        "minio_secret_key": self.minio_secret_key_file,
                    }
                )
            missing = sorted(name for name, path in required_files.items() if path is None)
            if missing:
                raise ValueError("production secrets must use *_FILE: " + ", ".join(missing))
            if self.service_role == "api" and (
                not self.secure_session_cookie or not self.session_cookie_name.startswith("__Host-")
            ):
                raise ValueError(
                    "production session cookie must be Secure and use the __Host- prefix"
                )
            if self.service_role == "api" and not self.public_base_url.startswith("https://"):
                raise ValueError("production public_base_url must use HTTPS")
            if any(value in {"*", "0.0.0.0"} for value in self.allowed_host_list):
                raise ValueError("production allowed_hosts must be explicit")
            if any(value == "*" for value in self.allowed_origin_list):
                raise ValueError("production CORS origins must be explicit")
            if self.service_role == "api" and len(self.session_secret.get_secret_value()) < 32:
                raise ValueError("production session secret must contain at least 32 characters")
            if self.service_role == "api" and len(self.csrf_secret.get_secret_value()) < 32:
                raise ValueError("production CSRF secret must contain at least 32 characters")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
