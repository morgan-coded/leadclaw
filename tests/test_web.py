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

TEST_WEB_PORT = 7499


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


@pytest.fixture(scope="module")
def web_server():
    server = HTTPServer(("127.0.0.1", TEST_WEB_PORT), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    yield server
    server.shutdown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(path, port=TEST_WEB_PORT):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def _post(path, data=None, port=TEST_WEB_PORT):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(data or {}).encode()
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, json.loads(body)


# ---------------------------------------------------------------------------
# api_summary unit tests (no HTTP)
# ---------------------------------------------------------------------------

def test_api_summary_empty_db():
    data = api_summary()
    assert "pipeline" in data and "today" in data and "stale" in data and "active" in data
    assert data["pipeline"]["open_value"] == 0


def test_api_summary_with_leads():
    queries.add_lead("Web Test", "roofing", phone="555-1111")
    id2, _ = queries.add_lead("Quoted Lead", "painting")
    queries.update_quote(id2, 1500.0)
    data = api_summary()
    assert data["pipeline"]["open_value"] > 0
    names = [l["name"] for l in data["active"]]
    assert "Web Test" in names and "Quoted Lead" in names


def test_api_summary_lead_fields():
    queries.add_lead("Field Check", "fencing", phone="555-9999", email="a@b.com", notes="test")
    data = api_summary()
    lead = next(l for l in data["active"] if l["name"] == "Field Check")
    assert lead["phone"] == "555-9999"
    assert lead["email"] == "a@b.com"
    assert lead["notes"] == "test"
    assert "id" in lead and "follow_up_after" in lead


# ---------------------------------------------------------------------------
# HTTP GET tests
# ---------------------------------------------------------------------------

def test_http_dashboard_200(web_server):
    status, body = _get("/")
    assert status == 200
    assert b"LeadClaw" in body


def test_http_api_summary_json(web_server):
    status, body = _get("/api/summary")
    assert status == 200
    assert b"pipeline" in body


def test_http_404(web_server):
    status, _ = _get("/nonexistent")
    assert status == 404


# ---------------------------------------------------------------------------
# POST /api/leads — add lead
# ---------------------------------------------------------------------------

def test_post_add_lead_valid(web_server, capsys):
    status, body = _post("/api/leads", {"name": "HTTP Add", "service": "gutters"})
    assert status == 201
    assert "id" in body
    lead = queries.get_lead_by_id(body["id"])
    assert lead is not None
    assert lead["name"] == "HTTP Add"


def test_post_add_lead_missing_fields(web_server):
    status, body = _post("/api/leads", {"name": "No Service"})
    assert status == 400
    assert "error" in body


def test_post_add_lead_empty_body(web_server):
    status, body = _post("/api/leads", {})
    assert status == 400


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/edit
# ---------------------------------------------------------------------------

def test_post_edit_lead(web_server):
    lead_id, _ = queries.add_lead("Edit Me", "painting")
    status, body = _post(f"/api/leads/{lead_id}/edit", {"phone": "555-7777", "notes": "updated"})
    assert status == 200
    assert body.get("ok")
    lead = queries.get_lead_by_id(lead_id)
    assert lead["phone"] == "555-7777"
    assert lead["notes"] == "updated"


def test_post_edit_lead_not_found(web_server):
    status, body = _post("/api/leads/99999/edit", {"phone": "555-0000"})
    assert status == 404


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/quote
# ---------------------------------------------------------------------------

def test_post_quote_valid(web_server):
    lead_id, _ = queries.add_lead("Quote Me", "roofing")
    status, body = _post(f"/api/leads/{lead_id}/quote", {"amount": 1200})
    assert status == 200
    lead = queries.get_lead_by_id(lead_id)
    assert lead["quote_amount"] == 1200.0
    assert lead["status"] == "quoted"


def test_post_quote_negative(web_server):
    lead_id, _ = queries.add_lead("Bad Quote", "fencing")
    status, body = _post(f"/api/leads/{lead_id}/quote", {"amount": -50})
    assert status == 400


def test_post_quote_missing_amount(web_server):
    lead_id, _ = queries.add_lead("No Amount", "fencing")
    status, body = _post(f"/api/leads/{lead_id}/quote", {})
    assert status == 400


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/won
# ---------------------------------------------------------------------------

def test_post_won(web_server):
    lead_id, _ = queries.add_lead("Win Me", "lawn care")
    status, body = _post(f"/api/leads/{lead_id}/won")
    assert status == 200
    lead = queries.get_lead_by_id(lead_id)
    assert lead["status"] == "won"


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/lost
# ---------------------------------------------------------------------------

def test_post_lost_valid(web_server):
    lead_id, _ = queries.add_lead("Lose Me", "pressure washing")
    status, body = _post(f"/api/leads/{lead_id}/lost", {"reason": "price"})
    assert status == 200
    lead = queries.get_lead_by_id(lead_id)
    assert lead["status"] == "lost"
    assert lead["lost_reason"] == "price"


def test_post_lost_other_requires_notes(web_server):
    lead_id, _ = queries.add_lead("Other Lost", "cleaning")
    status, body = _post(f"/api/leads/{lead_id}/lost", {"reason": "other"})
    assert status == 400


def test_post_lost_other_with_notes(web_server):
    lead_id, _ = queries.add_lead("Other OK", "painting")
    status, body = _post(f"/api/leads/{lead_id}/lost", {"reason": "other", "notes": "some reason"})
    assert status == 200


def test_post_lost_invalid_reason(web_server):
    lead_id, _ = queries.add_lead("Bad Reason", "roofing")
    status, body = _post(f"/api/leads/{lead_id}/lost", {"reason": "bad_reason"})
    assert status == 400


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/delete
# ---------------------------------------------------------------------------

def test_post_delete(web_server):
    lead_id, _ = queries.add_lead("Delete Me", "fencing")
    status, body = _post(f"/api/leads/{lead_id}/delete")
    assert status == 200
    assert queries.get_lead_by_id(lead_id) is None


def test_post_delete_not_found(web_server):
    status, body = _post("/api/leads/99999/delete")
    assert status == 404
