"""
tests/test_reports_digest_paid.py - Tests for Items 13, 14, 15.

Covers:
- Reporting: /api/reports, get_report_stats, conversion rate, zero-lead edge case
- Digest: send_followup_digest, no email when no overdue, --send-digests CLI
- Paid-with-amount: actual_amount stored, NULL allowed, _lead_to_dict includes it,
  get_closed_summary uses actual_amount over quote_amount
"""

import json
import os
from datetime import datetime, timedelta

import bcrypt
import pytest

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
    """A test client logged in as a verified user."""
    from leadclaw.web import limiter

    limiter.reset()
    email = "reports@example.com"
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user(email, pw_hash, "tok")
    verify_user_email(uid)
    client.post("/login", data={"email": email, "password": "password123"})
    client._test_user_id = uid
    return client


# ===========================================================================
# Item 15: Mark-Paid-With-Amount
# ===========================================================================


def test_mark_paid_with_actual_amount():
    """mark_paid with actual_amount should store the value."""
    lead_id, _ = queries.add_lead("Test", "mowing", user_id=1)
    queries.mark_paid(lead_id, actual_amount=350.00, user_id=1)
    lead = queries.get_lead_by_id(lead_id, user_id=1)
    assert lead["actual_amount"] == 350.00
    assert lead["status"] == "paid"


def test_mark_paid_without_amount_leaves_null():
    """mark_paid without actual_amount should leave it NULL."""
    lead_id, _ = queries.add_lead("Test", "mowing", user_id=1)
    queries.mark_paid(lead_id, user_id=1)
    lead = queries.get_lead_by_id(lead_id, user_id=1)
    assert lead["actual_amount"] is None


def test_lead_to_dict_includes_actual_amount():
    """_lead_to_dict should include actual_amount field."""
    from leadclaw.web import _lead_to_dict

    lead_id, _ = queries.add_lead("Test", "mowing", user_id=1)
    queries.mark_paid(lead_id, actual_amount=500.00, user_id=1)
    lead = queries.get_lead_by_id(lead_id, user_id=1)
    d = _lead_to_dict(lead)
    assert d["actual_amount"] == 500.00


def test_lead_to_dict_actual_amount_null():
    """_lead_to_dict should include actual_amount as None when not set."""
    from leadclaw.web import _lead_to_dict

    lead_id, _ = queries.add_lead("Test", "mowing", user_id=1)
    lead = queries.get_lead_by_id(lead_id, user_id=1)
    d = _lead_to_dict(lead)
    assert d["actual_amount"] is None


def test_closed_summary_uses_actual_amount():
    """get_closed_summary should use actual_amount over quote_amount when available."""
    lead_id, _ = queries.add_lead("Test", "mowing", user_id=1)
    queries.update_quote(lead_id, 400.00, user_id=1)
    queries.mark_paid(lead_id, actual_amount=500.00, user_id=1)
    closed, _ = queries.get_closed_summary(user_id=1)
    # Find the paid row
    paid_row = next((r for r in closed if r["status"] == "paid"), None)
    assert paid_row is not None
    assert paid_row["total"] == 500.00


def test_closed_summary_falls_back_to_quote():
    """get_closed_summary should fall back to quote_amount when actual_amount is NULL."""
    lead_id, _ = queries.add_lead("Test", "mowing", user_id=1)
    queries.update_quote(lead_id, 300.00, user_id=1)
    queries.mark_paid(lead_id, user_id=1)
    closed, _ = queries.get_closed_summary(user_id=1)
    paid_row = next((r for r in closed if r["status"] == "paid"), None)
    assert paid_row is not None
    assert paid_row["total"] == 300.00


def test_api_paid_with_actual_amount(auth_client):
    """POST /api/leads/<id>/paid should accept actual_amount."""
    lead_id, _ = queries.add_lead("Test", "mowing", user_id=auth_client._test_user_id)
    queries.update_quote(lead_id, 200.00, user_id=auth_client._test_user_id)

    r = auth_client.post(
        f"/api/leads/{lead_id}/paid",
        data=json.dumps({"actual_amount": 250.00}),
        content_type="application/json",
    )
    assert r.status_code == 200

    lead = queries.get_lead_by_id(lead_id, user_id=auth_client._test_user_id)
    assert lead["actual_amount"] == 250.00


