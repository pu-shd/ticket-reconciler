"""Ticket-tier resolution and purchase URLs.

Replaces the predecessor's hardcoded block, where two live discount codes, an
institutional email-domain branch, and the Eventbrite event slug were string
literals in a route handler.

The profile carries the *name* of an environment variable, never a code. The
profile is committed and is served to the browser at ``GET /api/event-profile``,
so a code pasted there would be public. Codes live only in App Service settings.
"""

from __future__ import annotations

import logging
import os

from eventkit.eventprofile import EventProfile

logger = logging.getLogger("ticket_reconciler.ticketing")


def resolve_tier(profile: EventProfile, *, email: str, fields: dict | None = None):
    """The tier this person should be offered, or ``None``.

    Delegates to the profile so the rules live in configuration. A tier already
    computed by the webform's Twig and carried in ``destination_url`` should be
    preferred by the caller; this is the fallback.
    """
    resolver = getattr(profile.ticketing, "resolve_tier", None)
    if resolver is None:  # pragma: no cover - older profile schema
        return None
    return resolver(email=email, fields=fields or {})


def discount_code_for(tier) -> str | None:
    """Read the code from the environment variable the tier names.

    Missing is not an error: an event may legitimately have a tier with no
    discount. It is logged once at debug so a misconfiguration is findable
    without putting the variable's *value* anywhere near a log line.
    """
    if tier is None or not getattr(tier, "discount_code_env", None):
        return None
    code = os.getenv(tier.discount_code_env)
    if not code:
        logger.debug(
            "tier %s names %s but it is unset", tier.key, tier.discount_code_env
        )
    return code or None


def purchase_url_for(
    profile: EventProfile, *, email: str, fields: dict | None = None
) -> str | None:
    """Build the Eventbrite URL to send someone who still owes a ticket."""
    event_id = os.getenv("EVENTBRITE_EVENT_ID")
    if not event_id:
        return None

    tier = resolve_tier(profile, email=email, fields=fields)
    code = discount_code_for(tier)

    builder = getattr(profile.ticketing, "purchase_url", None)
    if builder is None:  # pragma: no cover - older profile schema
        return None
    return builder(event_id=event_id, discount_code=code)
