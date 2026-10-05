from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")
    app_env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    web_port: int = 5173
    database_url: str = "sqlite:///./data/paper2formula.db"
    checkpoint_database_url: str = "sqlite:///./data/checkpoints.db"
    storage_backend: str = "local"
    storage_dir: Path = ROOT / "data"
    queue_backend: str = "local"
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/0"
    auth_mode: str = "development"
    dev_auth_token: str = "local-paper2formula-token"
    dev_owner_id: str = "local-user"
    jwt_secret: str = ""
    jwt_issuer: str = "paper2formula"
    jwt_audience: str = "paper2formula-api"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_upload_mb: int = 30
    max_pages: int = 100
    min_text_chars: int = 30
    libreoffice_path: str = "soffice"
    conversion_timeout: int = 90
    model_provider: str = "heuristic"
    model_api_key: str = ""
    model_base_url: str = "https://api.openai.com/v1"
    model_name: str = ""
    model_timeout: int = 60
    model_max_calls: int = 50
    model_max_tokens: int = 3000
    task_timeout: int = 1800
    task_lease_seconds: int = 120
    s3_endpoint_url: str = "http://127.0.0.1:9000"
    s3_access_key: str = "paper2formula"
    s3_secret_key: str = "local-paper2formula-storage"
    s3_bucket: str = "paper2formula"
    s3_region: str = "us-east-1"
    mcp_transport: str = "stdio"
    tool_transport: str = "local"
    dispatcher_enabled: bool = True

    @model_validator(mode="after")
    def validate_deployment(self):
        for name, choices in {"queue_backend": {"local", "celery"}, "storage_backend": {"local", "s3"},
                              "auth_mode": {"development", "jwt"}, "tool_transport": {"local", "mcp"}}.items():
            if getattr(self, name) not in choices:
                raise ValueError(f"Invalid {name}")
        if self.auth_mode == "jwt" and len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        if self.app_env != "development" and self.auth_mode == "development":
            raise ValueError("Production requires AUTH_MODE=jwt")
        if self.auth_mode == "development" and (not self.dev_auth_token or not self.dev_owner_id):
            raise ValueError("Development authentication needs DEV_AUTH_TOKEN and DEV_OWNER_ID")
        if min(self.max_upload_mb, self.max_pages, self.task_timeout, self.task_lease_seconds) <= 0:
            raise ValueError("Resource limits must be positive")
        if min(self.min_text_chars, self.conversion_timeout, self.model_timeout,
               self.model_max_calls, self.model_max_tokens) <= 0:
            raise ValueError("Processing limits must be positive")
        if self.model_provider not in {"heuristic", "openai_compatible"}:
            raise ValueError("MODEL_PROVIDER must be heuristic or openai_compatible")
        if not self.model_base_url.startswith(("https://", "http://")):
            raise ValueError("MODEL_BASE_URL must be an HTTP(S) URL")
        self.storage_dir = (ROOT / self.storage_dir).resolve() if not self.storage_dir.is_absolute() else self.storage_dir.resolve()
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
