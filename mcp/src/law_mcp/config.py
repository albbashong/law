"""설정 — 환경변수 UPPER_SNAKE (예: ``law_db_path`` → ``LAW_DB_PATH``)."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ".env"), extra="ignore")

    law_db_path: Path = ROOT / "data" / "lawdb.sqlite"
    facts_max_chars: int = 7600
    excerpt_chars: int = 400
    follow_depth: int = 2
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 7017
    mcp_allowed_hosts: list[str] = []
    mcp_allowed_origins: list[str] = []
    vworld_api_key: str = ""
    vworld_timeout: float = 10.0
    vworld_retries: int = 15          # 정상 키에도 INCORRECT_KEY 가 자주(관측 약 65%) 와서 넉넉히 다시 묻는다
    vworld_retry_wait: float = 0.2    # 다시 묻기 사이 대기(초) — 늘어나되 1초를 넘지 않는다


def get_settings() -> Settings:
    return Settings()
