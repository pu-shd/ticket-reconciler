"""The reconciliation engine.

A **pure function**: lists in, rows out. No ``Session``, no settings, no clock.
That is the point — in the predecessor this was 130 lines inside a route handler,
which made it the most valuable and least testable code in the stack. Staff trust
these statuses, so the semantics here are a faithful extraction, and the truth
table in ``tests/test_reconcile.py`` pins them.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class Status(StrEnum):
    """Derived reconciliation status, most-urgent first in :data:`DEFAULT_ORDER`."""

    UNMATCHED = "Unmatched"
    PENDING = "Pending"
    PAID = "Paid"
    COMPLETE = "Complete"
    WAIVED = "Waived"
    EXEMPT = "Exempt"
    MANUALLY_EXEMPT = "Manually Set to Exempt"
    REFUNDED = "Refunded"
    CANCELLED = "Cancelled"


#: Sort order for the dashboard. Overridable from ``profile.ticketing.status_order``
#: — the predecessor hardcoded a dict here.
DEFAULT_ORDER: tuple[str, ...] = (
    Status.UNMATCHED,
    Status.PENDING,
    Status.PAID,
    Status.COMPLETE,
    Status.WAIVED,
    Status.EXEMPT,
    Status.MANUALLY_EXEMPT,
    Status.REFUNDED,
    Status.CANCELLED,
)


class RegistrantLike(Protocol):
    person_key: str
    email_address: str | None
    first_name: str | None
    last_name: str | None
    tickets_sold_separately: bool
    linked_payment_id: str | None
    manually_exempt: bool
    waived: bool
    waiver_justification: str | None
    refund_override_attending: bool
    checkin_status: dict
    swag_size: str | None
    replacement_swag_size: str | None
    swag_checked_in: bool
    serial_number: int | None
    registered_at: Any


class PaymentLike(Protocol):
    id: str
    email: str
    first_name: str | None
    last_name: str | None
    eventbrite_order_id: str | None
    status: str
    paid_at: Any
    gross_amount: int
    net_amount: int


@dataclass(frozen=True, slots=True)
class ReportRow:
    person_key: str | None
    first_name: str | None
    last_name: str | None
    email: str | None
    status: str
    serial_number: int | None = None
    payment_id: str | None = None
    eventbrite_order_id: str | None = None
    paid_at: _dt.datetime | None = None
    gross_amount: int = 0
    net_amount: int = 0
    manually_linked: bool = False
    waiver_justification: str | None = None
    checkin_status: dict = field(default_factory=dict)
    swag_size: str | None = None
    replacement_swag_size: str | None = None
    swag_checked_in: bool = False
    registered_at: Any = None

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip()


@dataclass(frozen=True, slots=True)
class Stats:
    registered: int = 0
    complete: int = 0
    pending: int = 0
    unmatched: int = 0
    exempt: int = 0
    gross_cents: int = 0
    net_cents: int = 0


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _is_checked_in(checkin_status: dict | None) -> bool:
    """Any day recorded as state 1 counts as having attended."""
    if not checkin_status:
        return False
    return any(state == 1 for state in checkin_status.values())


def build_report(
    registrants: list[RegistrantLike],
    payments: list[PaymentLike],
    *,
    status_order: tuple[str, ...] | list[str] = DEFAULT_ORDER,
) -> list[ReportRow]:
    """Join registrations to payments and derive a status for each.

    Matching, in order:

    1. ``linked_payment_id`` — an explicit manual link, set by staff.
    2. An email match, **unless** that payment has already been claimed — either
       by somebody else's manual link, or by an earlier registrant with the same
       address. Without those guards one payment can be matched twice, which
       doubles the revenue figure and shows two people as Paid for one ticket.

    Any ``paid`` payment left over becomes an ``Unmatched`` row: someone bought a
    ticket with an address they did not register with. Left-over refunded or
    cancelled payments are dropped — a reversed order with no registration is
    not something the front desk can act on.
    """
    by_id: dict[str, PaymentLike] = {p.id: p for p in payments}

    by_email: dict[str, PaymentLike] = {}
    for p in payments:
        key = _norm(p.email)
        if key:
            by_email.setdefault(key, p)

    manually_claimed = {
        r.linked_payment_id for r in registrants if r.linked_payment_id
    }

    rows: list[ReportRow] = []
    consumed: set[str] = set()

    for r in registrants:
        payment: PaymentLike | None = None

        if r.linked_payment_id:
            payment = by_id.get(r.linked_payment_id)
        else:
            candidate = by_email.get(_norm(r.email_address))
            # Do not steal a payment another registrant has explicitly claimed,
            # and do not hand the same payment to two people. The webform makes
            # the email unique so this should not arise, but when it does the
            # cost of getting it wrong is a doubled revenue figure and two
            # people shown as Paid for one ticket. First in input order wins;
            # pass registrants in a deterministic order.
            if (
                candidate is not None
                and candidate.id not in manually_claimed
                and candidate.id not in consumed
            ):
                payment = candidate

        if payment is not None:
            consumed.add(payment.id)

        rows.append(
            ReportRow(
                person_key=r.person_key,
                first_name=r.first_name,
                last_name=r.last_name,
                email=r.email_address,
                serial_number=r.serial_number,
                status=derive_status(r, payment),
                payment_id=payment.id if payment else None,
                eventbrite_order_id=payment.eventbrite_order_id if payment else None,
                paid_at=payment.paid_at if payment else None,
                gross_amount=payment.gross_amount if payment else 0,
                net_amount=payment.net_amount if payment else 0,
                manually_linked=bool(r.linked_payment_id),
                waiver_justification=r.waiver_justification,
                checkin_status=dict(r.checkin_status or {}),
                swag_size=r.swag_size,
                replacement_swag_size=r.replacement_swag_size,
                swag_checked_in=r.swag_checked_in,
                registered_at=r.registered_at,
            )
        )

    for p in payments:
        if p.id in consumed or _norm(p.status) != "paid":
            continue
        rows.append(
            ReportRow(
                person_key=None,
                first_name=p.first_name,
                last_name=p.last_name,
                email=p.email,
                status=Status.UNMATCHED,
                payment_id=p.id,
                eventbrite_order_id=p.eventbrite_order_id,
                paid_at=p.paid_at,
                gross_amount=p.gross_amount,
                net_amount=p.net_amount,
            )
        )

    order = {name: i for i, name in enumerate(status_order)}
    rows.sort(
        key=lambda row: (
            order.get(row.status, len(order)),
            (row.last_name or "").lower(),
            (row.first_name or "").lower(),
        )
    )
    return rows


def derive_status(r: RegistrantLike, payment: PaymentLike | None) -> str:
    """Status for one registrant. Order of precedence is deliberate."""
    # A recorded human decision beats anything derived.
    if r.waived:
        return Status.WAIVED
    if r.manually_exempt:
        return Status.MANUALLY_EXEMPT

    if payment is not None:
        state = _norm(payment.status)
        if state == "refunded":
            # Someone who refunded but is standing at the desk. Staff set this
            # explicitly; it is not inferred.
            return Status.COMPLETE if r.refund_override_attending else Status.REFUNDED
        if state == "cancelled":
            return Status.COMPLETE if r.refund_override_attending else Status.CANCELLED
        if state == "paid":
            return Status.COMPLETE if _is_checked_in(r.checkin_status) else Status.PAID

    # No payment. Do they owe one?
    if r.tickets_sold_separately:
        return Status.PENDING
    return Status.EXEMPT


def build_stats(rows: list[ReportRow]) -> Stats:
    """Dashboard tiles. Revenue counts only money actually collected."""
    counted = {Status.PAID, Status.COMPLETE, Status.UNMATCHED}
    return Stats(
        registered=sum(1 for r in rows if r.person_key is not None),
        complete=sum(1 for r in rows if r.status == Status.COMPLETE),
        pending=sum(1 for r in rows if r.status == Status.PENDING),
        unmatched=sum(1 for r in rows if r.status == Status.UNMATCHED),
        exempt=sum(
            1 for r in rows if r.status in (Status.EXEMPT, Status.MANUALLY_EXEMPT)
        ),
        gross_cents=sum(r.gross_amount for r in rows if r.status in counted),
        net_cents=sum(r.net_amount for r in rows if r.status in counted),
    )
