import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    APP_PORT: int = 8000
    
    # Database
    DATABASE_URL: str = "sqlite:///./learnmesh.db"
    
    # Hindsight API configuration
    HINDSIGHT_BASE_URL: str = "http://localhost:8888"
    HINDSIGHT_API_KEY: str | None = None
    HINDSIGHT_BANK_ID: str = "learnmesh-org-fleet"
    
    # Groq LLM configuration
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
