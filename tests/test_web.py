"""
tests/test_web.py - Web dashboard API + HTML structure tests
"""

import json
import os
import threading
from http.client import HTTPConnection
from http.server import HTTPServer

import pytest

from leadclaw import db, queries
from leadclaw.config import MAX_NAME_LENGTH
from leadclaw.web import DASHBOARD_HTML, Handler, api_closed, api_summary
from tests.conftest import TEST_DB

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
# HTTP helpers
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
# api_summary / api_closed unit tests (no HTTP)
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
    names = [lead["name"] for lead in data["active"]]
    assert "Web Test" in names and "Quoted Lead" in names


def test_api_summary_lead_fields():
    queries.add_lead("Field Check", "fencing", phone="555-9999", email="a@b.com", notes="test")
    data = api_summary()
    lead = next(row for row in data["active"] if row["name"] == "Field Check")
    assert lead["phone"] == "555-9999"
    assert lead["email"] == "a@b.com"
    assert lead["notes"] == "test"
    assert "id" in lead and "follow_up_after" in lead


def test_api_closed_empty():
    data = api_closed()
    assert data["closed"] == []


def test_api_closed_contains_won_and_lost():
    id1, _ = queries.add_lead("Won Lead", "roofing")
    id2, _ = queries.add_lead("Lost Lead", "painting")
    id3, _ = queries.add_lead("Active Lead", "gutters")
    queries.mark_won(id1)
    queries.mark_lost(id2, "price")
    data = api_closed()
    names = [lead["name"] for lead in data["closed"]]
    assert "Won Lead" in names
    assert "Lost Lead" in names
    assert "Active Lead" not in names


def test_api_closed_includes_lost_reason():
    id1, _ = queries.add_lead("Lost With Reason", "painting")
    queries.mark_lost(id1, "price")
    data = api_closed()
    lead = next(row for row in data["closed"] if row["name"] == "Lost With Reason")
    assert lead["lost_reason"] == "price"


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


def test_http_api_closed(web_server):
    status, body = _get("/api/closed")
    assert status == 200
    data = json.loads(body)
    assert "closed" in data


def test_http_404(web_server):
    status, _ = _get("/nonexistent")
    assert status == 404


# ---------------------------------------------------------------------------
# POST /api/leads — add lead (including validation)
# ---------------------------------------------------------------------------


def test_post_add_lead_valid(web_server):
    status, body = _post("/api/leads", {"name": "HTTP Add", "service": "gutters"})
    assert status == 201
    assert "id" in body
    lead = queries.get_lead_by_id(body["id"])
    assert lead is not None and lead["name"] == "HTTP Add"


def test_post_add_lead_missing_fields(web_server):
    status, body = _post("/api/leads", {"name": "No Service"})
    assert status == 400 and "error" in body


def test_post_add_lead_empty_body(web_server):
    status, body = _post("/api/leads", {})
    assert status == 400


def test_post_add_lead_name_too_long(web_server):
    status, body = _post("/api/leads", {"name": "A" * (MAX_NAME_LENGTH + 1), "service": "roofing"})
    assert status == 400
    assert "name" in body.get("error", "")


def test_post_add_lead_invalid_email(web_server):
    status, body = _post(
        "/api/leads", {"name": "Bad Email", "service": "painting", "email": "notanemail"}
    )
    assert status == 400
    assert "email" in body.get("error", "")


def test_post_add_lead_duplicate_warning(web_server):
    """Adding a lead with the same name as an existing one should return duplicates list."""
    queries.add_lead("Dup Name", "roofing")
    status, body = _post("/api/leads", {"name": "Dup Name", "service": "painting"})
    assert status == 201
    assert "duplicates" in body
    assert len(body["duplicates"]) >= 1


def test_post_add_lead_no_duplicate_warning_for_unique(web_server):
    """Unique name should not return duplicates key."""
    status, body = _post("/api/leads", {"name": "Unique XYZ 999", "service": "gutters"})
    assert status == 201
    assert not body.get("duplicates")


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/edit (validation)
# ---------------------------------------------------------------------------


def test_post_edit_lead(web_server):
    lead_id, _ = queries.add_lead("Edit Me", "painting")
    status, body = _post(f"/api/leads/{lead_id}/edit", {"phone": "555-7777", "notes": "updated"})
    assert status == 200 and body.get("ok")
    lead = queries.get_lead_by_id(lead_id)
    assert lead["phone"] == "555-7777" and lead["notes"] == "updated"


def test_post_edit_lead_invalid_email(web_server):
    lead_id, _ = queries.add_lead("Edit Email Bad", "roofing")
    status, body = _post(f"/api/leads/{lead_id}/edit", {"email": "bademail"})
    assert status == 400
    assert "email" in body.get("error", "")


