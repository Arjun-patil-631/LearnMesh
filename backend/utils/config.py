import os
from typing import List, Literal, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Core Application
    APP_NAME: str = "LearnMesh"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    JSON_LOGS: bool = False
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    API_V1_STR: str = "/api/v1"
    CORS_ORIGINS: List[str] = ["*"]
    REQUEST_TIMEOUT_SECONDS: float = 30.0

    # Database Configuration (Swappable: SQLite for local dev/testing, PostgreSQL for production)
    DATABASE_URL: str = "sqlite:///./learnmesh.db"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_ECHO: bool = False

    # Hindsight Long-Term Memory Service (optional external memory backend)
    HINDSIGHT_BASE_URL: str = "http://localhost:8888"
    HINDSIGHT_API_KEY: Optional[str] = None
    HINDSIGHT_BANK_ID: str = "learnmesh-org-fleet"
    HINDSIGHT_TIMEOUT_SECONDS: float = 10.0

    # Gemini LLM Service (fallback reasoning provider)
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-3.5-flash"
    GEMINI_TIMEOUT_SECONDS: float = 15.0

    # Groq LLM Service
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GROQ_TIMEOUT_SECONDS: float = 15.0

    # Feature Flags
    FEATURE_CIRCUIT_BREAKER: bool = True
    FEATURE_BACKGROUND_QUEUE: bool = True
    FEATURE_IDEMPOTENCY: bool = True
    FEATURE_PROMETHEUS_METRICS: bool = True
    FEATURE_DEGRADED_LOCAL_CACHE: bool = True
    FEATURE_HINDSIGHT: bool = True

    # Circuit Breaker & Resilience
    CB_FAILURE_THRESHOLD: int = 3
    CB_RECOVERY_TIMEOUT_SECONDS: float = 30.0
    RETRY_MAX_ATTEMPTS: int = 3
    RETRY_BACKOFF_BASE: float = 0.5

    # Task Queue Settings
    TASK_QUEUE_WORKERS: int = 2
    TASK_QUEUE_MAX_SIZE: int = 1000

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in self.DATABASE_URL.lower()

    @property
    def is_postgres(self) -> bool:
        return "postgresql" in self.DATABASE_URL.lower() or "postgres" in self.DATABASE_URL.lower()

settings = Settings()

def validate_startup_config() -> dict:
    """Validate runtime environment and return operational diagnostic summary."""
    status = {
        "environment": settings.ENVIRONMENT,
        "database_backend": "SQLite" if settings.is_sqlite else ("PostgreSQL" if settings.is_postgres else "Other"),
        "database_url_configured": bool(settings.DATABASE_URL),
        "hindsight_enabled": settings.FEATURE_HINDSIGHT,
        "hindsight_configured": bool(settings.FEATURE_HINDSIGHT and settings.HINDSIGHT_BASE_URL),
        "groq_configured": bool(settings.GROQ_API_KEY and settings.GROQ_API_KEY.startswith("gsk_")),
        "gemini_configured": bool(settings.GEMINI_API_KEY),
        "feature_flags": {
            "circuit_breaker": settings.FEATURE_CIRCUIT_BREAKER,
            "background_queue": settings.FEATURE_BACKGROUND_QUEUE,
            "idempotency": settings.FEATURE_IDEMPOTENCY,
            "prometheus_metrics": settings.FEATURE_PROMETHEUS_METRICS,
            "degraded_local_cache": settings.FEATURE_DEGRADED_LOCAL_CACHE,
        }
    }
    return status
