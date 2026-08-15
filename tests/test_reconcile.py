"""The reconciliation truth table.

Pure-function tests, no database and no HTTP. This is the behaviour staff trust,
so it is pinned case by case rather than exercised incidentally through routes.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

import pytest

from ticket_reconciler.reconcile import (
    DEFAULT_ORDER,
    Status,
    build_report,
    build_stats,
    derive_status,
)

NOW = _dt.datetime(2030, 6, 1, 12, 0, tzinfo=_dt.UTC)


@dataclass
class R:
    """A registrant double. Structural typing, so no ORM needed."""

    person_key: str = "pk"
    email_address: str | None = "ada@example.edu"
    first_name: str | None = "Ada"
    last_name: str | None = "Lovelace"
    tickets_sold_separately: bool = True
    linked_payment_id: str | None = None
    manually_exempt: bool = False
    waived: bool = False
    waiver_justification: str | None = None
    refund_override_attending: bool = False
    checkin_status: dict = field(default_factory=dict)
    swag_size: str | None = None
    replacement_swag_size: str | None = None
    swag_checked_in: bool = False
    serial_number: int | None = 1
    registered_at: _dt.datetime = NOW


@dataclass
class P:
    id: str = "pay-1"
    email: str = "ada@example.edu"
    first_name: str | None = "Ada"
    last_name: str | None = "Lovelace"
    eventbrite_order_id: str | None = "order-1"
    status: str = "paid"
    paid_at: _dt.datetime = NOW
    gross_amount: int = 16000
    net_amount: int = 15000


class TestDerivedStatus:
    def test_paid_and_not_checked_in(self):
        assert derive_status(R(), P()) == Status.PAID

    def test_paid_and_checked_in_is_complete(self):
        assert derive_status(R(checkin_status={"2030-06-01": 1}), P()) == Status.COMPLETE

    def test_owes_a_ticket_with_no_payment_is_pending(self):
        assert derive_status(R(), None) == Status.PENDING

    def test_owes_nothing_with_no_payment_is_exempt(self):
        assert derive_status(R(tickets_sold_separately=False), None) == Status.EXEMPT

    def test_refunded(self):
        assert derive_status(R(), P(status="refunded")) == Status.REFUNDED

    def test_cancelled(self):
        assert derive_status(R(), P(status="cancelled")) == Status.CANCELLED

    def test_waived_beats_everything(self):
        assert derive_status(R(waived=True), P()) == Status.WAIVED
        assert derive_status(R(waived=True), None) == Status.WAIVED

    def test_manual_exemption_beats_a_payment(self):
        assert derive_status(R(manually_exempt=True), P()) == Status.MANUALLY_EXEMPT

    def test_waived_beats_manual_exemption(self):
        assert derive_status(R(waived=True, manually_exempt=True), None) == Status.WAIVED

    @pytest.mark.parametrize("reversed_state", ["refunded", "cancelled"])
    def test_refund_override_marks_them_present(self, reversed_state):
        """Someone who refunded but is standing at the desk. Set by staff, never inferred."""
        r = R(refund_override_attending=True)
        assert derive_status(r, P(status=reversed_state)) == Status.COMPLETE

    @pytest.mark.parametrize("state", [0, 2, 3])
    def test_only_state_1_counts_as_checked_in(self, state):
        assert derive_status(R(checkin_status={"2030-06-01": state}), P()) == Status.PAID

    def test_any_checked_in_day_counts(self):
        r = R(checkin_status={"2030-06-01": 3, "2030-06-02": 1})
        assert derive_status(r, P()) == Status.COMPLETE


class TestMatching:
    def test_matches_on_email_case_insensitively(self):
        rows = build_report([R(email_address="ADA@Example.edu")], [P()])
        assert rows[0].payment_id == "pay-1"

    def test_manual_link_wins_over_email(self):
        rows = build_report(
            [R(email_address="other@example.edu", linked_payment_id="pay-1")], [P()]
        )
        assert rows[0].payment_id == "pay-1"
        assert rows[0].manually_linked is True

    def test_a_manually_claimed_payment_is_not_also_email_matched(self):
        """The guard that matters: without it the payment is matched twice — once
        by the manual link and once by coincidence of address."""
        claimer = R(person_key="claimer", email_address="x@example.edu", linked_payment_id="pay-1")
        coincidence = R(person_key="other", email_address="ada@example.edu")
        rows = build_report([claimer, coincidence], [P()])

        by_key = {r.person_key: r for r in rows}
        assert by_key["claimer"].payment_id == "pay-1"
        assert by_key["other"].payment_id is None
        assert by_key["other"].status == Status.PENDING

    def test_a_manual_link_to_a_missing_payment_leaves_them_pending(self):
        rows = build_report([R(linked_payment_id="nope")], [])
        assert rows[0].payment_id is None
        assert rows[0].status == Status.PENDING

    def test_two_registrants_do_not_share_one_email_match(self):
        a = R(person_key="a", email_address="ada@example.edu")
        b = R(person_key="b", email_address="ada@example.edu")
        rows = build_report([a, b], [P()])
        matched = [r for r in rows if r.payment_id]
        assert len(matched) == 1


class TestUnmatchedPayments:
    def test_leftover_paid_payment_becomes_a_row(self):
        rows = build_report([], [P(email="stranger@example.edu")])
        assert len(rows) == 1
        assert rows[0].status == Status.UNMATCHED
        assert rows[0].person_key is None

    @pytest.mark.parametrize("state", ["refunded", "cancelled"])
    def test_leftover_reversed_payments_are_suppressed(self, state):
        """A reversed order with no registration is not actionable at the desk."""
        assert build_report([], [P(status=state)]) == []

    def test_a_matched_payment_is_not_also_unmatched(self):
        rows = build_report([R()], [P()])
        assert len(rows) == 1
        assert rows[0].status == Status.PAID


class TestOrdering:
    def test_sorted_by_status_then_name(self):
        rows = build_report(
            [
                R(person_key="p", last_name="Zeta", email_address="z@example.edu"),
                R(person_key="e", last_name="Alpha", email_address="a@example.edu",
                  tickets_sold_separately=False),
            ],
            [],
        )
        assert [r.status for r in rows] == [Status.PENDING, Status.EXEMPT]

    def test_names_break_ties_within_a_status(self):
        rows = build_report(
            [
                R(person_key="1", last_name="Zeta", email_address="z@example.edu"),
                R(person_key="2", last_name="Alpha", email_address="a@example.edu"),
            ],
            [],
        )
        assert [r.last_name for r in rows] == ["Alpha", "Zeta"]

    def test_unknown_status_sorts_last_rather_than_crashing(self):
        rows = build_report([R()], [], status_order=("Complete",))
        assert rows[0].status == Status.PENDING

    def test_default_order_covers_every_status(self):
        assert set(DEFAULT_ORDER) == {s.value for s in Status}


class TestStats:
    def test_counts(self):
        rows = build_report(
            [
                R(person_key="a", email_address="a@example.edu"),
                R(person_key="b", email_address="b@example.edu",
                  checkin_status={"2030-06-01": 1}),
                R(person_key="c", email_address="c@example.edu",
                  tickets_sold_separately=False),
            ],
            [P(id="p-b", email="b@example.edu"), P(id="p-x", email="x@example.edu")],
        )
        s = build_stats(rows)
        assert s.registered == 3
        assert s.complete == 1
        assert s.pending == 1
        assert s.exempt == 1
        assert s.unmatched == 1

    def test_revenue_counts_only_money_collected(self):
        rows = build_report(
            [R(person_key="r", email_address="r@example.edu")],
            [
                P(id="p1", email="r@example.edu", gross_amount=10000, net_amount=9000),
                P(id="p2", email="ref@example.edu", status="refunded",
                  gross_amount=99999, net_amount=99999),
            ],
        )
        s = build_stats(rows)
        assert s.gross_cents == 10000
        assert s.net_cents == 9000

    def test_money_is_integer_cents(self):
        s = build_stats(build_report([R()], [P()]))
        assert isinstance(s.gross_cents, int)
