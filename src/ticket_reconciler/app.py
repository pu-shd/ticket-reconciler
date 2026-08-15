"""Application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from eventkit.auth import AllowList, DeniedTheme, EasyAuth
from eventkit.auth import install as install_auth
from eventkit.backup import BackupSpec, TableSpec, make_backup_router
from eventkit.db import Database
from eventkit.drupal import resolve_field_map
from eventkit.eventprofile import EventProfile
from eventkit.eventprofile.load import load_profile
from eventkit.logging import configure_logging
from eventkit.realtime import make_changes_router
from eventkit.ui import static_path
from eventkit.webhook import WebhookTokens
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .deps import AppDeps, WebhookCounters, get_db, require_principal
from .models import Base, ChangeLog, Payment, Registrant, SavedGroup, SwagInventory, SyncLog
from .routers import admin, frontdesk, reports, webhook
from .settings import Settings, get_settings

logger = logging.getLogger("ticket_reconciler")

REQUIRED_FIELDS = ["email", "name"]
OPTIONAL_FIELDS = [
    "uuid", "sid", "serial", "tickets_sold_separately", "destination_url",
    "t_shirt_size", "attendee_status",
]

BACKUP_SPEC = BackupSpec(
    app_name="ticket-reconciler",
    app_version="1.0.0",
    tables=[
        TableSpec(model=Registrant, key="registrants", order=0),
        TableSpec(model=Payment, key="payments", order=1),
        TableSpec(model=SavedGroup, key="saved_groups", order=2),
        TableSpec(model=SwagInventory, key="swag_inventory", order=3),
        TableSpec(model=SyncLog, key="sync_logs", order=4),
    ],
    required_keys={"registrants", "payments"},
)


@dataclass
class ReconcilerDeps(AppDeps):
    """AppDeps plus the notifier, which only this application has."""

    notifier: object | None = field(default=None)


def _build_notifier(profile: EventProfile, settings: Settings):
    """Log transport unless configured otherwise, so a missing credential can
    never stop a deploy."""
    try:
        from eventkit.notify import Notifier, NotifyPolicy, NotifySettings, transport_from_settings
        from eventkit.notify.render import Renderer

        transport = transport_from_settings(
            NotifySettings(transport=settings.notify_transport)
        )
        policy = NotifyPolicy(
            enabled=dict(profile.notify.events or {}),
            default_recipients=[str(a) for a in (profile.notify.default_recipients or [])],
        )
        return Notifier(
            transport,
            Renderer(),
            policy,
            from_email=(settings.notification_recipient_email or "noreply@example.edu"),
            from_name=profile.notify.from_name or profile.event.title,
        )
    except Exception:  # noqa: BLE001 - notifications are never load-bearing
        logger.exception("notifier could not be built; continuing without one")
        return None


def build_deps(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    profile: EventProfile | None = None,
) -> ReconcilerDeps:
    settings = settings or get_settings()
    profile = profile or load_profile(settings.event_profile)
    profile.validate_for_app("ticket-reconciler", require=["event", "schedule", "branding"])

    database = database or Database(settings.database_url)
    field_map = resolve_field_map(profile, want=REQUIRED_FIELDS + OPTIONAL_FIELDS)

    auth = EasyAuth(
        AllowList.parse(settings.authorized_principals),
        dev_principal=settings.dev_principal,
        page_paths=("/",),
        theme=DeniedTheme.from_profile(profile),
    )
    tokens = WebhookTokens({"registration": settings.drupal_webhook_token})
    tokens.assert_all_strong()

    return ReconcilerDeps(
        settings=settings,
        database=database,
        profile=profile,
        auth=auth,
        tokens=tokens,
        field_map=field_map,
        counters=WebhookCounters(),
        notifier=_build_notifier(profile, settings),
    )


def _seed_swag(database: Database, profile: EventProfile) -> None:
    """Create an inventory row per configured size, once."""
    if not profile.swag.enabled:
        return
    with database.session() as session:
        for option in profile.swag.options:
            if not option.counts_toward_inventory:
                continue
            if session.get(SwagInventory, option.key) is None:
                session.add(SwagInventory(size=option.key, total_count=0))
        session.commit()


def create_app(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    profile: EventProfile | None = None,
    create_schema: bool = True,
) -> FastAPI:
    configure_logging()
    app_deps = build_deps(settings=settings, database=database, profile=profile)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if create_schema:
            Base.metadata.create_all(app_deps.database.engine)
            _seed_swag(app_deps.database, app_deps.profile)
        logger.info(
            "ticket-reconciler ready event=%s eventbrite=%s destructive_ops=%s",
            app_deps.profile.event.slug,
            app_deps.settings.eventbrite_configured,
            app_deps.settings.enable_destructive_ops,
        )
        yield

    app = FastAPI(
        title="ticket-reconciler",
        version="1.0.0",
        summary="Drupal registrations reconciled against Eventbrite sales.",
        lifespan=lifespan,
    )
    app.state.deps = app_deps
    app.state.database = app_deps.database
    app.state.auth = app_deps.auth
    app.state.profile = app_deps.profile

    install_auth(app, app_deps.auth)

    app.include_router(admin.router)
    app.include_router(reports.router)
    app.include_router(frontdesk.router)
    app.include_router(webhook.router)
    app.include_router(
        make_backup_router(
            BACKUP_SPEC,
            db=get_db,
            principal=require_principal,
            enable_restore=lambda: app_deps.settings.enable_restore,
            database=app_deps.database,
        )
    )
    app.include_router(make_changes_router(ChangeLog, db=get_db, principal=require_principal))

    app.mount("/ui", StaticFiles(directory=static_path()), name="ui")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
