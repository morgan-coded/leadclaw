"""
tests/test_tech_debt.py - Tests for Technical Debt Cleanup Pass.

Covers:
- Item 1: api_closed() uses SQL filtering (already done, verify behavior)
- Item 2: Clearing optional fields via edit API
- Item 3: Pilot score recalculation on field changes
- Item 4: Import/export round-trip consistency
"""

import csv
import io
import json
import os

import bcrypt
import pytest

from leadclaw import pilot as p
from leadclaw import queries
from leadclaw.db import create_user, init_db, verify_user_email
from leadclaw.web import app
from tests.conftest import TEST_DB


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_client(client):
    from leadclaw.web import limiter

    limiter.reset()
    email = "techdebt@example.com"
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user(email, pw_hash, "tok")
    verify_user_email(uid)
    client.post("/login", data={"email": email, "password": "password123"})
    client._test_user_id = uid
    return client


# ===========================================================================
# Item 1: api_closed uses SQL filtering
# ===========================================================================


def test_api_closed_returns_only_closed_statuses(auth_client):
    """api_closed should only return won/lost/paid leads, not new/quoted/etc."""
    uid = auth_client._test_user_id
    lid1, _ = queries.add_lead("Active", "mowing", user_id=uid)
    lid2, _ = queries.add_lead("Won", "mowing", user_id=uid)
    queries.mark_won(lid2, user_id=uid)
    lid3, _ = queries.add_lead("Lost", "mowing", user_id=uid)
    queries.mark_lost(lid3, "price", user_id=uid)

    r = auth_client.get("/api/closed")
    assert r.status_code == 200
    data = json.loads(r.data)
    ids = [lead["id"] for lead in data["closed"]]
    assert lid1 not in ids  # new — excluded
    assert lid2 in ids  # won — included
    assert lid3 in ids  # lost — included


# ===========================================================================
# Item 2: Clearing optional fields on edit
# ===========================================================================


def test_edit_clear_phone(auth_client):
    """Sending phone=null should clear the phone field."""
    uid = auth_client._test_user_id
    lid, _ = queries.add_lead("Test", "mowing", phone="555-1234", user_id=uid)

    r = auth_client.post(
        f"/api/leads/{lid}/edit",
        data=json.dumps({"phone": None}),
        content_type="application/json",
    )
    assert r.status_code == 200

    lead = queries.get_lead_by_id(lid, user_id=uid)
    assert lead["phone"] is None


def test_edit_clear_email(auth_client):
    """Sending email=null should clear the email field."""
    uid = auth_client._test_user_id
    lid, _ = queries.add_lead("Test", "mowing", email="test@example.com", user_id=uid)

    r = auth_client.post(
        f"/api/leads/{lid}/edit",
        data=json.dumps({"email": None}),
        content_type="application/json",
    )
    assert r.status_code == 200

    lead = queries.get_lead_by_id(lid, user_id=uid)
    assert lead["email"] is None


def test_edit_clear_notes(auth_client):
    """Sending notes="" should clear the notes field."""
    uid = auth_client._test_user_id
    lid, _ = queries.add_lead("Test", "mowing", notes="important note", user_id=uid)

    r = auth_client.post(
        f"/api/leads/{lid}/edit",
        data=json.dumps({"notes": ""}),
        content_type="application/json",
    )
    assert r.status_code == 200

    lead = queries.get_lead_by_id(lid, user_id=uid)
    assert lead["notes"] is None


def test_edit_clear_follow_up_after(auth_client):
    """Sending follow_up_after=null should clear the follow-up date."""
    uid = auth_client._test_user_id
    lid, _ = queries.add_lead("Test", "mowing", user_id=uid)

    # First set a follow-up date
    auth_client.post(
        f"/api/leads/{lid}/edit",
        data=json.dumps({"follow_up_after": "2025-12-31"}),
        content_type="application/json",
    )
    lead = queries.get_lead_by_id(lid, user_id=uid)
    assert lead["follow_up_after"] is not None

    # Now clear it
    r = auth_client.post(
        f"/api/leads/{lid}/edit",
        data=json.dumps({"follow_up_after": None}),
        content_type="application/json",
    )
    assert r.status_code == 200

    lead = queries.get_lead_by_id(lid, user_id=uid)
    assert lead["follow_up_after"] is None


