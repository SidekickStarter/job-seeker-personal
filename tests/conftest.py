"""Shared fixtures. Tests never touch the network: `fake_http` serves the JSON in tests/fixtures."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from jobradar import util
from jobradar.workspace import Workspace

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "jobradar" / "templates"

ROUTES = [
    (r"boards-api\.greenhouse\.io/v1/boards/acme/jobs\?content=true$", "greenhouse_jobs.json"),
    (r"boards-api\.greenhouse\.io/v1/boards/acme/jobs/101\?questions=true$", "greenhouse_form.json"),
    (r"api\.ashbyhq\.com/posting-api/job-board/beta$", "ashby_board.json"),
    (r"api\.lever\.co/v0/postings/gamma\?mode=json$", "lever_postings.json"),
]

CONFIG = """
[[companies]]
name = "Acme"
track = "payments"
ats = "greenhouse"
slug = "acme"

[[companies]]
name = "Beta"
track = "backend"
ats = "ashby"
slug = "beta"

[[companies]]
name = "Gamma"
track = "backend"
ats = "lever"
slug = "gamma"
"""


@pytest.fixture
def fake_http(monkeypatch):
    calls = []

    def fake(url, payload=None):
        calls.append(url)
        for pattern, name in ROUTES:
            if re.search(pattern, url):
                return json.loads((FIXTURES / name).read_text())
        raise util.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(util, "http_json", fake)
    return calls


@pytest.fixture
def ws(tmp_path) -> Workspace:
    """A workspace using the shipped example config, with the example companies swapped for
    fixture-backed ones, and a filled-in profile."""
    text = (TEMPLATES / "config.example.toml").read_text()
    text = text[:text.index("[[companies]]")] + CONFIG
    (tmp_path / "config.toml").write_text(text)
    profile = (TEMPLATES / "profile.example.toml").read_text()
    for a, b in (('"First"', '"Alex"'), ('"Last"', '"Rivera"'), ('city = ""', 'city = "Brooklyn"'),
                 ('state = ""', 'state = "New York"')):
        profile = profile.replace(a, b, 1)
    (tmp_path / "profile.toml").write_text(profile)
    return Workspace.load(tmp_path)


@pytest.fixture
def example_config(tmp_path) -> Path:
    shutil.copy(TEMPLATES / "config.example.toml", tmp_path / "config.toml")
    return tmp_path / "config.toml"
