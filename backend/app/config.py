from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
    )

    DATABASE_URL: str
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    GEMINI_API_KEY: str | None = None  # unset -> delay messages use the default French text
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"  # fast + cheap; the message is 1-2 sentences
    GOOGLE_CLIENT_ID: str | None = None  # unset -> /auth/google returns 501
    CLINIC_TIMEZONE: str = "Africa/Algiers"  # every time a human reads or types
    PUBLIC_APP_URL: str = "http://localhost:5173"  # base of tracker links sent by SMS/email


settings = Settings()
