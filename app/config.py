from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Settings are read from environment variables, with safe local defaults where possible.
    app_env: str = "local"
    app_name: str = "UW Seat Watch"
    debug: bool = Field(default=False, validation_alias="APP_DEBUG")
    log_level: str = "INFO"

    # SECRET_KEY is allowed to be a placeholder locally, but production must override it.
    secret_key: str = "local-dev-only-change-me"

    base_url: str = "http://localhost:8000"
    contact_email: str = "arothe995@gmail.com"

    database_url: str = "sqlite:///./uw_seat_watch.db"
    
    waterloo_max_concurrent_requests: int = Field(default=2, ge=1, le=5) #max requests to UW_SERVER

    email_backend: str = "console"
    brevo_api_key: str | None = None
    from_email: str = "UW Seat Watch <alerts@example.com>"

    waterloo_schedule_url: str = (
        "https://classes.uwaterloo.ca/cgi-bin/cgiwrap/infocour/salook.pl"
    )
    waterloo_openapi_base_url: str = "https://openapi.data.uwaterloo.ca/v3"
    uw_openapi_key: str | None = None
    waterloo_request_timeout_seconds: float = Field(default=15.0, gt=0)

    scheduler_enabled: bool = False
    poll_minutes: str = "1,31"
    poll_start_hour: int = Field(default=8, ge=0, le=23)
    poll_end_hour: int = Field(default=20, ge=0, le=23)
    timezone: str = "America/Toronto"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def require_real_secret_in_production(self) -> "Settings":
        if self.app_env.lower() == "production" and self.secret_key == "local-dev-only-change-me":
            raise ValueError("SECRET_KEY must be set to a real secret in production.")
        return self


@lru_cache
def get_settings() -> Settings:
    # Cache settings so every import does not re-read .env and environment variables.
    return Settings()
    
