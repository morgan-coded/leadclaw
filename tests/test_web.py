"""
tests/test_web.py - Web dashboard API + HTML structure tests

Uses Flask test client (no live HTTP server thread needed).
Auth is bypassed by creating a user and logging in through the test client.
"""

import json
import os

import pytest

from leadclaw import db, queries
from leadclaw.config import MAX_NAME_LENGTH
from leadclaw.web import DASHBOARD_HTML, api_closed, api_summary, app
from tests.conftest import TEST_DB

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_client(client):
    """A test client already logged in as a verified user."""
    import bcrypt

    from leadclaw.db import create_user, verify_user_email

    email = "test@example.com"
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    token = "test-verify-token"
    user_id = create_user(email, pw_hash, token)
    verify_user_email(user_id)

    # Log in
    client.post("/login", data={"email": email, "password": "password123"})
    # Attach user_id so tests that bypass HTTP can still reference it
    client._test_user_id = user_id
    return client


# ---------------------------------------------------------------------------
# api_summary / api_closed unit tests (no HTTP) — use user_id=1 default
# ---------------------------------------------------------------------------


def test_api_summary_empty_db():
    data = api_summary(user_id=1)
    assert "pipeline" in data and "today" in data and "stale" in data and "active" in data
    assert data["pipeline"]["open_value"] == 0


def test_api_summary_with_leads():
    queries.add_lead("Web Test", "roofing", phone="555-1111", user_id=1)
    id2, _ = queries.add_lead("Quoted Lead", "painting", user_id=1)
    queries.update_quote(id2, 1500.0)
    data = api_summary(user_id=1)
    assert data["pipeline"]["open_value"] > 0
    names = [lead["name"] for lead in data["active"]]
    assert "Web Test" in names and "Quoted Lead" in names


def test_api_summary_lead_fields():
    queries.add_lead(
        "Field Check", "fencing", phone="555-9999", email="a@b.com", notes="test", user_id=1
    )
    data = api_summary(user_id=1)
    lead = next(row for row in data["active"] if row["name"] == "Field Check")
    assert lead["phone"] == "555-9999"
    assert lead["email"] == "a@b.com"
    assert lead["notes"] == "test"
    assert "id" in lead and "follow_up_after" in lead


def test_api_closed_empty():
    data = api_closed(user_id=1)
    assert data["closed"] == []


def test_api_closed_contains_won_and_lost():
    id1, _ = queries.add_lead("Won Lead", "roofing", user_id=1)
    id2, _ = queries.add_lead("Lost Lead", "painting", user_id=1)
    id3, _ = queries.add_lead("Active Lead", "gutters", user_id=1)
    queries.mark_won(id1)
    queries.mark_lost(id2, "price")
    data = api_closed(user_id=1)
    names = [row["name"] for row in data["closed"]]
    assert "Won Lead" in names
    assert "Lost Lead" in names
    assert "Active Lead" not in names


def test_api_closed_includes_lost_reason():
    id1, _ = queries.add_lead("Lost With Reason", "painting", user_id=1)
    queries.mark_lost(id1, "price")
    data = api_closed(user_id=1)
    lead = next(row for row in data["closed"] if row["name"] == "Lost With Reason")
    assert lead["lost_reason"] == "price"


# ---------------------------------------------------------------------------
# HTTP GET tests (auth_client)
# ---------------------------------------------------------------------------


