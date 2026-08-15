"""Eventbrite sync.

``eventkit.eventbrite`` owns the hard part — paging the API and aggregating
attendees per email, where a paid record beats a refunded one and multiple paid
records sum. This module is only the port: how those results reach *this*
application's tables.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any

from eventkit.eventbrite import AggregatedPayment
from eventkit.eventbrite.sync import SyncEvent, SyncResult
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Payment, Registrant, SyncLog

logger = logging.getLogger("ticket_reconciler.sync")


class SqlPorts:
    """Implements ``eventkit.eventbrite.sync.SyncPorts`` against our models."""

    def __init__(self, session: Session, *, notifier: Any = None) -> None:
        self.session = session
        self.notifier = notifier
        self.created = 0
        self.updated = 0

    def load_existing_payments(self) -> dict[str, Payment]:
        rows = self.session.execute(select(Payment)).scalars()
        return {p.email.strip().lower(): p for p in rows if p.email}

    def load_registrant_index(self) -> dict[str, Registrant]:
        rows = self.session.execute(select(Registrant)).scalars()
        return {
            r.email_address.strip().lower(): r for r in rows if r.email_address
        }

    def upsert_payment(self, agg: AggregatedPayment) -> tuple[Payment, bool]:
        key = agg.email.strip().lower()
        existing = self.session.execute(
            select(Payment).where(Payment.email == key)
        ).scalars().first()

        if existing is None:
            payment = Payment(
                id=agg.attendee_id or agg.order_id or key,
                email=key,
                first_name=agg.first_name,
                last_name=agg.last_name,
                eventbrite_order_id=agg.order_id,
                eventbrite_attendee_id=agg.attendee_id,
                status=str(agg.status),
                paid_at=agg.paid_at,
                gross_amount=agg.gross_cents,
                net_amount=agg.net_cents,
            )
            self.session.add(payment)
            self.created += 1
            return payment, True

        existing.first_name = agg.first_name or existing.first_name
        existing.last_name = agg.last_name or existing.last_name
        existing.eventbrite_order_id = agg.order_id or existing.eventbrite_order_id
        existing.eventbrite_attendee_id = agg.attendee_id or existing.eventbrite_attendee_id
        existing.status = str(agg.status)
        existing.paid_at = agg.paid_at or existing.paid_at
        existing.gross_amount = agg.gross_cents
        existing.net_amount = agg.net_cents
        self.updated += 1
        return existing, False

    def record_sync(self, result: SyncResult) -> None:
        self.session.add(
            SyncLog(
                synced_at=_dt.datetime.now(_dt.UTC),
                records_pulled=result.records_pulled,
                status=result.status,
                error_message=result.error,
            )
        )
        self.session.commit()

    async def emit(self, event: SyncEvent, ctx: dict) -> None:
        """Fire a notification, without letting it break the sync.

        A notifier failure must not roll back a successful reconciliation: the
        payment data is the valuable part, the email is a convenience.
        """
        if self.notifier is None:
            return
        try:
            await self.notifier.notify(str(event), ctx)
        except Exception:  # noqa: BLE001 - deliberately swallowed, logged below
            logger.exception("notification for %s failed; sync continues", event)
