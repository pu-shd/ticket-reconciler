"""deploy/app.conf is a contract with the eventkit Azure toolkit.

It shipped once with `name = "X"; type = "computed"` on a single line, which is
not TOML — the semicolon and everything after it is a syntax error, so the
toolkit could not read a single setting. Nothing caught it because nothing
parsed the file outside the toolkit. This does.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parent.parent / "deploy" / "app.conf"

KNOWN_TYPES = {"computed", "secret", "list", "bool", "int", "string", "choice", "fixed"}


@pytest.fixture(scope="module")
def conf() -> dict:
    with CONF.open("rb") as fh:
        return tomllib.load(fh)


def test_it_parses(conf):
    assert conf["name"]
    assert conf["image"]
    assert conf["health_path"].startswith("/")


def test_every_setting_is_a_table_with_a_known_type(conf):
    settings = conf.get("setting", [])
    assert settings, "an app with no declared settings cannot be deployed"
    for setting in settings:
        assert setting["name"] == setting["name"].upper(), setting
        assert setting["type"] in KNOWN_TYPES, setting


def test_no_setting_is_declared_twice(conf):
    names = [s["name"] for s in conf.get("setting", [])]
    assert len(names) == len(set(names)), names


def test_no_secret_value_is_committed(conf):
    """Secrets are generated or prompted for; a literal here would be public."""
    for setting in conf.get("setting", []):
        if setting["type"] == "secret":
            assert "value" not in setting, setting["name"]
            assert "default" not in setting, setting["name"]


def test_the_container_start_limit_is_raised(conf):
    """App Service kills a container that has not answered in 230s; a first boot
    that runs migrations exceeds that."""
    fixed = {s["name"]: s.get("value") for s in conf.get("setting", []) if s["type"] == "fixed"}
    assert fixed.get("WEBSITES_CONTAINER_START_TIME_LIMIT") == "600"
