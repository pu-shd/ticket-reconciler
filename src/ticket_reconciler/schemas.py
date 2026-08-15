"""Request and response shapes."""

from __future__ import annotations

import datetime as _dt

from eventkit.drupal import DrupalSubmissionModel
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RegistrationSubmission(DrupalSubmissionModel):
    """A Drupal Remote Post payload, normalised by ``eventkit.drupal``."""

    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    uuid: str | None = None
    sid: int | None = None
    serial: int | None = None
    tickets_sold_separately: bool = False
    destination_url: str | None = None
    t_shirt_size: str | None = None
    attendee_status: str | None = None


class ReportItem(BaseModel):
    """One dashboard row."""

    model_config = ConfigDict(from_attributes=True)

    person_key: str | None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    status: str
    serial_number: int | None = None
    payment_id: str | None = None
    eventbrite_order_id: str | None = None
    paid_at: _dt.datetime | None = None
    gross_amount: int = 0
    net_amount: int = 0
    manually_linked: bool = False
    waiver_justification: str | None = None
    checkin_status: dict = {}
    swag_size: str | None = None
    replacement_swag_size: str | None = None
    swag_checked_in: bool = False
    registered_at: _dt.datetime | None = None
    purchase_url: str | None = None


class DashboardStats(BaseModel):
    registered: int
    complete: int
    pending: int
    unmatched: int
    exempt: int
    gross_cents: int
    net_cents: int
    last_sync_at: _dt.datetime | None = None
    last_sync_status: str | None = None


class LinkPayload(BaseModel):
    person_key: str
    payment_id: str


class UnlinkPayload(BaseModel):
    person_key: str


class CheckInPayload(BaseModel):
    person_key: str
    day_key: str
    #: 0 unrecorded, 1 checked in, 2 unsure, 3 absent.
    state: int = Field(ge=0, le=3)


class TogglePayload(BaseModel):
    person_key: str
    value: bool


class WaivePayload(BaseModel):
    person_key: str
    waived: bool
    justification: str | None = None

    @model_validator(mode="after")
    def _needs_a_reason(self) -> WaivePayload:
        """A waiver is a financial decision, so it carries an explanation."""
        if self.waived and not (self.justification or "").strip():
            raise ValueError("A justification is required when waiving a fee.")
        return self


class SwagInventoryItem(BaseModel):
    size: str
    total_count: int = Field(ge=0)


class SwagReplacementPayload(BaseModel):
    size: str | None = None


class SavedGroupPayload(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    person_keys: list[str] = []

    @field_validator("name")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()


class ClearPayload(BaseModel):
    target: str = Field(pattern="^(registrations|payments|both)$")
    confirm: str


class SyncResultOut(BaseModel):
    status: str
    records_pulled: int = 0
    payments_created: int = 0
    payments_updated: int = 0
    error: str | None = None


class WebhookStatus(BaseModel):
    received_total: int
    authenticated_total: int
    rejected_total: int
    last_received_at: _dt.datetime | None = None
    unmapped_keys: list[str] = []
