from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    APP_NAME: str = "AI Chief of Staff"
    VERSION: str = "1.0.0"
    DATABASE_URL: str = "sqlite:///./ai_chief.db"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen:7b"
    SECRET_KEY: str = "change-this-to-a-random-secret-key"
    GMAIL_CREDENTIALS_FILE: Optional[str] = None

    class Config:
        env_file = ".env"


settings = Settings()
