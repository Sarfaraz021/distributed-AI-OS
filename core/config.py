from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = ""
    direct_url: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    langsmith_api_key: str = ""
    langchain_tracing_v2: bool = False
    langchain_project: str = "mini-aios"

    llm_backend: str = "fake"  # fake | live
    llm_model: str = "openai/gpt-4o-mini"
    fault_inject_429: bool = False
    crash_after_email: bool = False

    max_attempts: int = 3
    retry_base_s: float = 0.5
    retry_cap_s: float = 8.0
    retry_budget_ratio: float = 0.2
    default_deadline_s: float = 45.0

    circuit_fail_threshold: int = 3
    circuit_cooldown_s: float = 20.0
    circuit_half_open_max: int = 1

    data_dir: Path = Field(default_factory=lambda: ROOT / "data")

    @property
    def email_dir(self) -> Path:
        return self.data_dir / "emails"

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def postgres_dsn(self) -> str:
        # Prefer session-mode pooler (DIRECT_URL, port 5432). Transaction mode
        # (DATABASE_URL, port 6543) breaks prepared statements and SKIP LOCKED.
        url = (self.direct_url or self.database_url).strip().strip('"')
        if not url:
            raise RuntimeError("DIRECT_URL or DATABASE_URL is missing from .env")
        if "sslmode=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.email_dir.mkdir(parents=True, exist_ok=True)
    if settings.langsmith_api_key and settings.langchain_tracing_v2:
        import os

        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.langchain_project)
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    return settings
