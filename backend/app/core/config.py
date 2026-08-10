from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    APP_NAME: str = "AI Chief of Staff"
    VERSION: str = "1.0.0"
    DATABASE_URL: str = "sqlite:///./ai_chief.db"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b"
    SECRET_KEY: str = "change-this-to-a-random-secret-key"
    GMAIL_CREDENTIALS_FILE: Optional[str] = None
    MS_CLIENT_ID: Optional[str] = None
    MS_TENANT_ID: str = "consumers"
    AUTH_PASSWORD: Optional[str] = None
    AUTH_TOKEN_TTL_DAYS: int = 7

    class Config:
        env_file = _ENV_FILE


settings = Settings()