def test_post_edit_lead_invalid_date(web_server):
    lead_id, _ = queries.add_lead("Edit Date Bad", "fencing")
    status, body = _post(f"/api/leads/{lead_id}/edit", {"follow_up_after": "not-a-date"})
    assert status == 400
    assert "follow_up_after" in body.get("error", "")


def test_post_edit_lead_valid_date(web_server):
    lead_id, _ = queries.add_lead("Edit Date OK", "painting")
    status, body = _post(f"/api/leads/{lead_id}/edit", {"follow_up_after": "2026-12-31"})
    assert status == 200 and body.get("ok")


def test_post_edit_name_too_long(web_server):
    lead_id, _ = queries.add_lead("Edit Name", "roofing")
    status, body = _post(f"/api/leads/{lead_id}/edit", {"name": "X" * (MAX_NAME_LENGTH + 1)})
    assert status == 400
    assert "name" in body.get("error", "")


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
    assert lead["quote_amount"] == 1200.0 and lead["status"] == "quoted"


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
    assert queries.get_lead_by_id(lead_id)["status"] == "won"


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/lost
# ---------------------------------------------------------------------------


def test_post_lost_valid(web_server):
    lead_id, _ = queries.add_lead("Lose Me", "pressure washing")
    status, body = _post(f"/api/leads/{lead_id}/lost", {"reason": "price"})
    assert status == 200
    lead = queries.get_lead_by_id(lead_id)
    assert lead["status"] == "lost" and lead["lost_reason"] == "price"


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


# ---------------------------------------------------------------------------
# HTML structure tests (browser UI assertions without a browser)
# ---------------------------------------------------------------------------


def test_html_contains_add_button():
    """Dashboard HTML must have the Add Lead button."""
    assert "openAdd()" in DASHBOARD_HTML
    assert "+ Add Lead" in DASHBOARD_HTML


def test_html_contains_all_modals():
    """All three modals must be present in the HTML."""
    assert 'id="modal-edit"' in DASHBOARD_HTML
    assert 'id="modal-quote"' in DASHBOARD_HTML
    assert 'id="modal-lost"' in DASHBOARD_HTML


def test_html_contains_closed_tab():
    """Closed-leads tab must be present."""
    assert "switchTab('closed')" in DASHBOARD_HTML
    assert 'id="tab-closed"' in DASHBOARD_HTML
    assert 'id="closed"' in DASHBOARD_HTML


def test_html_renders_active_lead_actions():
    """renderLead JS must include all action buttons for active leads."""
    assert "openQuote(" in DASHBOARD_HTML
    assert "openEdit(" in DASHBOARD_HTML
    assert "doWon(" in DASHBOARD_HTML
    assert "openLost(" in DASHBOARD_HTML
    assert "doDelete(" in DASHBOARD_HTML


def test_html_closed_leads_delete_only():
    """Won/lost leads must only get the Del button (no Quote/Won/Lost for closed leads)."""
    # The JS checks isActive before rendering full action set
    assert "isActive" in DASHBOARD_HTML


def test_html_duplicate_warning_element():
    """Duplicate warning banner must be in the Add modal."""
    assert 'id="dup-warn"' in DASHBOARD_HTML
    assert "duplicates" in DASHBOARD_HTML


def test_html_client_validation_email():
    """Client-side email validation function must be present."""
    assert "validEmail" in DASHBOARD_HTML


def test_html_client_validation_date():
    """Client-side date validation function must be present."""
    assert "validDate" in DASHBOARD_HTML


def test_html_lost_reasons_injected():
    """LOST_REASONS constant must be injected into the HTML."""
    assert "LOST_REASONS=" in DASHBOARD_HTML.replace(" ", "")
    assert "price" in DASHBOARD_HTML


def test_html_max_name_injected():
    """MAX_NAME constant must be injected for client-side length validation."""
    assert f"MAX_NAME={MAX_NAME_LENGTH}" in DASHBOARD_HTML.replace(" ", "")


def test_html_api_closed_fetch():
    """JS must call /api/closed when loading the closed tab."""
    assert "/api/closed" in DASHBOARD_HTML


def test_html_pilot_tab_present():
    """Pilot tab must be present in dashboard HTML."""
    assert "switchTab('pilot')" in DASHBOARD_HTML
    assert 'id="tab-pilot"' in DASHBOARD_HTML


def test_html_pilot_table_columns():
    """Pilot table must have score, status, source, follow-up, reply columns."""
    assert 'id="pilot-table"' in DASHBOARD_HTML
    assert "Score" in DASHBOARD_HTML
    assert "Source" in DASHBOARD_HTML
    assert "Follow-up" in DASHBOARD_HTML
    assert "Reply" in DASHBOARD_HTML


def test_html_pilot_action_buttons():
    """Pilot action buttons must be present in JS."""
    assert "openPilotDraft" in DASHBOARD_HTML
    assert "pilotAction" in DASHBOARD_HTML
    assert "openPilotReply" in DASHBOARD_HTML
    assert "save-and-approve" in DASHBOARD_HTML
    assert "mark-sent" in DASHBOARD_HTML
    assert "log-reply" in DASHBOARD_HTML


