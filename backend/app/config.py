from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = "123456"
    db_name: str = "crm_os"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    secret_key: str = "your-secret-key-minimum-32-characters-long-here"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # App
    app_env: str = "development"
    cors_origins: str = "http://localhost:5173"
    # Commit the image was built from (baked in by CI); reported by /api/health
    git_sha: str = "unknown"

    # WhatsApp
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_webhook_verify_token: str = ""
    whatsapp_app_secret: str = ""

    # IMAP
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_poll_interval_seconds: int = 60

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # AutoCount Cloud Accounting integration
    autocount_api_base_url: str = "https://accounting-api.autocountcloud.com"
    autocount_account_book_id: str = ""
    autocount_key_id: str = ""
    autocount_api_key: str = ""
    autocount_poll_interval_seconds: int = 60

    # Shared WhatsApp demo gateway (whatsapp_gateway)
    internal_shared_secret: str = ""
    whatsapp_gateway_base_url: str = ""

    # ai_chatbot_demo -> CRM conversation events (docs/chatbot-events.md section 4).
    # Same value as CRM_EVENTS_SECRET on the chatbot side; _previous holds the old
    # key while rotating. Both empty: POST /internal/chatbot/events answers 503.
    chatbot_events_secret: str = ""
    chatbot_events_secret_previous: str = ""

    # CRM -> ai_chatbot_demo replies. CHATBOT_SEND_SECRET has the same value
    # as CRM_SEND_SECRET on the chatbot side and must differ from
    # CHATBOT_EVENTS_SECRET. The URL is an internal base URL including a port,
    # without an endpoint path.
    chatbot_send_url: str = ""
    chatbot_send_secret: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()