def test_edit_omitted_field_not_changed(auth_client):
    """Fields not present in the request body should not be modified."""
    uid = auth_client._test_user_id
    lid, _ = queries.add_lead("Test", "mowing", phone="555-9999", email="keep@test.com", user_id=uid)

    r = auth_client.post(
        f"/api/leads/{lid}/edit",
        data=json.dumps({"notes": "new note"}),
        content_type="application/json",
    )
    assert r.status_code == 200

    lead = queries.get_lead_by_id(lid, user_id=uid)
    assert lead["phone"] == "555-9999"  # unchanged
    assert lead["email"] == "keep@test.com"  # unchanged
    assert lead["notes"] == "new note"


def test_edit_cannot_clear_required_name(auth_client):
    """Sending name=null should not clear the name (name is required)."""
    uid = auth_client._test_user_id
    lid, _ = queries.add_lead("Test", "mowing", user_id=uid)

    r = auth_client.post(
        f"/api/leads/{lid}/edit",
        data=json.dumps({"name": None}),
        content_type="application/json",
    )
    assert r.status_code == 200

    lead = queries.get_lead_by_id(lid, user_id=uid)
    assert lead["name"] == "Test"  # not cleared


# ===========================================================================
# Item 3: Pilot score recalculation
# ===========================================================================


def test_pilot_score_recalc_on_service_type_change():
    """Changing service_type should recalculate the score."""
    cid, _ = p.add_candidate("Jane", service_type="handyman")
    c = p.get_candidate_by_id(cid)
    old_score = c["score"]

    p.update_candidate(cid, service_type="lawn care")
    c = p.get_candidate_by_id(cid)
    assert c["score"] != old_score
    assert c["score"] == p.score_candidate("lawn care", has_phone=False, has_email=False)


def test_pilot_score_recalc_on_phone_added():
    """Adding a phone number should recalculate (increase) the score."""
    cid, _ = p.add_candidate("Jane", service_type="roofing")
    c = p.get_candidate_by_id(cid)
    old_score = c["score"]

    p.update_candidate(cid, phone="555-0001")
    c = p.get_candidate_by_id(cid)
    assert c["score"] == old_score + 10  # phone bonus


def test_pilot_score_recalc_on_email_added():
    """Adding an email should recalculate (increase) the score."""
    cid, _ = p.add_candidate("Jane", service_type="roofing", phone="555-0001")
    c = p.get_candidate_by_id(cid)
    old_score = c["score"]

    p.update_candidate(cid, email="jane@example.com")
    c = p.get_candidate_by_id(cid)
    assert c["score"] == old_score + 5  # email bonus


def test_pilot_score_recalc_on_phone_cleared():
    """Clearing phone should recalculate (decrease) the score."""
    cid, _ = p.add_candidate("Jane", service_type="roofing", phone="555-0001")
    c = p.get_candidate_by_id(cid)
    score_with_phone = c["score"]

    p.update_candidate(cid, phone=None)
    c = p.get_candidate_by_id(cid)
    assert c["score"] == score_with_phone - 10


def test_pilot_score_unchanged_on_irrelevant_update():
    """Updating notes should NOT recalculate the score."""
    cid, _ = p.add_candidate("Jane", service_type="roofing", phone="555-0001")
    c = p.get_candidate_by_id(cid)
    old_score = c["score"]

    p.update_candidate(cid, notes="some new notes")
    c = p.get_candidate_by_id(cid)
    assert c["score"] == old_score


# ===========================================================================
# Item 4: Import/export round-trip
# ===========================================================================


def test_import_actual_amount():
    """import_leads_from_rows should handle actual_amount."""
    rows = [{"name": "Alice", "service": "mowing", "actual_amount": "350.00"}]
    result = queries.import_leads_from_rows(rows)
    assert result["imported"] == 1
    leads = queries.get_all_leads(limit=10)
    assert leads[0]["actual_amount"] == 350.00
    assert leads[0]["status"] == "paid"  # actual_amount implies paid


def test_import_follow_up_after():
    """import_leads_from_rows should handle follow_up_after date."""
    rows = [{"name": "Bob", "service": "mowing", "follow_up_after": "2025-06-15"}]
    result = queries.import_leads_from_rows(rows)
    assert result["imported"] == 1
    leads = queries.get_all_leads(limit=10)
    assert "2025-06-15" in str(leads[0]["follow_up_after"])