def test_html_pilot_modals():
    """Pilot draft and reply modals must be present."""
    assert 'id="modal-pilot-draft"' in DASHBOARD_HTML
    assert 'id="modal-pilot-reply"' in DASHBOARD_HTML


def test_html_pilot_status_filter():
    """Status filter select must be in the pilot tab."""
    assert 'id="pilot-filter"' in DASHBOARD_HTML


# ---------------------------------------------------------------------------
# Pilot API HTTP tests
# ---------------------------------------------------------------------------


def test_http_get_pilot_empty(web_server):
    status, body = _get("/api/pilot")
    assert status == 200
    data = json.loads(body)
    assert "candidates" in data
    assert "summary" in data
    assert data["candidates"] == []


def test_http_get_pilot_with_candidates(web_server):
    queries.add_lead("ignore", "roofing")  # leads don't appear in pilot
    from leadclaw import pilot as p

    p.add_candidate("Pilot Web Test", service_type="lawn care", phone="555-8888")
    status, body = _get("/api/pilot")
    assert status == 200
    data = json.loads(body)
    names = [c["name"] for c in data["candidates"]]
    assert "Pilot Web Test" in names


def test_http_get_pilot_filter_by_status(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Filter Test", service_type="roofing")
    p.set_status(cid, "sent", contacted=True)
    p.add_candidate("New One", service_type="painting")
    status, body = _get("/api/pilot?status=sent")
    assert status == 200
    data = json.loads(body)
    assert all(c["status"] == "sent" for c in data["candidates"])


def test_http_pilot_save_draft(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Draft Save", service_type="fencing")
    status, body = _post(
        f"/api/pilot/{cid}/save-draft", {"draft": "Hey, quick question about your fencing work."}
    )
    assert status == 200
    c = p.get_candidate_by_id(cid)
    assert c["outreach_draft"] == "Hey, quick question about your fencing work."
    assert c["status"] == "drafted"


def test_http_pilot_save_draft_empty(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Empty Draft", service_type="roofing")
    status, body = _post(f"/api/pilot/{cid}/save-draft", {"draft": ""})
    assert status == 400


def test_http_pilot_save_and_approve(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Approve Test", service_type="lawn care")
    status, body = _post(
        f"/api/pilot/{cid}/save-and-approve",
        {"draft": "Hi, I saw your lawn care work on Nextdoor."},
    )
    assert status == 200
    c = p.get_candidate_by_id(cid)
    assert c["status"] == "approved"
    assert c["outreach_draft"] is not None


def test_http_pilot_approve_without_draft(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("No Draft", service_type="roofing")
    status, body = _post(f"/api/pilot/{cid}/approve", {})
    assert status == 400
    assert "draft" in body.get("error", "").lower()


def test_http_pilot_approve_with_draft(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Has Draft", service_type="painting")
    p.set_draft(cid, "My draft message.")
    status, body = _post(f"/api/pilot/{cid}/approve", {})
    assert status == 200
    assert p.get_candidate_by_id(cid)["status"] == "approved"


def test_http_pilot_mark_sent(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Send Test", service_type="gutters")
    p.set_draft(cid, "draft")
    p.set_status(cid, "approved")
    status, body = _post(f"/api/pilot/{cid}/mark-sent", {})
    assert status == 200
    c = p.get_candidate_by_id(cid)
    assert c["status"] == "sent"
    assert c["contacted_at"] is not None


def test_http_pilot_log_reply(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Reply Test", service_type="roofing")
    p.set_status(cid, "sent", contacted=True)
    status, body = _post(f"/api/pilot/{cid}/log-reply", {"reply": "Sure, I'd be interested."})
    assert status == 200
    c = p.get_candidate_by_id(cid)
    assert c["reply_text"] == "Sure, I'd be interested."
    assert c["status"] == "replied"


def test_http_pilot_log_reply_empty(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Empty Reply", service_type="fencing")
    status, body = _post(f"/api/pilot/{cid}/log-reply", {"reply": ""})
    assert status == 400


def test_http_pilot_convert(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Convert Test", service_type="lawn care")
    p.set_status(cid, "replied")
    status, body = _post(f"/api/pilot/{cid}/convert", {})
    assert status == 200
    assert p.get_candidate_by_id(cid)["status"] == "converted"


def test_http_pilot_pass(web_server):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Pass Test", service_type="painting")
    status, body = _post(f"/api/pilot/{cid}/pass", {})
    assert status == 200
    assert p.get_candidate_by_id(cid)["status"] == "passed"


def test_http_pilot_not_found(web_server):
    status, body = _post("/api/pilot/99999/approve", {})
    assert status == 404
