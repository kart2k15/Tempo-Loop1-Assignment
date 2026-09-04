from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    github_token: str = ""
    db_path: Path = REPO_ROOT / "data" / "insights.db"
    cache_ttl_seconds: int = 900
    claude_cli_timeout_seconds: int = 60


settings = Settings()
