"""Page rendering.

Static HTML plus a small injected JSON blob, rather than a template engine. The
page needs exactly one thing from the server — the public event profile — and
shipping it inline saves a round trip and means the page renders correctly before
any JavaScript runs.
"""

from __future__ import annotations

import json
from functools import lru_cache
from html import escape
from pathlib import Path

from eventkit.eventprofile import EventProfile
from eventkit.eventprofile.public import to_public_dict
from eventkit.ui import render_theme_vars

STATIC_DIR = Path(__file__).parent / "static"


@lru_cache(maxsize=8)
def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def render_page(name: str, profile: EventProfile) -> str:
    """Substitute the profile-derived placeholders into a static page.

    Only four placeholders, all escaped. There is no user-supplied data in this
    path; presenter content is fetched by the page and escaped client-side.
    """
    html = _read(name)
    public = to_public_dict(profile)
    replacements = {
        "{{ EVENT_TITLE }}": escape(profile.event.title),
        "{{ EVENT_NAME }}": escape(profile.event.name),
        "{{ SITE_URL }}": escape(str(profile.event.site_url)),
        "{{ THEME_VARS }}": render_theme_vars(profile),
        "{{ PROFILE_JSON }}": json.dumps(public).replace("</", "<\\/"),
    }
    for token, value in replacements.items():
        html = html.replace(token, value)
    return html
