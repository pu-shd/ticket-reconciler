"""Database models."""

from __future__ import annotations

import datetime as _dt

from eventkit.db import declarative_base
from eventkit.identity import IdentityMixin
from eventkit.realtime import ChangeLogMixin
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = declarative_base()


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


class Registrant(IdentityMixin, Base):
    """Someone who filled in the registration webform."""

    __tablename__ = "registrants"

    #: False means "this person owes a ticket". An absent or unchecked exemption
    #: box reads as exempt, matching how the form hides it from speakers.
    tickets_sold_separately: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    registered_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    #: A manual override linking this person to a specific payment. Wins over an
    #: email match, and a payment claimed this way is never also matched to
    #: somebody else.
    linked_payment_id: Mapped[str | None] = mapped_column(String(64), default=None)
    #: Carries a tier slug computed by the webform's Twig.
    destination_url: Mapped[str | None] = mapped_column(String(512), default=None)
    resolved_tier: Mapped[str | None] = mapped_column(String(64), default=None)

    #: {ISO day key: state}. Keys are ISO dates, never "6/28": year-less keys
    #: collide across events and both "7/1" and "07/01" appeared in live data.
    checkin_status: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    manually_exempt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    waived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    waiver_justification: Mapped[str | None] = mapped_column(Text, default=None)
    refund_override_attending: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    #: Swag lives here and nowhere else. Two applications counting shirts
    #: independently is how you oversell mediums.
    swag_size: Mapped[str | None] = mapped_column(String(16), default=None)
    replacement_swag_size: Mapped[str | None] = mapped_column(String(16), default=None)
    swag_checked_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Payment(Base):
    """An Eventbrite order, aggregated per email by ``eventkit.eventbrite``."""

    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Indexed but NOT unique. One purchaser buying two tickets is ordinary; the
    #: predecessor's unique constraint made it a 500, and the workaround was to
    #: aggregate before insert rather than to drop the constraint.
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(255), default=None)
    last_name: Mapped[str | None] = mapped_column(String(255), default=None)
    eventbrite_order_id: Mapped[str | None] = mapped_column(String(64), default=None)
    eventbrite_attendee_id: Mapped[str | None] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(32), default="paid", nullable=False)
    paid_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    #: Cents. Never floats for money.
    gross_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    net_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    synced_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    records_pulled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)


class SavedGroup(Base):
    """A named set of people, for filtering the dashboard at the front desk."""

    __tablename__ = "saved_groups"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    person_keys: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class SwagInventory(Base):
    __tablename__ = "swag_inventory"

    size: Mapped[str] = mapped_column(String(16), primary_key=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ChangeLog(ChangeLogMixin, Base):
    __tablename__ = "change_log"
