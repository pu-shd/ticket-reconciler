"""Drupal Remote Post ingest, and the manual Eventbrite sync trigger."""

from __future__ import annotations

import logging
from typing import Any

from eventkit.drupal import parse_submission
from eventkit.identity import IdentityError
from eventkit.identity import person_key as derive_person_key
from eventkit.realtime import ChangeOp, record_change
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..deps import ProfileDep, SessionDep, deps, require_principal, webhook_guard
from ..models import ChangeLog, Registrant
from ..schemas import SyncResultOut

logger = logging.getLogger("ticket_reconciler.webhook")

router = APIRouter()


def upsert_registrant(session: Session, submission: Any, profile: Any) -> tuple[Registrant | None, str]:
    """Create or update a registrant. Shared with the bulk importer."""
    try:
        key = derive_person_key(
            uuid=submission.get("uuid"), email=submission.email
        )
    except IdentityError:
        return None, "skipped"

    fields = {
        "email_address": submission.email,
        "first_name": submission.first_name,
        "last_name": submission.last_name,
        "drupal_uuid": submission.get("uuid"),
        "drupal_sid": submission.get("sid"),
        "serial_number": submission.get("serial"),
        "destination_url": submission.get("destination_url"),
        "swag_size": submission.get("t_shirt_size"),
    }
    # An exemption checkbox hidden by #states is absent from the payload, not
    # false. Absent reads as exempt, which is what the form intends.
    tickets = bool(submission.get("tickets_sold_separately"))

    existing = session.get(Registrant, key)
    if existing is None:
        registrant = Registrant(
            person_key=key, tickets_sold_separately=tickets, **fields
        )
        session.add(registrant)
        return registrant, "created"

    for name, value in fields.items():
        if value is not None:
            setattr(existing, name, value)
    existing.tickets_sold_separately = tickets
    existing.row_version += 1
    return existing, "updated"


@router.post(
    "/api/drupal-webhook",
    dependencies=[Depends(webhook_guard("registration"))],
    status_code=status.HTTP_200_OK,
)
async def drupal_webhook(request: Request, session: Session = SessionDep, profile=ProfileDep):
    d = deps(request)
    body = await request.json()
    submission = parse_submission(body, d.field_map)

    mapped = set(d.field_map.element_keys())
    payload_keys = set((body.get("data") or body).keys()) if isinstance(body, dict) else set()
    unmapped = sorted(payload_keys - mapped)
    d.counters.record(authenticated=True, unmapped=unmapped)

    registrant, outcome = upsert_registrant(session, submission, profile)
    if registrant is None:
        # 200 rather than an error: a Remote Post handler fires inside the
        # registrant's own request, and Drupal does not retry either way.
        logger.warning("submission carried no usable identity; ignored")
        return {"status": "ignored"}

    record_change(
        session,
        ChangeLog,
        entity="registrant",
        entity_id=registrant.person_key,
        op=ChangeOp.created if outcome == "created" else ChangeOp.updated,
    )
    session.commit()
    logger.info("webhook %s person_key=%s", outcome, registrant.person_key[:8])
    return {"status": outcome, "person_key": registrant.person_key}


@router.post(
    "/api/sync",
    response_model=SyncResultOut,
    dependencies=[Depends(require_principal)],
)
async def trigger_sync(request: Request, session: Session = SessionDep) -> SyncResultOut:
    """Pull the Eventbrite attendee list and reconcile it into payments."""
    d = deps(request)
    if not d.settings.eventbrite_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Eventbrite is not configured. Set EVENTBRITE_API_TOKEN and "
            "EVENTBRITE_EVENT_ID.",
        )

    from eventkit.eventbrite.client import EventbriteClient
    from eventkit.eventbrite.sync import run_sync

    from ..sync import SqlPorts

    client = EventbriteClient(
        d.settings.eventbrite_api_token, d.settings.eventbrite_event_id
    )
    ports = SqlPorts(session, notifier=d.notifier)
    result = await run_sync(client, ports)

    return SyncResultOut(
        status=result.status,
        records_pulled=result.records_pulled,
        payments_created=ports.created,
        payments_updated=ports.updated,
        error=result.error,
    )
