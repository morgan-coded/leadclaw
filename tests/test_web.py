"""
tests/test_web.py - Web dashboard API tests
"""
import json
import os
import threading
from http.client import HTTPConnection

import pytest

from leadclaw import db, queries
from tests.conftest import TEST_DB
from leadclaw.web import DEFAULT_PORT, Handler, api_summary
from http.server import HTTPServer


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


# ---------------------------------------------------------------------------
# api_summary unit tests (no HTTP)
# ---------------------------------------------------------------------------

def test_api_summary_empty_db():
    data = api_summary()
    assert "pipeline" in data
    assert "today" in data
    assert "stale" in data
    assert "active" in data
    assert data["pipeline"]["open_value"] == 0


def test_api_summary_with_leads():
    queries.add_lead("Web Test", "roofing", phone="555-1111")
    id2, _ = queries.add_lead("Quoted Lead", "painting")
    queries.update_quote(id2, 1500.0)
    data = api_summary()
    assert data["pipeline"]["open_value"] > 0
    names = [l["name"] for l in data["active"]]
    assert "Web Test" in names
    assert "Quoted Lead" in names


def test_api_summary_lead_fields():
    queries.add_lead("Field Check", "fencing", phone="555-9999", email="a@b.com", notes="test")
    data = api_summary()
    lead = next(l for l in data["active"] if l["name"] == "Field Check")
    assert lead["phone"] == "555-9999"
    assert lead["email"] == "a@b.com"
    assert lead["notes"] == "test"
    assert "id" in lead
    assert "follow_up_after" in lead


# ---------------------------------------------------------------------------
# HTTP integration tests (spins up a real server on a test port)
# ---------------------------------------------------------------------------

TEST_WEB_PORT = 7499


@pytest.fixture(scope="module")
def web_server():
    server = HTTPServer(("127.0.0.1", TEST_WEB_PORT), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    yield server
    server.shutdown()


def _get(path: str, port: int = TEST_WEB_PORT):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def test_http_dashboard_200(web_server):
    status, body = _get("/")
    assert status == 200
    assert b"LeadClaw" in body


def test_http_api_summary_json(web_server):
    status, body = _get("/api/summary")
    assert status == 200
    data = json.loads(body)
    assert "pipeline" in data


def test_http_404(web_server):
    status, _ = _get("/nonexistent")
    assert status == 404
