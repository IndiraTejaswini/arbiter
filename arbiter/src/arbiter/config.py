"""Central settings, read once from the environment. See docker-compose.yml
for the default local values these fall back to."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARBITER_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://arbiter:arbiter@localhost:5432/arbiter"
    redis_url: str = "redis://localhost:6379/0"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "arbiter"
    minio_secret_key: str = "arbiter123"
    minio_bucket: str = "arbiter-artifacts"
    minio_secure: bool = False

    rulepack_dir: Path = REPO_ROOT / "rulepacks" / "amex"

    conformal_alpha: float = 0.05
    conformal_min_n: int = 100

    # Qwen2.5-VL via a local Ollama daemon (arbiter.ingest.extract_vlm).
    # Extraction degrades to the OCR/native path if unreachable -- CLAUDE.md
    # #9: evidence degrades, never rejected; that principle applies to the
    # extraction pipeline's own availability too.
    ollama_base_url: str = "http://localhost:11434"
    vlm_model: str = "qwen2.5vl:7b"

    max_artifact_bytes: int = 25 * 1024 * 1024

    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
