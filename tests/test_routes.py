"""HTTP surface: authentication, webhook, front desk, and the destructive clear."""

from __future__ import annotations

import logging

import pytest
from eventkit.testing.plugin import STRONG_TEST_TOKEN
from eventkit.webhook import WEAK_TOKENS

from ticket_reconciler.models import Payment, Registrant

PROTECTED = [
    ("get", "/api/reports/registrations"),
    ("get", "/api/reports/stats"),
    ("get", "/api/reports/export.csv"),
    ("get", "/api/swag/inventory"),
    ("get", "/api/groups"),
    ("get", "/api/webhook/status"),
    ("get", "/api/admin/db-backup"),
]


class TestProtection:
    @pytest.mark.parametrize(("verb", "path"), PROTECTED)
    def test_anonymous_is_refused(self, anon_client, verb, path):
        res = getattr(anon_client, verb)(path)
        assert res.status_code != 200, f"{path} answered 200 to an anonymous caller"

    def test_dashboard_redirects_anonymous_to_login(self, anon_client):
        res = anon_client.get("/")
        assert res.status_code in (302, 307)
        assert "/.auth/login" in res.headers.get("location", "")

    def test_healthz_is_open(self, anon_client):
        assert anon_client.get("/healthz").json() == {"status": "ok"}


class TestWebhook:
    def test_requires_a_token(self, client):
        assert client.post("/api/drupal-webhook", json={"data": {}}).status_code == 403

    def test_creates_a_registrant(self, webhook_post, session):
        assert webhook_post().status_code == 200
        r = session.query(Registrant).one()
        assert r.first_name == "Ada"
        assert r.tickets_sold_separately is True
        assert r.swag_size == "UMED"

    def test_is_idempotent(self, webhook_post, session):
        webhook_post()
        webhook_post()
        session.expire_all()
        assert session.query(Registrant).count() == 1

    def test_absent_exemption_box_reads_as_exempt(self, webhook_post, session):
        """A #states-hidden checkbox is absent from the payload, not false."""
        webhook_post(data={"tickets_sold_separately": ""})
        assert session.query(Registrant).one().tickets_sold_separately is False

    def test_no_token_reaches_the_logs(self, client, caplog):
        with caplog.at_level(logging.DEBUG):
            client.post(
                "/api/drupal-webhook",
                json={"data": {"email": "a@example.edu"}},
                headers={"X-Drupal-Webhook-Token": "wrong-but-long-enough-to-look-real"},
            )
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert STRONG_TEST_TOKEN not in logged
        assert "wrong-but-long-enough-to-look-real" not in logged


class TestReports:
    def test_report_and_stats(self, webhook_post, client):
        webhook_post()
        rows = client.get("/api/reports/registrations").json()
        assert len(rows) == 1
        assert rows[0]["status"] == "Pending"
        assert client.get("/api/reports/stats").json()["pending"] == 1

    def test_csv_export(self, webhook_post, client):
        webhook_post()
        res = client.get("/api/reports/export.csv")
        assert res.status_code == 200
        assert "text/csv" in res.headers["content-type"]
        assert "Ada" in res.text

    def test_manual_link_and_unlink(self, webhook_post, client, session):
        webhook_post()
        key = session.query(Registrant).one().person_key
        session.add(Payment(id="pay-1", email="someone.else@example.edu", status="paid",
                            gross_amount=100, net_amount=90))
        session.commit()

        linked = client.post("/api/reports/link",
                             json={"person_key": key, "payment_id": "pay-1"}).json()
        assert linked["payment_id"] == "pay-1"
        assert linked["status"] == "Paid"

        unlinked = client.post("/api/reports/unlink", json={"person_key": key}).json()
        assert unlinked["payment_id"] is None
        assert unlinked["status"] == "Pending"

    def test_link_to_a_missing_payment_is_404(self, webhook_post, client, session):
        webhook_post()
        key = session.query(Registrant).one().person_key
        assert client.post("/api/reports/link",
                           json={"person_key": key, "payment_id": "nope"}).status_code == 404


