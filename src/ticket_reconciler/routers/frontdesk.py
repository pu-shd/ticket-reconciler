"""Front-desk operations: check-in, swag, waivers, exemptions, saved groups."""

from __future__ import annotations

from eventkit.realtime import ChangeOp, record_change
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import ProfileDep, SessionDep, require_principal
from ..models import ChangeLog, Registrant, SavedGroup, SwagInventory
from ..schemas import (
    CheckInPayload,
    SavedGroupPayload,
    SwagInventoryItem,
    SwagReplacementPayload,
    TogglePayload,
    WaivePayload,
)

router = APIRouter(dependencies=[Depends(require_principal)])


def _get(session: Session, person_key: str) -> Registrant:
    registrant = session.get(Registrant, person_key)
    if registrant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such registrant.")
    return registrant


def _touch(session: Session, registrant: Registrant, what: str) -> None:
    registrant.row_version += 1
    record_change(
        session,
        ChangeLog,
        entity="registrant",
        entity_id=registrant.person_key,
        op=ChangeOp.updated,
        payload={"field": what},
    )
    session.commit()


@router.post("/api/checkin")
def check_in(payload: CheckInPayload, session: Session = SessionDep, profile=ProfileDep):
    """Record one person's state for one day.

    Day keys are validated against the profile. The predecessor used bare
    ``"6/28"`` strings, which collide across events and are ambiguous to parse —
    both ``"7/1"`` and ``"07/01"`` appear in the live data.
    """
    valid = {d.key for d in profile.schedule.checkin_days}
    if payload.day_key not in valid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown check-in day {payload.day_key!r}. Configured days: {sorted(valid)}",
        )

    registrant = _get(session, payload.person_key)
    # Reassign rather than mutate: SQLAlchemy does not track in-place JSON edits.
    updated = dict(registrant.checkin_status or {})
    updated[payload.day_key] = payload.state
    registrant.checkin_status = updated
    _touch(session, registrant, "checkin")

    return {"person_key": registrant.person_key, "checkin_status": registrant.checkin_status}


@router.get("/api/swag/inventory", response_model=list[SwagInventoryItem])
def swag_inventory(session: Session = SessionDep) -> list[SwagInventory]:
    return list(session.execute(select(SwagInventory).order_by(SwagInventory.size)).scalars())


@router.post("/api/swag/inventory", response_model=list[SwagInventoryItem])
def set_swag_inventory(
    items: list[SwagInventoryItem], session: Session = SessionDep, profile=ProfileDep
):
    valid = {o.key for o in profile.swag.options}
    for item in items:
        if item.size not in valid:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown swag size {item.size!r}. Configured: {sorted(valid)}",
            )
        row = session.get(SwagInventory, item.size)
        if row is None:
            session.add(SwagInventory(size=item.size, total_count=item.total_count))
        else:
            row.total_count = item.total_count
    session.commit()
    return swag_inventory(session)


@router.post("/api/swag/checkin")
def issue_swag(payload: TogglePayload, session: Session = SessionDep):
    registrant = _get(session, payload.person_key)
    registrant.swag_checked_in = payload.value
    _touch(session, registrant, "swag_checked_in")
    return {"person_key": registrant.person_key, "swag_checked_in": registrant.swag_checked_in}


@router.patch("/api/registrants/{person_key}/swag-replacement")
def swap_swag_size(
    person_key: str,
    payload: SwagReplacementPayload,
    session: Session = SessionDep,
    profile=ProfileDep,
):
    """Somebody's shirt does not fit and they want a different one."""
    if payload.size is not None:
        valid = {o.key for o in profile.swag.options}
        if payload.size not in valid:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown swag size {payload.size!r}. Configured: {sorted(valid)}",
            )
    registrant = _get(session, person_key)
    registrant.replacement_swag_size = payload.size
    _touch(session, registrant, "replacement_swag_size")
    return {
        "person_key": registrant.person_key,
        "replacement_swag_size": registrant.replacement_swag_size,
    }


@router.post("/api/registrants/{person_key}/exemption")
def toggle_exemption(person_key: str, payload: TogglePayload, session: Session = SessionDep):
    registrant = _get(session, person_key)
    registrant.manually_exempt = payload.value
    _touch(session, registrant, "manually_exempt")
    return {"person_key": person_key, "manually_exempt": registrant.manually_exempt}


@router.post("/api/registrants/{person_key}/waive")
def waive(person_key: str, payload: WaivePayload, session: Session = SessionDep):
    """Waive a fee. The justification is required and is kept."""
    registrant = _get(session, person_key)
    registrant.waived = payload.waived
    registrant.waiver_justification = payload.justification if payload.waived else None
    _touch(session, registrant, "waived")
    return {
        "person_key": person_key,
        "waived": registrant.waived,
        "waiver_justification": registrant.waiver_justification,
    }


@router.post("/api/registrants/{person_key}/refund-override")
def refund_override(person_key: str, payload: TogglePayload, session: Session = SessionDep):
    """Mark a refunded or cancelled person as attending anyway."""
    registrant = _get(session, person_key)
    registrant.refund_override_attending = payload.value
    _touch(session, registrant, "refund_override_attending")
    return {
        "person_key": person_key,
        "refund_override_attending": registrant.refund_override_attending,
    }


@router.get("/api/groups")
def list_groups(session: Session = SessionDep):
    rows = session.execute(select(SavedGroup).order_by(SavedGroup.name)).scalars()
    return [{"name": g.name, "person_keys": g.person_keys} for g in rows]


@router.post("/api/groups")
def save_group(payload: SavedGroupPayload, session: Session = SessionDep):
    group = session.get(SavedGroup, payload.name)
    if group is None:
        session.add(SavedGroup(name=payload.name, person_keys=payload.person_keys))
    else:
        group.person_keys = payload.person_keys
    session.commit()
    return {"name": payload.name, "person_keys": payload.person_keys}


@router.delete("/api/groups/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(name: str, session: Session = SessionDep):
    group = session.get(SavedGroup, name)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such group.")
    session.delete(group)
    session.commit()
