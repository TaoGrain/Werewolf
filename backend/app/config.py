from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILES = (PROJECT_ROOT / ".env.example", PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    """应用配置；固定值来自根目录 .env，密钥不会进入 API DTO。"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str
    database_url: str
    frontend_origins: list[str]
    llm_provider: str
    llm_model: str
    llm_provider_concurrency_limit: int = Field(default=4, ge=1)
    llm_provider_max_retries: int = Field(default=2, ge=0)
    llm_gateway_max_total_attempts: int = Field(default=9, ge=1)
    llm_history_max_messages: int = Field(default=12, ge=1)
    llm_history_max_characters: int = Field(default=20000, ge=1)
    llm_phase_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_phase_output_token_budget: int = Field(default=1000000, ge=1)
    llm_speech_temperature: float = Field(default=0.55, ge=0, le=2)
    llm_vote_temperature: float = Field(default=0.35, ge=0, le=2)
    llm_max_tokens: int = Field(ge=1)
    llm_timeout_seconds: float = Field(gt=0)

    minimax_model: str
    minimax_api_url: str
    minimax_api_key: SecretStr | None = None
    minimax_api_key_file: Path | None = None

    deepseek_pro_model: str
    deepseek_flash_model: str
    deepseek_api_url: str
    deepseek_api_key: SecretStr | None = None
    deepseek_api_key_file: Path | None = None

    openai_compatible_api_url: str | None = None
    openai_compatible_api_key: SecretStr | None = None

    minimax_run_db: Path
    minimax_run_max_phase_retries: int = Field(ge=1)
    minimax_run_max_steps: int = Field(ge=1)
    minimax_provider_max_retries: int = Field(ge=0)

    @field_validator("database_url")
    @classmethod
    def resolve_relative_sqlite_database(cls, value: str) -> str:
        """Resolve SQLite paths from the project root, not the process cwd."""

        url = make_url(value)
        database = url.database
        if (
            url.drivername.startswith("sqlite")
            and database
            and database != ":memory:"
            and not database.startswith("file:")
        ):
            path = Path(database)
            if not path.is_absolute():
                url = url.set(database=(PROJECT_ROOT / path).resolve().as_posix())
                return url.render_as_string(hide_password=False)
        return value

    @model_validator(mode="after")
    def validate_llm_attempt_budget(self) -> "Settings":
        provider_attempts = self.llm_provider_max_retries + 1
        if self.llm_gateway_max_total_attempts < provider_attempts:
            raise ValueError(
                "llm_gateway_max_total_attempts must cover one provider call"
            )
        return self

    @staticmethod
    def _read_api_key(value: SecretStr | None, configured: Path | None) -> str:
        if value is not None and value.get_secret_value().strip():
            return value.get_secret_value().strip()
        if configured is None:
            return ""
        if not configured.is_absolute():
            configured = (PROJECT_ROOT / configured).resolve()
        try:
            return configured.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def read_minimax_api_key(self) -> str:
        return self._read_api_key(self.minimax_api_key, self.minimax_api_key_file)

    def read_deepseek_api_key(self) -> str:
        return self._read_api_key(self.deepseek_api_key, self.deepseek_api_key_file)


@lru_cache
def get_settings() -> Settings:
    return Settings()