def test_api_paid_without_amount(auth_client):
    """POST /api/leads/<id>/paid without actual_amount should leave it NULL."""
    lead_id, _ = queries.add_lead("Test", "mowing", user_id=auth_client._test_user_id)

    r = auth_client.post(
        f"/api/leads/{lead_id}/paid",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert r.status_code == 200

    lead = queries.get_lead_by_id(lead_id, user_id=auth_client._test_user_id)
    assert lead["actual_amount"] is None


def test_api_paid_negative_amount_rejected(auth_client):
    """POST /api/leads/<id>/paid with negative actual_amount should return 400."""
    lead_id, _ = queries.add_lead("Test", "mowing", user_id=auth_client._test_user_id)

    r = auth_client.post(
        f"/api/leads/{lead_id}/paid",
        data=json.dumps({"actual_amount": -50}),
        content_type="application/json",
    )
    assert r.status_code == 400


# ===========================================================================
# Item 13: Reporting
# ===========================================================================


def test_report_stats_empty_db():
    """get_report_stats should return zeros for empty date range."""
    now = datetime.utcnow()
    start = now.replace(day=1).strftime("%Y-%m-%d")
    end = (now.replace(day=28) + timedelta(days=4)).replace(day=1).strftime("%Y-%m-%d")
    stats = queries.get_report_stats(1, start, end)
    assert stats["jobs_completed"] == 0
    assert stats["revenue_closed"] == 0.0
    assert stats["leads_created"] == 0
    assert stats["conversion_rate"] == 0.0


def test_report_stats_with_data():
    """get_report_stats should return correct counts and revenue."""
    # Create and complete some leads
    lid1, _ = queries.add_lead("Alice", "mowing", user_id=1)
    queries.update_quote(lid1, 200.00, user_id=1)
    queries.mark_paid(lid1, actual_amount=250.00, user_id=1)

    lid2, _ = queries.add_lead("Bob", "landscaping", user_id=1)
    queries.mark_completed(lid2, user_id=1)

    lid3, _ = queries.add_lead("Charlie", "cleanup", user_id=1)  # still new

    now = datetime.utcnow()
    start = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    stats = queries.get_report_stats(1, start, end)
    assert stats["jobs_completed"] == 2  # Alice (paid) + Bob (completed)
    assert stats["revenue_closed"] == 250.00  # only Alice has revenue
    assert stats["leads_created"] == 3
    assert stats["conversion_rate"] == round(2 / 3 * 100, 1)


def test_report_stats_no_division_by_zero():
    """Conversion rate should be 0.0 when no leads exist (no division by zero)."""
    stats = queries.get_report_stats(1, "2000-01-01", "2000-02-01")
    assert stats["conversion_rate"] == 0.0


def test_report_stats_all_time():
    """get_report_stats_all_time should include average_deal_size."""
    lid1, _ = queries.add_lead("A", "mowing", user_id=1)
    queries.mark_paid(lid1, actual_amount=300.00, user_id=1)

    lid2, _ = queries.add_lead("B", "mowing", user_id=1)
    queries.mark_paid(lid2, actual_amount=500.00, user_id=1)

    stats = queries.get_report_stats_all_time(1)
    assert stats["jobs_completed"] == 2
    assert stats["revenue_closed"] == 800.00
    assert stats["average_deal_size"] == 400.00


def test_report_stats_dont_leak_across_users():
    """Report stats for user 1 should not include user 2's data."""
    pw_hash = bcrypt.hashpw(b"pass", bcrypt.gensalt()).decode()
    uid2 = create_user("user2@test.com", pw_hash, "t2")

    lid1, _ = queries.add_lead("User1Lead", "mowing", user_id=1)
    queries.mark_paid(lid1, actual_amount=100.00, user_id=1)

    lid2, _ = queries.add_lead("User2Lead", "mowing", user_id=uid2)
    queries.mark_paid(lid2, actual_amount=200.00, user_id=uid2)

    stats1 = queries.get_report_stats_all_time(1)
    stats2 = queries.get_report_stats_all_time(uid2)

    assert stats1["revenue_closed"] == 100.00
    assert stats2["revenue_closed"] == 200.00


def test_api_reports_returns_json(auth_client):
    """/api/reports should return valid JSON with this_month, last_month, all_time."""
    r = auth_client.get("/api/reports")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "this_month" in data
    assert "last_month" in data
    assert "all_time" in data
    # Verify structure
    for key in ("this_month", "last_month", "all_time"):
        assert "jobs_completed" in data[key]
        assert "revenue_closed" in data[key]
        assert "conversion_rate" in data[key]
    assert "average_deal_size" in data["all_time"]


def test_api_reports_requires_auth(client):
    """/api/reports should require authentication."""
    r = client.get("/api/reports")
    assert r.status_code == 302  # redirect to login


# ===========================================================================
# Item 14: Follow-Up Digest
# ===========================================================================


def test_send_followup_digest_sends_when_overdue():
    """send_followup_digest should return True when there are overdue leads."""
    from leadclaw.web import send_followup_digest

    # Create a lead with overdue follow-up
    lid, _ = queries.add_lead("Overdue", "mowing", phone="555-1234", user_id=1)
    from leadclaw.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET follow_up_after = date('now', '-2 days') WHERE id = ?",
            (lid,),
        )
    # Set the default user's email to something real
    with get_conn() as conn:
        conn.execute("UPDATE users SET email = 'owner@test.com' WHERE id = 1")

    result = send_followup_digest(1)
    assert result is True


