"""Wiring shared by the routers.

Held on ``app.state`` rather than in module globals so that ``create_app()`` can be
called more than once in a single process — which is what makes the test suite fast
and is impossible in either predecessor.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import Depends, Request

if TYPE_CHECKING:  # pragma: no cover - typing only
    from eventkit.auth import EasyAuth, Principal
    from eventkit.db import Database
    from eventkit.eventprofile import EventProfile
    from eventkit.webhook import WebhookTokens
    from sqlalchemy.orm import Session

    from .settings import Settings


@dataclass
class WebhookCounters:
    """Operational counters for ``/api/webhook/status``.

    In-process, so a restart resets them. That is acceptable for what they are
    used for — confirming a Remote Post handler is wired up, and noticing a
    renamed Drupal element — and it keeps a hot path free of database writes.
    """

    received_total: int = 0
    authenticated_total: int = 0
    rejected_total: int = 0
    last_received_at: _dt.datetime | None = None
    unmapped_keys: set[str] = field(default_factory=set)

    def record(self, *, authenticated: bool, unmapped: list[str] | None = None) -> None:
        self.received_total += 1
        self.last_received_at = _dt.datetime.now(_dt.UTC)
        if authenticated:
            self.authenticated_total += 1
        else:
            self.rejected_total += 1
        if unmapped:
            self.unmapped_keys.update(unmapped)


@dataclass
class AppDeps:
    settings: Settings
    database: Database
    profile: EventProfile
    auth: EasyAuth
    tokens: WebhookTokens
    field_map: Any
    counters: WebhookCounters = field(default_factory=WebhookCounters)


def deps(request: Request) -> AppDeps:
    return request.app.state.deps


def get_db(request: Request):
    """Yield a session. Overridable in tests via ``dependency_overrides``."""
    database = request.app.state.deps.database
    with database.session() as session:
        yield session


def get_profile(request: Request) -> EventProfile:
    return request.app.state.deps.profile


def require_principal(request: Request) -> Principal:
    """Authenticated, allow-listed principal, or raise.

    A dependency rather than an imperative call at the top of each handler: the
    predecessor used the imperative form in eighteen places, where a new handler
    that forgot the line was silently public.
    """
    return request.app.state.deps.auth.require(request)


PrincipalDep = Depends(require_principal)
SessionDep = Depends(get_db)
ProfileDep = Depends(get_profile)


def webhook_guard(name: str):
    """Verify the Remote Post shared secret for the named token."""

    def _guard(request: Request) -> str:
        tokens = request.app.state.deps.tokens
        presented = request.headers.get(tokens.header)
        counters = request.app.state.deps.counters
        if not tokens.check(name, presented):
            counters.record(authenticated=False)
            from eventkit.webhook import fingerprint
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing webhook authentication token.",
                headers={"X-Token-Fingerprint": fingerprint(presented)},
            )
        return name

    return _guard


def session_scope(database: Database):
    """Context manager for scripts and background tasks."""
    return database.session()


__all__ = [
    "AppDeps",
    "PrincipalDep",
    "ProfileDep",
    "SessionDep",
    "Session",
    "WebhookCounters",
    "deps",
    "get_db",
    "get_profile",
    "require_principal",
    "session_scope",
    "webhook_guard",
]
