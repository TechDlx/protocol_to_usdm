"""Application settings, loaded from the environment and the repo-root .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    anthropic_api_key: SecretStr | None = None
    # Optional: the pipeline is designed to run without CDISC Library membership.
    cdisc_api_key: SecretStr | None = None

    studies_root: Path = Field(default=REPO_ROOT / "studies")
    max_upload_mb: int = Field(default=200, gt=0)
    log_level: str = "INFO"

    def resolved_studies_root(self) -> Path:
        root = self.studies_root
        if not root.is_absolute():
            root = REPO_ROOT / root
        return root.resolve()

    def secret_values(self) -> list[str]:
        """Every configured secret, for log redaction."""
        secrets = (self.anthropic_api_key, self.cdisc_api_key)
        return [s.get_secret_value() for s in secrets if s and s.get_secret_value()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
