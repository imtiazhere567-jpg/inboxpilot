"""Central settings. Every external credential is read from the environment (or a local .env file).

Nothing here is ever committed with a real value — see .env.example for the full list.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- app ---------------------------------------------------------------
    app_env: Literal["dev", "prod", "test"] = "dev"
    app_base_url: str = "http://localhost:8000"
    demo_password: str = Field(default="", description="Shared demo login; empty disables the login gate (dev only)")
    reset_token: str = Field(default="", description="Bearer token for POST /reset and POST /inject")
    log_level: str = "INFO"

    # --- database -----------------------------------------------------------
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ops_agent"

    # --- Claude -------------------------------------------------------------
    anthropic_api_key: str = ""
    claude_model_main: str = "claude-sonnet-5"                  # classify / extract / draft
    claude_model_verify: str = "claude-haiku-4-5-20251001"      # verifier pass
    llm_fake: bool = Field(default=False, description="Use the deterministic fake LLM (tests / no key)")

    # --- Gmail (IMAP/SMTP with an app password) ------------------------------
    gmail_address: str = ""
    gmail_app_password: str = ""
    gmail_imap_host: str = "imap.gmail.com"
    gmail_smtp_host: str = "smtp.gmail.com"
    gmail_smtp_port: int = 587
    gmail_label_inbox: str = "ops-agent/inbox"
    gmail_label_processed: str = "ops-agent/processed"

    # --- Rules (Google Sheet) -------------------------------------------------
    google_service_account_json: str = Field(default="", description="Path to the service-account JSON file")
    rules_sheet_id: str = ""
    rules_sheet_tab: str = "rules"
    rules_cache_seconds: int = 60

    # --- HubSpot (developer test account, private app token) --------------------
    hubspot_access_token: str = ""

    # --- QuickBooks Online (sandbox) -------------------------------------------
    qbo_client_id: str = ""
    qbo_client_secret: str = ""
    qbo_refresh_token: str = ""
    qbo_realm_id: str = ""
    qbo_environment: Literal["sandbox", "production"] = "sandbox"

    # --- Slack ------------------------------------------------------------------
    slack_bot_token: str = ""
    slack_channel: str = "#ops-agent"

    # --- scheduler ----------------------------------------------------------------
    intake_poll_seconds: int = 30
    reset_interval_minutes: int = 60
    reset_idle_minutes: int = 15
    scheduler_enabled: bool = True

    @property
    def integrations_configured(self) -> dict[str, bool]:
        """Which external systems have credentials. Used by /health and the under-the-hood page."""
        return {
            "anthropic": bool(self.anthropic_api_key) or self.llm_fake,
            "gmail": bool(self.gmail_address and self.gmail_app_password),
            "rules_sheet": bool(self.google_service_account_json and self.rules_sheet_id),
            "hubspot": bool(self.hubspot_access_token),
            "qbo": bool(self.qbo_client_id and self.qbo_client_secret and self.qbo_refresh_token and self.qbo_realm_id),
            "slack": bool(self.slack_bot_token),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
