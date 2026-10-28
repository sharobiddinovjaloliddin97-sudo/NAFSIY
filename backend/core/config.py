from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    QURAN_EDITION_UZ: str = ""
    QURAN_EDITION_EN: str = "en.sahih"
    QURAN_EDITION_RU: str = "ru.kuliev"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