def test_send_followup_digest_no_overdue():
    """send_followup_digest should return False when no leads are overdue."""
    from leadclaw.web import send_followup_digest

    # Create a lead with future follow-up
    queries.add_lead("Future", "mowing", user_id=1)

    result = send_followup_digest(1)
    assert result is False


def test_send_followup_digest_excludes_closed_statuses():
    """Paid/completed/lost leads should not appear in digest."""
    from leadclaw.web import send_followup_digest

    lid1, _ = queries.add_lead("Paid", "mowing", user_id=1)
    queries.mark_paid(lid1, user_id=1)
    lid2, _ = queries.add_lead("Lost", "mowing", user_id=1)
    queries.mark_lost(lid2, "no_response", user_id=1)

    from leadclaw.db import get_conn

    # Set both to overdue follow-up — but they should still be excluded
    with get_conn() as conn:
        conn.execute("UPDATE leads SET follow_up_after = date('now', '-2 days')")
        conn.execute("UPDATE users SET email = 'owner@test.com' WHERE id = 1")

    result = send_followup_digest(1)
    assert result is False


def test_send_followup_digest_skips_notifications_off():
    """send_followup_digest should skip users with notifications disabled."""
    from leadclaw.web import send_followup_digest

    lid, _ = queries.add_lead("Overdue", "mowing", user_id=1)
    from leadclaw.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET follow_up_after = date('now', '-2 days') WHERE id = ?", (lid,)
        )
        conn.execute(
            "UPDATE users SET email = 'owner@test.com', notify_new_requests = 0 WHERE id = 1"
        )

    result = send_followup_digest(1)
    assert result is False


def test_get_overdue_followups():
    """get_overdue_followups should return overdue leads excluding closed statuses."""
    lid1, _ = queries.add_lead("Overdue1", "mowing", user_id=1)
    lid2, _ = queries.add_lead("Overdue2", "mowing", user_id=1)
    lid3, _ = queries.add_lead("PaidOverdue", "mowing", user_id=1)
    queries.mark_paid(lid3, user_id=1)

    from leadclaw.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET follow_up_after = date('now', '-1 days') WHERE id IN (?, ?, ?)",
            (lid1, lid2, lid3),
        )

    overdue = queries.get_overdue_followups(1)
    ids = [r["id"] for r in overdue]
    assert lid1 in ids
    assert lid2 in ids
    assert lid3 not in ids  # paid — excluded


def test_run_send_digests():
    """_run_send_digests should process all users."""
    from leadclaw.web import _run_send_digests

    pw_hash = bcrypt.hashpw(b"pass", bcrypt.gensalt()).decode()
    uid = create_user("digest@test.com", pw_hash, "dtok")
    verify_user_email(uid)

    # Give this user an overdue lead
    lid, _ = queries.add_lead("OverdueDigest", "mowing", user_id=uid)
    from leadclaw.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET follow_up_after = date('now', '-1 days') WHERE id = ?", (lid,)
        )

    sent = _run_send_digests()
    assert sent >= 1


# ===========================================================================
# CSV export includes actual_amount
# ===========================================================================


def test_csv_export_includes_actual_amount():
    """CSV export fields should include actual_amount."""
    # Just verify the field is in the export field list
    import importlib

    from leadclaw import commands

    importlib.reload(commands)  # ensure latest

    # Find cmd_export and check its fields
    import inspect

    source = inspect.getsource(commands.cmd_export)
    assert "actual_amount" in source