def test_http_dashboard_200(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    assert b"LeadClaw" in r.data


def test_http_redirects_to_login_unauthenticated(client):
    r = client.get("/")
    assert r.status_code in (302, 308)
    assert b"login" in r.headers["Location"].lower().encode()


def test_http_api_summary_json(auth_client):
    r = auth_client.get("/api/summary")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "pipeline" in data


def test_http_api_closed(auth_client):
    r = auth_client.get("/api/closed")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "closed" in data


def test_http_404(auth_client):
    r = auth_client.get("/nonexistent")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Helpers for auth_client
# ---------------------------------------------------------------------------


def _post(client, path, data=None):
    body = json.dumps(data or {}).encode()
    r = client.post(path, data=body, content_type="application/json")
    return r.status_code, json.loads(r.data)


# ---------------------------------------------------------------------------
# POST /api/leads — add lead
# ---------------------------------------------------------------------------


def test_post_add_lead_valid(auth_client):
    status, body = _post(auth_client, "/api/leads", {"name": "HTTP Add", "service": "gutters"})
    assert status == 201
    assert "id" in body
    lead = queries.get_lead_by_id(body["id"])
    assert lead is not None and lead["name"] == "HTTP Add"


def test_post_add_lead_missing_fields(auth_client):
    status, body = _post(auth_client, "/api/leads", {"name": "No Service"})
    assert status == 400 and "error" in body


def test_post_add_lead_empty_body(auth_client):
    status, body = _post(auth_client, "/api/leads", {})
    assert status == 400


def test_post_add_lead_name_too_long(auth_client):
    status, body = _post(
        auth_client, "/api/leads", {"name": "A" * (MAX_NAME_LENGTH + 1), "service": "roofing"}
    )
    assert status == 400
    assert "name" in body.get("error", "")


def test_post_add_lead_invalid_email(auth_client):
    status, body = _post(
        auth_client,
        "/api/leads",
        {"name": "Bad Email", "service": "painting", "email": "notanemail"},
    )
    assert status == 400
    assert "email" in body.get("error", "")


def test_post_add_lead_duplicate_warning(auth_client):
    queries.add_lead("Dup Name", "roofing", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, "/api/leads", {"name": "Dup Name", "service": "painting"})
    assert status == 201
    assert "duplicates" in body
    assert len(body["duplicates"]) >= 1


def test_post_add_lead_no_duplicate_warning_for_unique(auth_client):
    status, body = _post(
        auth_client, "/api/leads", {"name": "Unique XYZ 999", "service": "gutters"}
    )
    assert status == 201
    assert not body.get("duplicates")


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/edit
# ---------------------------------------------------------------------------


def test_post_edit_lead(auth_client):
    lead_id, _ = queries.add_lead("Edit Me", "painting", user_id=auth_client._test_user_id)
    status, body = _post(
        auth_client, f"/api/leads/{lead_id}/edit", {"phone": "555-7777", "notes": "updated"}
    )
    assert status == 200 and body.get("ok")
    lead = queries.get_lead_by_id(lead_id)
    assert lead["phone"] == "555-7777" and lead["notes"] == "updated"


def test_post_edit_lead_invalid_email(auth_client):
    lead_id, _ = queries.add_lead("Edit Email Bad", "roofing", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, f"/api/leads/{lead_id}/edit", {"email": "bademail"})
    assert status == 400
    assert "email" in body.get("error", "")


def test_post_edit_lead_invalid_date(auth_client):
    lead_id, _ = queries.add_lead("Edit Date Bad", "fencing", user_id=auth_client._test_user_id)
    status, body = _post(
        auth_client, f"/api/leads/{lead_id}/edit", {"follow_up_after": "not-a-date"}
    )
    assert status == 400
    assert "follow_up_after" in body.get("error", "")


def test_post_edit_lead_valid_date(auth_client):
    lead_id, _ = queries.add_lead("Edit Date OK", "painting", user_id=auth_client._test_user_id)
    status, body = _post(
        auth_client, f"/api/leads/{lead_id}/edit", {"follow_up_after": "2026-12-31"}
    )
    assert status == 200 and body.get("ok")


def test_post_edit_name_too_long(auth_client):
    lead_id, _ = queries.add_lead("Edit Name", "roofing", user_id=auth_client._test_user_id)
    status, body = _post(
        auth_client, f"/api/leads/{lead_id}/edit", {"name": "X" * (MAX_NAME_LENGTH + 1)}
    )
    assert status == 400
    assert "name" in body.get("error", "")


def test_post_edit_lead_not_found(auth_client):
    status, body = _post(auth_client, "/api/leads/99999/edit", {"phone": "555-0000"})
    assert status == 404


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/quote
# ---------------------------------------------------------------------------


def test_post_quote_valid(auth_client):
    lead_id, _ = queries.add_lead("Quote Me", "roofing", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, f"/api/leads/{lead_id}/quote", {"amount": 1200})
    assert status == 200
    lead = queries.get_lead_by_id(lead_id)
    assert lead["quote_amount"] == 1200.0 and lead["status"] == "quoted"


def test_post_quote_negative(auth_client):
    lead_id, _ = queries.add_lead("Bad Quote", "fencing", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, f"/api/leads/{lead_id}/quote", {"amount": -50})
    assert status == 400


def test_post_quote_missing_amount(auth_client):
    lead_id, _ = queries.add_lead("No Amount", "fencing", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, f"/api/leads/{lead_id}/quote", {})
    assert status == 400


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/won
# ---------------------------------------------------------------------------


def test_post_won(auth_client):
    lead_id, _ = queries.add_lead("Win Me", "lawn care", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, f"/api/leads/{lead_id}/won")
    assert status == 200
    assert queries.get_lead_by_id(lead_id)["status"] == "won"


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/lost
# ---------------------------------------------------------------------------


def test_post_lost_valid(auth_client):
    lead_id, _ = queries.add_lead("Lose Me", "pressure washing", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, f"/api/leads/{lead_id}/lost", {"reason": "price"})
    assert status == 200
    lead = queries.get_lead_by_id(lead_id)
    assert lead["status"] == "lost" and lead["lost_reason"] == "price"


def test_post_lost_other_requires_notes(auth_client):
    lead_id, _ = queries.add_lead("Other Lost", "cleaning", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, f"/api/leads/{lead_id}/lost", {"reason": "other"})
    assert status == 400


def test_post_lost_other_with_notes(auth_client):
    lead_id, _ = queries.add_lead("Other OK", "painting", user_id=auth_client._test_user_id)
    status, body = _post(
        auth_client, f"/api/leads/{lead_id}/lost", {"reason": "other", "notes": "some reason"}
    )
    assert status == 200


def test_post_lost_invalid_reason(auth_client):
    lead_id, _ = queries.add_lead("Bad Reason", "roofing", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, f"/api/leads/{lead_id}/lost", {"reason": "bad_reason"})
    assert status == 400


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/delete
# ---------------------------------------------------------------------------


def test_post_delete(auth_client):
    lead_id, _ = queries.add_lead("Delete Me", "fencing", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, f"/api/leads/{lead_id}/delete")
    assert status == 200
    assert queries.get_lead_by_id(lead_id) is None


def test_post_delete_not_found(auth_client):
    status, body = _post(auth_client, "/api/leads/99999/delete")
    assert status == 404


# ---------------------------------------------------------------------------
# HTML structure tests (browser UI assertions without a browser)
# ---------------------------------------------------------------------------


def test_html_contains_add_button():
    assert "openAdd()" in DASHBOARD_HTML
    assert "+ Add Lead" in DASHBOARD_HTML


def test_html_contains_all_modals():
    # Sheets replaced modals in the mobile-first UI
    assert 'id="sheet-edit"' in DASHBOARD_HTML
    assert 'id="sheet-quote"' in DASHBOARD_HTML
    assert 'id="sheet-lost"' in DASHBOARD_HTML


def test_html_contains_closed_tab():
    # Closed is now under the 'more' tab
    assert "switchTab('more')" in DASHBOARD_HTML
    assert 'id="tab-more"' in DASHBOARD_HTML
    assert 'id="closed"' in DASHBOARD_HTML


def test_html_renders_active_lead_actions():
    assert "openQuote(" in DASHBOARD_HTML
    assert "openEdit(" in DASHBOARD_HTML
    assert "openLost(" in DASHBOARD_HTML
    assert "doDelete(" in DASHBOARD_HTML
    # Send Quote is the primary CTA for new/quoted/followup_due leads
    assert "Send Quote" in DASHBOARD_HTML


def test_html_closed_leads_delete_only():
    # Closed/paid/won/lost leads show delete only — check the status list used for this
    assert "doDelete(" in DASHBOARD_HTML


def test_html_duplicate_warning_element():
    assert 'id="dup-warn"' in DASHBOARD_HTML
    assert "duplicates" in DASHBOARD_HTML


def test_html_client_validation_email():
    assert "validEmail" in DASHBOARD_HTML


def test_html_client_validation_date():
    assert "validDate" in DASHBOARD_HTML


def test_html_lost_reasons_injected():
    assert "LOST_REASONS=" in DASHBOARD_HTML.replace(" ", "")
    assert "price" in DASHBOARD_HTML


def test_html_max_name_injected():
    assert f"MAX_NAME={MAX_NAME_LENGTH}" in DASHBOARD_HTML.replace(" ", "")


def test_html_api_closed_fetch():
    assert "/api/closed" in DASHBOARD_HTML


def test_html_pilot_tab_present():
    # Pilot is now under the 'more' tab
    assert "switchTab('more')" in DASHBOARD_HTML
    assert 'id="tab-more"' in DASHBOARD_HTML


def test_html_pilot_table_columns():
    assert 'id="pilot-table"' in DASHBOARD_HTML
    assert "Score" in DASHBOARD_HTML
    assert "Source" in DASHBOARD_HTML
    assert "Follow-up" in DASHBOARD_HTML
    assert "Reply" in DASHBOARD_HTML


def test_html_pilot_action_buttons():
    assert "openPilotDraft" in DASHBOARD_HTML
    assert "pilotAction" in DASHBOARD_HTML
    assert "openPilotReply" in DASHBOARD_HTML
    assert "save-and-approve" in DASHBOARD_HTML
    assert "mark-sent" in DASHBOARD_HTML
    assert "log-reply" in DASHBOARD_HTML


def test_html_pilot_modals():
    # Sheets replaced modals in the mobile-first UI
    assert 'id="sheet-pilot-draft"' in DASHBOARD_HTML
    assert 'id="sheet-pilot-reply"' in DASHBOARD_HTML


def test_html_pilot_status_filter():
    assert 'id="pilot-filter"' in DASHBOARD_HTML


def test_html_signout_link():
    """Dashboard must contain a sign-out link."""
    assert "/logout" in DASHBOARD_HTML
    # Sign out label (case may vary with mobile-first UI)
    assert (
        "sign-out" in DASHBOARD_HTML.lower()
        or "sign out" in DASHBOARD_HTML.lower()
        or "logout" in DASHBOARD_HTML.lower()
    )


# ---------------------------------------------------------------------------
# Pilot API HTTP tests
# ---------------------------------------------------------------------------


def test_http_get_pilot_empty(auth_client):
    r = auth_client.get("/api/pilot")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "candidates" in data and "summary" in data
    assert data["candidates"] == []


def test_http_get_pilot_with_candidates(auth_client):
    from leadclaw import pilot as p

    p.add_candidate(
        "Pilot Web Test",
        service_type="lawn care",
        phone="555-8888",
        user_id=auth_client._test_user_id,
    )
    r = auth_client.get("/api/pilot")
    assert r.status_code == 200
    data = json.loads(r.data)
    names = [c["name"] for c in data["candidates"]]
    assert "Pilot Web Test" in names


def test_http_get_pilot_filter_by_status(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate(
        "Filter Test", service_type="roofing", user_id=auth_client._test_user_id
    )
    p.set_status(cid, "sent", contacted=True)
    p.add_candidate("New One", service_type="painting", user_id=auth_client._test_user_id)
    r = auth_client.get("/api/pilot?status=sent")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert all(c["status"] == "sent" for c in data["candidates"])


def test_http_pilot_save_draft(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate(
        "Draft Save", service_type="fencing", user_id=auth_client._test_user_id
    )
    status, body = _post(
        auth_client,
        f"/api/pilot/{cid}/save-draft",
        {"draft": "Hey, quick question about your fencing work."},
    )
    assert status == 200
    c = p.get_candidate_by_id(cid)
    assert c["outreach_draft"] == "Hey, quick question about your fencing work."
    assert c["status"] == "drafted"


def test_http_pilot_save_draft_empty(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate(
        "Empty Draft", service_type="roofing", user_id=auth_client._test_user_id
    )
    status, body = _post(auth_client, f"/api/pilot/{cid}/save-draft", {"draft": ""})
    assert status == 400


def test_http_pilot_save_and_approve(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate(
        "Approve Test", service_type="lawn care", user_id=auth_client._test_user_id
    )
    status, body = _post(
        auth_client,
        f"/api/pilot/{cid}/save-and-approve",
        {"draft": "Hi, I saw your lawn care work on Nextdoor."},
    )
    assert status == 200
    c = p.get_candidate_by_id(cid)
    assert c["status"] == "approved"
    assert c["outreach_draft"] is not None


def test_http_pilot_approve_without_draft(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("No Draft", service_type="roofing", user_id=auth_client._test_user_id)
    status, body = _post(auth_client, f"/api/pilot/{cid}/approve", {})
    assert status == 400
    assert "draft" in body.get("error", "").lower()


def test_http_pilot_approve_with_draft(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate(
        "Has Draft", service_type="painting", user_id=auth_client._test_user_id
    )
    p.set_draft(cid, "My draft message.")
    status, body = _post(auth_client, f"/api/pilot/{cid}/approve", {})
    assert status == 200
    assert p.get_candidate_by_id(cid)["status"] == "approved"


def test_http_pilot_mark_sent(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate("Send Test", service_type="gutters", user_id=auth_client._test_user_id)
    p.set_draft(cid, "draft")
    p.set_status(cid, "approved")
    status, body = _post(auth_client, f"/api/pilot/{cid}/mark-sent", {})
    assert status == 200
    c = p.get_candidate_by_id(cid)
    assert c["status"] == "sent"
    assert c["contacted_at"] is not None


def test_http_pilot_log_reply(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate(
        "Reply Test", service_type="roofing", user_id=auth_client._test_user_id
    )
    p.set_status(cid, "sent", contacted=True)
    status, body = _post(
        auth_client, f"/api/pilot/{cid}/log-reply", {"reply": "Sure, I'd be interested."}
    )
    assert status == 200
    c = p.get_candidate_by_id(cid)
    assert c["reply_text"] == "Sure, I'd be interested."
    assert c["status"] == "replied"


def test_http_pilot_log_reply_empty(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate(
        "Empty Reply", service_type="fencing", user_id=auth_client._test_user_id
    )
    status, body = _post(auth_client, f"/api/pilot/{cid}/log-reply", {"reply": ""})
    assert status == 400


def test_http_pilot_convert(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate(
        "Convert Test", service_type="lawn care", user_id=auth_client._test_user_id
    )
    p.set_status(cid, "replied")
    status, body = _post(auth_client, f"/api/pilot/{cid}/convert", {})
    assert status == 200
    assert p.get_candidate_by_id(cid)["status"] == "converted"


def test_http_pilot_pass(auth_client):
    from leadclaw import pilot as p

    cid, _ = p.add_candidate(
        "Pass Test", service_type="painting", user_id=auth_client._test_user_id
    )
    status, body = _post(auth_client, f"/api/pilot/{cid}/pass", {})
    assert status == 200
    assert p.get_candidate_by_id(cid)["status"] == "passed"


def test_http_pilot_not_found(auth_client):
    status, body = _post(auth_client, "/api/pilot/99999/approve", {})
    assert status == 404


# ---------------------------------------------------------------------------
# Auth flow tests
# ---------------------------------------------------------------------------


def test_signup_creates_user(client):
    r = client.post(
        "/signup",
        data={
            "email": "new@example.com",
            "password": "securepass123",
            "confirm": "securepass123",
        },
        follow_redirects=False,
    )
    # Auto-verify active (email delivery bypassed for pilot phase) — redirects to dashboard
    assert r.status_code == 302
    row = db.get_user_by_email("new@example.com")
    assert row is not None
    assert row["email_verified"] == 1


def test_signup_password_mismatch(client):
    r = client.post(
        "/signup",
        data={
            "email": "bad@example.com",
            "password": "pass1234",
            "confirm": "different",
        },
    )
    assert b"Passwords do not match" in r.data


def test_signup_short_password(client):
    r = client.post(
        "/signup",
        data={
            "email": "short@example.com",
            "password": "abc",
            "confirm": "abc",
        },
    )
    assert b"8 characters" in r.data


def test_signup_duplicate_email(client):
    import bcrypt

    from leadclaw.db import create_user

    pw = bcrypt.hashpw(b"pass1234", bcrypt.gensalt()).decode()
    create_user("dup@example.com", pw, "tok")
    r = client.post(
        "/signup",
        data={
            "email": "dup@example.com",
            "password": "pass1234xx",
            "confirm": "pass1234xx",
        },
    )
    assert b"already exists" in r.data


def test_verify_token_logs_in(client):
    import bcrypt

    from leadclaw.db import create_user

    pw = bcrypt.hashpw(b"pass1234", bcrypt.gensalt()).decode()
    uid = create_user("verify@example.com", pw, "my-secret-token")
    r = client.get("/verify/my-secret-token", follow_redirects=False)
    assert r.status_code == 302
    assert "/" in r.headers["Location"]
    row = db.get_user_by_id(uid)
    assert row["email_verified"] == 1


def test_verify_invalid_token(client):
    r = client.get("/verify/totally-wrong-token")
    assert b"Invalid or expired" in r.data


def test_login_unverified_user_can_login_but_dashboard_blocked(client):
    """User can log in but is redirected to unverified page when accessing dashboard."""
    import bcrypt

    from leadclaw.db import create_user

    pw = bcrypt.hashpw(b"pass1234", bcrypt.gensalt()).decode()
    create_user("unverified@example.com", pw, "some-token")
    client.post("/login", data={"email": "unverified@example.com", "password": "pass1234"})
    r = client.get("/")
    assert r.status_code == 200
    assert b"verify" in r.data.lower() or b"Verify" in r.data


def test_login_wrong_password(client):
    import bcrypt

    from leadclaw.db import create_user, verify_user_email

    pw = bcrypt.hashpw(b"correct", bcrypt.gensalt()).decode()
    uid = create_user("wrongpw@example.com", pw, "tok")
    verify_user_email(uid)
    r = client.post("/login", data={"email": "wrongpw@example.com", "password": "wrong"})
    assert b"Invalid email or password" in r.data


def test_logout_redirects(auth_client):
    r = auth_client.get("/logout", follow_redirects=False)
    assert r.status_code in (302, 308)


# ---------------------------------------------------------------------------
# Public service request form
# ---------------------------------------------------------------------------


class TestPublicRequest:
    _VALID = {
        "name": "Jane Smith",
        "phone": "512-555-0199",
        "email": "jane@example.com",
        "service": "Lawn Mowing",
        "service_address": "123 Oak St, Austin, TX 78701",
        "requested_date": "2026-05-10",
        "requested_time_window": "morning",
        "notes": "Gate code is 1234",
    }

    def test_get_form_renders(self, client):
        r = client.get("/request")
        assert r.status_code == 200
        assert b"Request Service" in r.data
        assert b"name=" in r.data
        assert b"phone" in r.data
        assert b"service_address" in r.data

    def test_valid_submission_creates_lead(self, client):
        r = client.post("/request", data=self._VALID, follow_redirects=True)
        assert r.status_code == 200
        assert b"Request Received" in r.data

        # Verify lead is in DB with correct metadata
        import sqlite3

        conn = sqlite3.connect(os.environ["LEADCLAW_DB"])
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM leads WHERE name = ?", ("Jane Smith",)).fetchone()
        conn.close()

        assert row is not None
        assert row["lead_source"] == "public_request"
        assert row["service_address"] == "123 Oak St, Austin, TX 78701"
        assert row["requested_date"] == "2026-05-10"
        assert row["requested_time_window"] == "morning"
        assert row["phone"] == "512-555-0199"
        assert row["email"] == "jane@example.com"
        assert row["service"] == "Lawn Mowing"
        assert row["status"] == "new"

    def test_missing_name_returns_422(self, client):
        data = {**self._VALID, "name": ""}
        r = client.post("/request", data=data)
        assert r.status_code == 422
        assert b"Name is required" in r.data

    def test_missing_phone_returns_422(self, client):
        data = {**self._VALID, "phone": ""}
        r = client.post("/request", data=data)
        assert r.status_code == 422
        assert b"Phone number is required" in r.data

    def test_missing_address_returns_422(self, client):
        data = {**self._VALID, "service_address": ""}
        r = client.post("/request", data=data)
        assert r.status_code == 422
        assert b"Service address is required" in r.data

    def test_invalid_service_returns_422(self, client):
        data = {**self._VALID, "service": "HackerService"}
        r = client.post("/request", data=data)
        assert r.status_code == 422

    def test_optional_fields_can_be_omitted(self, client):
        data = {
            "name": "Bob",
            "phone": "555-0100",
            "service": "Cleanup",
            "service_address": "456 Elm St",
        }
        r = client.post("/request", data=data, follow_redirects=True)
        assert r.status_code == 200
        assert b"Request Received" in r.data

    def test_no_auth_required_for_request_page(self, client):
        """Public form must not redirect unauthenticated users."""
        r = client.get("/request")
        assert r.status_code == 200

    def test_protected_routes_still_require_auth(self, client):
        """No regression — protected routes still 401/redirect without login."""
        r = client.get("/api/summary", follow_redirects=False)
        assert r.status_code in (302, 401)


# ---------------------------------------------------------------------------
# Anti-spam tests for /request
# ---------------------------------------------------------------------------

class TestPublicRequestAntiSpam:
    """Tests for honeypot, min-time, and rate-limit protections on /request."""

    @pytest.fixture(autouse=True)
    def clear_throttle(self):
        """Reset rate-limit state before/after each test to prevent cross-test pollution."""
        from leadclaw.web import _REQUEST_THROTTLE
        _REQUEST_THROTTLE.clear()
        yield
        _REQUEST_THROTTLE.clear()

    _VALID = {
        "name": "Spam Test User",
        "phone": "512-555-9999",
        "service": "Lawn Mowing",
        "service_address": "999 Test St, Austin TX",
    }

    def test_honeypot_filled_returns_success_without_storing(self, client):
        """If honeypot field is filled, silently return success but don't save lead."""
        import sqlite3
        data = {**self._VALID, "_hp_website": "http://spam.example.com"}
        r = client.post("/request", data=data, follow_redirects=True)
        # Should silently "succeed" (no error to tip off bots)
        assert r.status_code == 200
        # No lead should be saved
        conn = sqlite3.connect(os.environ["LEADCLAW_DB"])
        row = conn.execute(
            "SELECT * FROM leads WHERE name = ?", ("Spam Test User",)
        ).fetchone()
        conn.close()
        assert row is None

    def test_honeypot_empty_allows_submission(self, client):
        """Normal submission with no honeypot field succeeds."""
        data = {**self._VALID, "_hp_website": ""}
        r = client.post("/request", data=data, follow_redirects=True)
        assert r.status_code == 200
        assert b"Request Received" in r.data

    def test_form_includes_honeypot_field(self, client):
        """GET /request should include the hidden honeypot input."""
        r = client.get("/request")
        assert r.status_code == 200
        assert b"_hp_website" in r.data

    def test_form_includes_timestamp_field(self, client):
        """GET /request should include the hidden timestamp field."""
        r = client.get("/request")
        assert b"_form_ts" in r.data

    def test_min_time_check_rejects_instant_submission(self, client):
        """Submission with _form_ts set to 'now' (elapsed < 3s) should be rejected."""
        import time
        data = {**self._VALID, "_form_ts": str(int(time.time()))}
        r = client.post("/request", data=data)
        # Should return an error (422 or re-render form with error message)
        assert r.status_code in (200, 422)
        assert b"Please take a moment" in r.data

    def test_min_time_check_allows_old_timestamp(self, client):
        """Submission with _form_ts set to 10 seconds ago should be allowed."""
        import time
        data = {**self._VALID, "_form_ts": str(int(time.time()) - 10)}
        r = client.post("/request", data=data, follow_redirects=True)
        assert r.status_code == 200
        assert b"Request Received" in r.data

    def test_missing_timestamp_does_not_block(self, client):
        """Submissions with no _form_ts should pass (don't break old behavior)."""
        data = {**self._VALID}  # no _form_ts key at all
        r = client.post("/request", data=data, follow_redirects=True)
        assert r.status_code == 200
        assert b"Request Received" in r.data

    def test_rate_limit_blocks_after_threshold(self, client):
        """Same IP submitting more than the limit in one window gets a 429."""
        import time
        from leadclaw.web import _REQUEST_THROTTLE, _REQUEST_THROTTLE_LIMIT

        # Clear any existing throttle state for this IP
        _REQUEST_THROTTLE.clear()

        old_ts = str(int(time.time()) - 10)  # pass min-time check

        # Submit up to the limit
        for _ in range(_REQUEST_THROTTLE_LIMIT):
            data = {**self._VALID, "_form_ts": old_ts}
            r = client.post("/request", data=data, follow_redirects=True)
            assert r.status_code == 200

        # Next submission should be throttled
        data = {**self._VALID, "_form_ts": old_ts}
        r = client.post("/request", data=data)
        assert r.status_code == 429

    def test_rate_limit_does_not_block_under_threshold(self, client):
        """Submissions under the rate limit should always succeed."""
        import time
        from leadclaw.web import _REQUEST_THROTTLE, _REQUEST_THROTTLE_LIMIT

        _REQUEST_THROTTLE.clear()

        old_ts = str(int(time.time()) - 10)
        for i in range(_REQUEST_THROTTLE_LIMIT - 1):
            data = {**self._VALID, "name": f"User{i}", "_form_ts": old_ts}
            r = client.post("/request", data=data, follow_redirects=True)
            assert r.status_code == 200
