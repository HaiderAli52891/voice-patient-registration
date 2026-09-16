"""Application configuration.

Every secret is read from the environment. Nothing is hardcoded — copy
`.env.example` to `.env` for local development, or set the same keys in your
hosting provider's dashboard (Railway / Render / Fly).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Core -------------------------------------------------------------
    app_name: str = "Voice AI Patient Registration"
    environment: str = "development"
    log_level: str = "INFO"

    # Public base URL of this service, e.g. https://your-app.railway.app
    # Used to build Twilio webhook callbacks and to let the voice agent call
    # its own REST API.
    public_base_url: str = "http://localhost:8000"

    # --- Database ---------------------------------------------------------
    # SQLite by default so the project runs with zero external services.
    # Point DATABASE_URL at Postgres in production, e.g.
    #   postgresql+psycopg2://user:pass@host:5432/dbname
    database_url: str = "sqlite:///./data/patients.db"

    # --- LLM --------------------------------------------------------------
        
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""

    # --- Telephony (Twilio) ----------------------------------------------
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    # Twilio <Say> voice. "Polly.Joanna-Neural" sounds markedly more human
    # than the default and costs nothing extra.
    twilio_voice: str = "Polly.Joanna-Neural"
    # Verify that inbound webhooks really come from Twilio. Turn off only
    # when replaying requests locally with curl.
    validate_twilio_signature: bool = False

    # --- Vapi / Retell (optional alternative front-end) -------------------
    # Shared secret checked on the Vapi tool webhook.
    vapi_secret: str = ""

    # --- Dashboard --------------------------------------------------------
    # Optional HTTP Basic credentials for /dashboard. Leave blank to make the
    # dashboard public (fine for a demo, not for real PHI).
    dashboard_user: str = ""
    dashboard_password: str = ""

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
