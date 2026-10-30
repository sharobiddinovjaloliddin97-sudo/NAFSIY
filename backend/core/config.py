from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
import ipaddress


class Settings(BaseSettings):
    DATABASE_URL: str
    QURAN_EDITION_UZ: str = ""
    QURAN_EDITION_EN: str = "en.sahih"
    QURAN_EDITION_RU: str = "ru.kuliev"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    JWT_SECRET_KEY: str = ""
    AUTH_COOKIE_SECURE: bool = True
    AUTH_TRUSTED_ORIGINS: list[str] = [
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:8000", "http://127.0.0.1:8000",
    ]
    TRUSTED_PROXY_CIDRS: list[str] = []

    @field_validator("TRUSTED_PROXY_CIDRS")
    @classmethod
    def valid_proxy_networks(cls, value):
        networks = [ipaddress.ip_network(item, strict=False) for item in value]
        if any(network.prefixlen == 0 for network in networks):
            raise ValueError("Trust only explicitly identified proxy networks")
        return [str(network) for network in networks]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
