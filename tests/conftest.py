"""Test wiring. eventkit's pytest plugin supplies the rest via its entry point."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from eventkit.db import Database
from eventkit.testing.plugin import STRONG_TEST_TOKEN
from starlette.testclient import TestClient

from ticket_reconciler.app import create_app
from ticket_reconciler.deps import get_db, require_principal
from ticket_reconciler.models import Base
from ticket_reconciler.settings import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        drupal_webhook_token=STRONG_TEST_TOKEN,
        authorized_principals="admin@example.edu",
        enable_restore=False,
        enable_destructive_ops=False,
    )


@pytest.fixture
def database(settings: Settings) -> Database:
    db = Database(settings.database_url)
    Base.metadata.create_all(db.engine)
    return db


@pytest.fixture
def session(database: Database) -> Iterator:
    with database.session() as s:
        yield s


@pytest.fixture
def app_profile():
    """The profile this repository actually ships.

    Preferred over eventkit's minimal fixture: the app's own example configures
    swag sizes, check-in days and ticket tiers, so the tests exercise the
    configuration an adopter will start from rather than a stripped-down one.
    """
    from eventkit.eventprofile.load import load_profile

    return load_profile(Path(__file__).resolve().parents[1] / "event-profile.yaml")


@pytest.fixture
def app(settings: Settings, database: Database, app_profile):
    return create_app(settings=settings, database=database, profile=app_profile)


@pytest.fixture
def client(app, session) -> Iterator[TestClient]:
    from eventkit.auth import Principal

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_principal] = lambda: Principal(email="admin@example.edu")
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client(app, session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def webhook_post(client):
    """POST a Drupal-shaped submission with a valid token."""

    def _post(**over):
        body = {
            "sid": "1", "serial": "7",
            "uuid": "3f8c1e2a-0000-4000-8000-000000000001",
            "data": {
                "email": "ada@example.edu",
                "registrant_name": {"first": "Ada", "last": "Lovelace"},
                "tickets_sold_separately": "1",
                "t_shirt_size": "UMED",
            },
        }
        body["data"].update(over.pop("data", {}))
        body.update(over)
        return client.post(
            "/api/drupal-webhook", json=body,
            headers={"X-Drupal-Webhook-Token": STRONG_TEST_TOKEN},
        )

    return _post
