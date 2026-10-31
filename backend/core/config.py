from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from pydantic import Field
from decimal import Decimal
import ipaddress


class Settings(BaseSettings):
    APP_ENV: str = "development"
    PUBLIC_WEB_URL: str = "http://localhost:3000"
    EMAIL_MODE: str = "disabled"
    EMAIL_FROM: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    RESEND_API_KEY: str = ""
    STAGING_PROXY_SECRET: str = ""
    REQUIRE_EMAIL_VERIFICATION: bool = False
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    DATABASE_URL: str
    DB_POOL_SIZE: int = Field(default=3, ge=1, le=20)
    DB_MAX_OVERFLOW: int = Field(default=1, ge=0, le=10)
    DB_POOL_TIMEOUT_SECONDS: int = Field(default=5, ge=1, le=30)
    DB_POOL_RECYCLE_SECONDS: int = Field(default=1800, ge=60, le=7200)
    ACCOUNT_DELETION_ENABLED: bool = False
    QURAN_EDITION_UZ: str = ""
    QURAN_ARABIC_EDITION: str = "quran-uthmani"
    QURAN_EDITION_EN: str = "en.itani"
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
    MAX_GLOBAL_AI_GENERATIONS: int = Field(default=10, ge=1, le=100)
    AI_DAILY_BUDGET_USD: Decimal = Field(default=Decimal("2"), ge=0, allow_inf_nan=False)
    AI_MONTHLY_BUDGET_USD: Decimal = Field(default=Decimal("20"), ge=0, allow_inf_nan=False)
    AI_INPUT_USD_PER_MILLION: Decimal = Field(default=Decimal("1"), gt=0, allow_inf_nan=False)
    AI_OUTPUT_USD_PER_MILLION: Decimal = Field(default=Decimal("2"), gt=0, allow_inf_nan=False)
    AI_MAX_PROVIDER_ATTEMPTS: int = Field(default=6, ge=1, le=20)
    AI_MAX_INPUT_TOKENS: int = Field(default=16384, ge=4096, le=32768)
    AI_PLANNER_OUTPUT_TOKENS: int = Field(default=2048, ge=256, le=4096)
    AI_MAX_OUTPUT_TOKENS: int = Field(default=4096, ge=256, le=8192)

    @field_validator("TRUSTED_PROXY_CIDRS")
    @classmethod
    def valid_proxy_networks(cls, value):
        networks = [ipaddress.ip_network(item, strict=False) for item in value]
        if any(network.prefixlen == 0 for network in networks):
            raise ValueError("Trust only explicitly identified proxy networks")
        return [str(network) for network in networks]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