class TestFrontDesk:
    def _key(self, session):
        return session.query(Registrant).one().person_key

    def test_checkin_cycles_state(self, webhook_post, client, session, app_profile):
        webhook_post()
        key = self._key(session)
        day = app_profile.schedule.checkin_days[0].key
        body = client.post("/api/checkin",
                           json={"person_key": key, "day_key": day, "state": 1}).json()
        assert body["checkin_status"][day] == 1

    def test_checkin_rejects_an_unknown_day(self, webhook_post, client, session):
        webhook_post()
        res = client.post("/api/checkin",
                          json={"person_key": self._key(session), "day_key": "6/28", "state": 1})
        assert res.status_code == 400
        assert "Unknown check-in day" in res.json()["detail"]

    def test_checked_in_becomes_complete_once_paid(self, webhook_post, client, session, app_profile):
        webhook_post()
        key = self._key(session)
        session.add(Payment(id="p", email="ada@example.edu", status="paid",
                            gross_amount=100, net_amount=90))
        session.commit()
        client.post("/api/checkin", json={"person_key": key,
                                          "day_key": app_profile.schedule.checkin_days[0].key,
                                          "state": 1})
        rows = client.get("/api/reports/registrations").json()
        assert rows[0]["status"] == "Complete"

    def test_swag_issue_toggles(self, webhook_post, client, session):
        webhook_post()
        key = self._key(session)
        assert client.post("/api/swag/checkin",
                           json={"person_key": key, "value": True}).json()["swag_checked_in"] is True

    def test_swag_inventory_is_seeded_and_settable(self, client):
        seeded = client.get("/api/swag/inventory").json()
        assert {i["size"] for i in seeded} >= {"USML", "UMED"}
        out = client.post("/api/swag/inventory", json=[{"size": "UMED", "total_count": 40}]).json()
        assert next(i for i in out if i["size"] == "UMED")["total_count"] == 40

    def test_swag_rejects_an_unknown_size(self, client):
        assert client.post("/api/swag/inventory",
                           json=[{"size": "XXXL", "total_count": 1}]).status_code == 400

    def test_replacement_size_rejects_unknown(self, webhook_post, client, session):
        webhook_post()
        res = client.patch(f"/api/registrants/{self._key(session)}/swag-replacement",
                           json={"size": "NOPE"})
        assert res.status_code == 400

    def test_waiver_requires_a_justification(self, webhook_post, client, session):
        webhook_post()
        key = self._key(session)
        assert client.post(f"/api/registrants/{key}/waive",
                           json={"person_key": key, "waived": True}).status_code == 422

    def test_waiver_with_a_justification_is_kept(self, webhook_post, client, session):
        webhook_post()
        key = self._key(session)
        body = client.post(f"/api/registrants/{key}/waive",
                           json={"person_key": key, "waived": True,
                                 "justification": "Hardship, approved by the chair"}).json()
        assert body["waived"] is True
        assert "chair" in body["waiver_justification"]
        assert client.get("/api/reports/registrations").json()[0]["status"] == "Waived"

    def test_manual_exemption(self, webhook_post, client, session):
        webhook_post()
        key = self._key(session)
        client.post(f"/api/registrants/{key}/exemption", json={"person_key": key, "value": True})
        assert client.get("/api/reports/registrations").json()[0]["status"] == "Manually Set to Exempt"

    def test_refund_override_marks_them_present(self, webhook_post, client, session):
        webhook_post()
        key = self._key(session)
        session.add(Payment(id="p", email="ada@example.edu", status="refunded",
                            gross_amount=100, net_amount=90))
        session.commit()
        assert client.get("/api/reports/registrations").json()[0]["status"] == "Refunded"
        client.post(f"/api/registrants/{key}/refund-override",
                    json={"person_key": key, "value": True})
        assert client.get("/api/reports/registrations").json()[0]["status"] == "Complete"

    def test_saved_groups_round_trip(self, client):
        client.post("/api/groups", json={"name": "Speakers", "person_keys": ["a", "b"]})
        assert client.get("/api/groups").json()[0]["name"] == "Speakers"
        assert client.delete("/api/groups/Speakers").status_code == 204
        assert client.get("/api/groups").json() == []


class TestDestructiveClear:
    """The predecessor's equivalent had no auth dependency at all."""

    def test_anonymous_cannot_clear(self, anon_client, webhook_post, session):
        webhook_post()
        res = anon_client.post("/api/admin/clear",
                               json={"target": "both", "confirm": "DESTROY"})
        assert res.status_code in (401, 403, 307)
        session.expire_all()
        assert session.query(Registrant).count() == 1

    def test_disabled_by_default_even_for_an_admin(self, client, webhook_post, session):
        webhook_post()
        res = client.post("/api/admin/clear", json={"target": "both", "confirm": "DESTROY"})
        assert res.status_code == 403
        assert "disabled" in res.json()["detail"].lower()
        session.expire_all()
        assert session.query(Registrant).count() == 1

    def test_wrong_phrase_is_refused_when_enabled(self, app, client, webhook_post):
        app.state.deps.settings.enable_destructive_ops = True
        assert client.post("/api/admin/clear",
                           json={"target": "both", "confirm": "nope"}).status_code == 400

    def test_clears_when_explicitly_enabled(self, app, client, webhook_post, session):
        webhook_post()
        app.state.deps.settings.enable_destructive_ops = True
        body = client.post("/api/admin/clear",
                           json={"target": "registrations", "confirm": "DESTROY"}).json()
        assert body["deleted_registrants"] == 1
        session.expire_all()
        assert session.query(Registrant).count() == 0


class TestSync:
    def test_sync_without_eventbrite_configured_is_503(self, client):
        assert client.post("/api/sync").status_code == 503


class TestSettingsFailClosed:
    def test_defaults(self):
        from ticket_reconciler.settings import Settings

        f = Settings.model_fields
        assert f["enable_restore"].default is False
        assert f["enable_destructive_ops"].default is False
        assert f["authorized_principals"].default == ""
        assert f["drupal_webhook_token"].is_required()

    @pytest.mark.parametrize("weak", sorted(WEAK_TOKENS))
    def test_placeholder_token_rejected(self, weak):
        """Every token on eventkit's weak list is refused, with a ConfigError
        rather than a pydantic ValidationError, so the startup message names the
        variable and says how to generate a real one.

        The values come from ``WEAK_TOKENS`` rather than being written out here:
        CI greps the tree for the committed placeholders, and a copy living in a
        test is indistinguishable from a copy living in config.
        """
        from eventkit.errors import ConfigError

        from ticket_reconciler.settings import Settings

        with pytest.raises(ConfigError, match="placeholder"):
            Settings(drupal_webhook_token=weak)
