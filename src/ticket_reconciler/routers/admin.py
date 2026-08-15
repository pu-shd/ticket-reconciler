"""Admin surfaces: the dashboard page, webhook status, and the destructive clear."""

from __future__ import annotations

import logging

from eventkit.auth import Principal
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..deps import ProfileDep, SessionDep, deps, require_principal
from ..models import Payment, Registrant
from ..schemas import ClearPayload, WebhookStatus
from ..templating import render_page

logger = logging.getLogger("ticket_reconciler.admin")

router = APIRouter(dependencies=[Depends(require_principal)])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(profile=ProfileDep) -> HTMLResponse:
    return HTMLResponse(render_page("index.html", profile))


@router.get("/api/webhook/status", response_model=WebhookStatus)
def webhook_status(request: Request) -> WebhookStatus:
    c = deps(request).counters
    return WebhookStatus(
        received_total=c.received_total,
        authenticated_total=c.authenticated_total,
        rejected_total=c.rejected_total,
        last_received_at=c.last_received_at,
        unmapped_keys=sorted(c.unmapped_keys),
    )


@router.post("/api/admin/clear")
def clear_database(
    payload: ClearPayload,
    request: Request,
    session: Session = SessionDep,
    user: Principal = Depends(require_principal),
):
    """Delete registrations, payments, or both.

    The predecessor's equivalent had **no authentication dependency at all** —
    the only gate was a confirmation phrase published in its own repository, so
    any anonymous caller who knew the hostname could destroy the database. It was
    open because a CI workflow curled it with no header.

    Here it needs an allow-listed principal *and* an explicit opt-in flag, so even
    a compromised admin session cannot wipe the database unless an operator has
    deliberately enabled destructive operations for that window. There is no CI
    path to it.
    """
    settings = deps(request).settings
    if not settings.enable_destructive_ops:
        logger.error("clear refused for %s: ENABLE_DESTRUCTIVE_OPS is off", user.email)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Destructive operations are disabled. Set ENABLE_DESTRUCTIVE_OPS=True, "
            "perform the operation, then set it back to False.",
        )

    if payload.confirm != "DESTROY":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid confirmation phrase. Must be 'DESTROY'."
        )

    deleted_registrants = deleted_payments = 0
    if payload.target in ("registrations", "both"):
        deleted_registrants = session.query(Registrant).delete()
    if payload.target in ("payments", "both"):
        deleted_payments = session.query(Payment).delete()
    session.commit()

    logger.warning(
        "DESTRUCTIVE: clear by %s target=%s registrants=%d payments=%d",
        user.email,
        payload.target,
        deleted_registrants,
        deleted_payments,
    )
    return {
        "status": "success",
        "target": payload.target,
        "deleted_registrants": deleted_registrants,
        "deleted_payments": deleted_payments,
    }
