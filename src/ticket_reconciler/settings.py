"""Application settings. Lazy, so ``create_app()`` has no import-time side effects."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "sqlite:///./data/ticket-reconciler.db"

    #: No default. A placeholder default ships a publicly-known shared secret.
    drupal_webhook_token: SecretStr

    #: Empty means DENY ALL, not allow all.
    authorized_principals: str = ""

    #: Both destructive switches are opt-in.
    enable_restore: bool = False
    #: Gates the clear endpoint. The predecessor's equivalent had no auth at all.
    enable_destructive_ops: bool = False

    event_profile: str | None = None

    # Eventbrite
    eventbrite_api_token: SecretStr | None = None
    eventbrite_event_id: str | None = None
    enable_auto_sync: bool = False
    auto_sync_interval_minutes: int = Field(default=60, ge=3, le=1440)

    # Notifications. Log transport by default, so a missing credential can never
    # block a deploy.
    notify_transport: str = "log"
    notification_recipient_email: str | None = None

    allow_local_dev_admin: bool = False
    local_dev_admin_principal: str | None = None

    @model_validator(mode="after")
    def _check_token(self) -> Settings:
        from eventkit.webhook import assert_strong

        assert_strong(self.drupal_webhook_token, name="DRUPAL_WEBHOOK_TOKEN")
        return self

    @property
    def dev_principal(self) -> str | None:
        return self.local_dev_admin_principal if self.allow_local_dev_admin else None

    @property
    def eventbrite_configured(self) -> bool:
        return bool(self.eventbrite_api_token and self.eventbrite_event_id)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