def test_import_lost_reason():
    """import_leads_from_rows should handle lost_reason + status."""
    rows = [{"name": "Charlie", "service": "mowing", "status": "lost", "lost_reason": "price"}]
    result = queries.import_leads_from_rows(rows)
    assert result["imported"] == 1
    leads = queries.get_all_leads(limit=10)
    assert leads[0]["status"] == "lost"
    assert leads[0]["lost_reason"] == "price"


def test_import_status_won():
    """import_leads_from_rows should handle status=won."""
    rows = [{"name": "Dave", "service": "mowing", "status": "won"}]
    result = queries.import_leads_from_rows(rows)
    assert result["imported"] == 1
    leads = queries.get_all_leads(limit=10)
    assert leads[0]["status"] == "won"


def test_import_status_paid_without_amount():
    """import_leads_from_rows should handle status=paid without actual_amount."""
    rows = [{"name": "Eve", "service": "mowing", "status": "paid"}]
    result = queries.import_leads_from_rows(rows)
    assert result["imported"] == 1
    leads = queries.get_all_leads(limit=10)
    assert leads[0]["status"] == "paid"
    assert leads[0]["actual_amount"] is None


def test_import_invalid_lost_reason_ignored():
    """Invalid lost_reason should be ignored (not crash)."""
    rows = [{"name": "Frank", "service": "mowing", "status": "lost", "lost_reason": "bogus_reason"}]
    result = queries.import_leads_from_rows(rows)
    assert result["imported"] == 1
    leads = queries.get_all_leads(limit=10)
    # Status stays 'new' since lost_reason was invalid and we need both
    assert leads[0]["status"] == "new"


def test_import_export_round_trip():
    """Exported CSV should be importable and preserve key fields."""
    # Create leads with various statuses and fields
    lid1, _ = queries.add_lead("Alice", "mowing", phone="555-0001", email="alice@test.com")
    queries.update_quote(lid1, 200.00)
    queries.mark_paid(lid1, actual_amount=250.00)

    lid2, _ = queries.add_lead("Bob", "landscaping", notes="big yard")
    queries.mark_lost(lid2, "price")

    lid3, _ = queries.add_lead("Charlie", "cleaning", phone="555-0003")

    # Export
    all_leads = queries.get_all_leads(limit=100)
    fields = [
        "id", "name", "phone", "email", "service", "status",
        "lost_reason", "lost_reason_notes", "quote_amount", "actual_amount",
        "created_at", "last_contact_at", "follow_up_after", "notes",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for lead in all_leads:
        writer.writerow(dict(lead))

    # Clear DB and reimport
    from leadclaw.db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM leads")

    buf.seek(0)
    reader = csv.DictReader(buf)
    rows = list(reader)
    result = queries.import_leads_from_rows(rows)
    assert result["imported"] == 3
    assert result["skipped"] == 0

    # Verify key fields survived the round-trip
    reimported = queries.get_all_leads(limit=100)
    by_name = {r["name"]: dict(r) for r in reimported}

    assert by_name["Alice"]["status"] == "paid"
    assert by_name["Alice"]["actual_amount"] == 250.00
    assert by_name["Alice"]["phone"] == "555-0001"

    assert by_name["Bob"]["status"] == "lost"
    assert by_name["Bob"]["lost_reason"] == "price"

    assert by_name["Charlie"]["phone"] == "555-0003"
    assert by_name["Charlie"]["status"] == "new"


def test_import_negative_actual_amount_ignored():
    """Negative actual_amount should be treated as None."""
    rows = [{"name": "Test", "service": "mowing", "actual_amount": "-50"}]
    result = queries.import_leads_from_rows(rows)
    assert result["imported"] == 1
    leads = queries.get_all_leads(limit=10)
    assert leads[0]["actual_amount"] is None
    assert leads[0]["status"] == "new"


def test_import_invalid_follow_up_after_ignored():
    """Invalid date format for follow_up_after should be ignored."""
    rows = [{"name": "Test", "service": "mowing", "follow_up_after": "not-a-date"}]
    result = queries.import_leads_from_rows(rows)
    assert result["imported"] == 1
