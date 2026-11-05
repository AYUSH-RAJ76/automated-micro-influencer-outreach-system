from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    database_url: str = "sqlite:///./data/outreach.db"
    youtube_api_key: str = ""
    youtube_connect_timeout: float = 5.0
    youtube_read_timeout: float = 20.0
    youtube_write_timeout: float = 10.0
    youtube_pool_timeout: float = 5.0
    youtube_max_retries: int = 3
    youtube_retry_backoff_seconds: float = 0.5
    youtube_search_query_limit: int = 3
    youtube_cache_ttl_seconds: int = 1800
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_request_timeout_seconds: float = 60.0
    groq_max_retries: int = 2
    groq_retry_backoff_seconds: float = 1.0
    brand_name: str = "DevFlow AI"
    min_subscribers: int = 5000
    max_subscribers: int = 100000
    engagement_threshold: float = 1.0
    recent_days: int = 180
    dry_run: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    send_limit: int = 20
    send_delay_seconds: float = 1.0


@lru_cache
def get_settings() -> Settings:
    # Streamlit Community Cloud stores app credentials in st.secrets rather
    # than automatically exporting them as environment variables. Read those
    # values when running under Streamlit, while keeping local .env and normal
    # environment configuration available for the API and development runs.
    secret_values = {}
    try:
        import streamlit as st

        for field_name in Settings.model_fields:
            secret_key = field_name.upper()
            if secret_key in st.secrets:
                secret_values[field_name] = st.secrets[secret_key]
            elif field_name in st.secrets:
                secret_values[field_name] = st.secrets[field_name]
    except Exception:
        # Streamlit is optional for API-only usage, and st.secrets raises when
        # no local secrets.toml is configured. Fall back to env/.env settings.
        pass
    return Settings(**secret_values)
