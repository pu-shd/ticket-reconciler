"""Dashboard data: the reconciliation report, stats, manual linking, and export."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import ProfileDep, SessionDep, require_principal
from ..models import Payment, Registrant, SyncLog
from ..reconcile import DEFAULT_ORDER, build_report, build_stats
from ..schemas import DashboardStats, LinkPayload, ReportItem, UnlinkPayload
from ..ticketing import purchase_url_for

router = APIRouter(dependencies=[Depends(require_principal)])


def _load(session: Session):
    # Deterministic order so that, in the pathological case of two registrants
    # sharing an address, the same one wins the payment on every render.
    registrants = list(
        session.execute(
            select(Registrant).order_by(Registrant.person_key.asc())
        ).scalars()
    )
    payments = list(session.execute(select(Payment).order_by(Payment.id.asc())).scalars())
    return registrants, payments


def _status_order(profile) -> tuple[str, ...]:
    configured = getattr(profile.ticketing, "status_order", None)
    return tuple(configured) if configured else DEFAULT_ORDER


@router.get("/api/reports/registrations", response_model=list[ReportItem])
def registrations(
    request: Request, session: Session = SessionDep, profile=ProfileDep
) -> list[ReportItem]:
    registrants, payments = _load(session)
    rows = build_report(registrants, payments, status_order=_status_order(profile))

    out: list[ReportItem] = []
    for row in rows:
        item = ReportItem.model_validate(row, from_attributes=True)
        if row.status == "Pending" and row.email:
            item.purchase_url = purchase_url_for(profile, email=row.email)
        out.append(item)
    return out


@router.get("/api/reports/stats", response_model=DashboardStats)
def stats(session: Session = SessionDep, profile=ProfileDep) -> DashboardStats:
    registrants, payments = _load(session)
    s = build_stats(build_report(registrants, payments, status_order=_status_order(profile)))

    last = session.execute(
        select(SyncLog).order_by(SyncLog.synced_at.desc()).limit(1)
    ).scalar_one_or_none()

    return DashboardStats(
        registered=s.registered,
        complete=s.complete,
        pending=s.pending,
        unmatched=s.unmatched,
        exempt=s.exempt,
        gross_cents=s.gross_cents,
        net_cents=s.net_cents,
        last_sync_at=last.synced_at if last else None,
        last_sync_status=last.status if last else None,
    )


@router.get("/api/reports/export.csv", include_in_schema=False)
def export_csv(session: Session = SessionDep, profile=ProfileDep) -> Response:
    """Server-side export: the auditable full dump.

    CSV rather than xlsx so there is no binary dependency in the container; the
    dashboard's own export covers the filtered-rows case staff usually want.
    """
    registrants, payments = _load(session)
    rows = build_report(registrants, payments, status_order=_status_order(profile))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "First name", "Last name", "Email", "Serial", "Status",
            "Eventbrite order", "Paid at", "Gross (cents)", "Net (cents)",
            "Swag size", "Replacement size", "Swag issued",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.first_name or "", r.last_name or "", r.email or "",
                r.serial_number or "", r.status, r.eventbrite_order_id or "",
                r.paid_at.isoformat() if r.paid_at else "",
                r.gross_amount, r.net_amount,
                r.swag_size or "", r.replacement_swag_size or "",
                "yes" if r.swag_checked_in else "no",
            ]
        )

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="reconciliation.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/api/reports/link", response_model=ReportItem)
def link(payload: LinkPayload, session: Session = SessionDep, profile=ProfileDep):
    registrant = session.get(Registrant, payload.person_key)
    if registrant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such registrant.")
    if session.get(Payment, payload.payment_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such payment.")

    registrant.linked_payment_id = payload.payment_id
    registrant.row_version += 1
    session.commit()
    return _one(session, profile, payload.person_key)


@router.post("/api/reports/unlink", response_model=ReportItem)
def unlink(payload: UnlinkPayload, session: Session = SessionDep, profile=ProfileDep):
    registrant = session.get(Registrant, payload.person_key)
    if registrant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such registrant.")
    registrant.linked_payment_id = None
    registrant.row_version += 1
    session.commit()
    return _one(session, profile, payload.person_key)


def _one(session: Session, profile, person_key: str) -> ReportItem:
    registrants, payments = _load(session)
    rows = build_report(registrants, payments, status_order=_status_order(profile))
    for row in rows:
        if row.person_key == person_key:
            return ReportItem.model_validate(row, from_attributes=True)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "No such registrant.")
