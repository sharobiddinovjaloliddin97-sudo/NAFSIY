from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    QURAN_EDITION_UZ: str = ""
    QURAN_EDITION_EN: str = "en.sahih"
    QURAN_EDITION_RU: str = "ru.kuliev"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    JWT_SECRET_KEY: str = ""
    AUTH_COOKIE_SECURE: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
