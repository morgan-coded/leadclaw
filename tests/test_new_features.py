"""
tests/test_new_features.py - Tests for product pass features:
1. Reminder dismissal
2. Per-service recurring defaults
3. Pilot usage tracking (event_log)
4. Won vs Paid cleanup
"""

import json
import os

import pytest

from leadclaw.db import get_conn, init_db
from leadclaw.queries import (
    add_lead,
    dismiss_reminder_standalone,
    get_event_counts,
    get_job_today_leads,
    get_lead_by_id,
    get_reactivation_leads,
    get_review_reminders,
    log_event,
    mark_booked,
    mark_completed,
    mark_invoice_sent,
    mark_paid,
    set_next_service,
    update_quote,
)
from leadclaw.service_defaults import (
    DEFAULT_SERVICE_INTERVAL,
    SERVICE_INTERVALS,
    get_service_interval,
)
from tests.conftest import TEST_DB


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


# ---------------------------------------------------------------------------
# Feature 2: Per-service interval helpers
# ---------------------------------------------------------------------------


def test_get_service_interval_known():
    assert get_service_interval("lawn care") == 14
    assert get_service_interval("gutters") == 180
    assert get_service_interval("pressure washing") == 365
    assert get_service_interval("window cleaning") == 90


def test_get_service_interval_case_insensitive():
    assert get_service_interval("Lawn Care") == 14
    assert get_service_interval("GUTTERS") == 180
    assert get_service_interval("Gutter Cleaning") == 180


def test_get_service_interval_unknown_returns_default():
    assert get_service_interval("unicycle repair") == DEFAULT_SERVICE_INTERVAL
    assert get_service_interval("") == DEFAULT_SERVICE_INTERVAL
    assert get_service_interval(None) == DEFAULT_SERVICE_INTERVAL


def test_get_service_interval_partial_match():
    # "gutter cleaning" contains "gutters" keyword — should match
    result = get_service_interval("gutter cleaning service")
    assert result in SERVICE_INTERVALS.values()


def test_mark_paid_auto_fills_next_service_from_service_type():
    """mark_paid should auto-set next_service_due_at based on service type."""
    lead_id, _ = add_lead("Lawn Customer", "lawn care")
    mark_paid(lead_id)
    lead = get_lead_by_id(lead_id)
    assert lead["next_service_due_at"] is not None
    # Lawn care = 14 days; should be roughly 2 weeks out
    assert lead["service_reminder_at"] is not None


def test_mark_paid_auto_fills_generic_service():
    """mark_paid uses DEFAULT_SERVICE_INTERVAL for unknown services."""
    lead_id, _ = add_lead("Generic Customer", "widget repair")
    mark_paid(lead_id)
    lead = get_lead_by_id(lead_id)
    assert lead["next_service_due_at"] is not None


def test_mark_paid_does_not_override_existing_next_service():
    """mark_paid should NOT override an explicitly set next_service_due_at."""
    lead_id, _ = add_lead("Service Cust", "pressure washing")
    # Set explicit next service BEFORE marking paid
    set_next_service(lead_id, "2030-12-31")
    mark_paid(lead_id)
    lead = get_lead_by_id(lead_id)
    assert lead["next_service_due_at"] == "2030-12-31"


def test_mark_completed_auto_fills_next_service():
    """mark_completed should auto-set next_service_due_at if not already set."""
    lead_id, _ = add_lead("Complete Test", "lawn care")
    mark_booked(lead_id, "2026-06-01")
    mark_completed(lead_id)
    lead = get_lead_by_id(lead_id)
    assert lead["next_service_due_at"] is not None


def test_mark_completed_does_not_override_existing_next_service():
    """mark_completed should NOT override an existing next_service_due_at."""
    lead_id, _ = add_lead("Complete No Override", "gutters")
    set_next_service(lead_id, "2030-01-01")
    mark_booked(lead_id, "2026-06-01")
    mark_completed(lead_id)
    lead = get_lead_by_id(lead_id)
    assert lead["next_service_due_at"] == "2030-01-01"


# ---------------------------------------------------------------------------
# Feature 3: Pilot usage tracking (event_log)
# ---------------------------------------------------------------------------


def test_log_event_basic():
    with get_conn() as conn:
        log_event(conn, "test_event", user_id=1, lead_id=42, meta={"key": "value"})
    counts = get_event_counts()
    types = [r["event_type"] for r in counts]
    assert "test_event" in types


def test_log_event_meta_stored_as_json():
    with get_conn() as conn:
        log_event(conn, "meta_test", user_id=1, meta={"action": "test", "count": 5})
    with get_conn() as conn:
        row = conn.execute("SELECT meta FROM event_log WHERE event_type = 'meta_test'").fetchone()
    assert row is not None
    meta = json.loads(row["meta"])
    assert meta["action"] == "test"
    assert meta["count"] == 5


def test_log_event_no_meta():
    with get_conn() as conn:
        log_event(conn, "no_meta_event")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT meta FROM event_log WHERE event_type = 'no_meta_event'"
        ).fetchone()
    assert row is not None
    assert row["meta"] is None


def test_get_event_counts_all_time():
    with get_conn() as conn:
        log_event(conn, "quote_sent", user_id=1)
        log_event(conn, "quote_sent", user_id=1)
        log_event(conn, "lead_paid", user_id=1)
    counts = get_event_counts()
    count_map = {r["event_type"]: r["count"] for r in counts}
    assert count_map.get("quote_sent") == 2
    assert count_map.get("lead_paid") == 1


def test_get_event_counts_last_30_days():
    with get_conn() as conn:
        log_event(conn, "invoice_sent", user_id=1)
        # Insert an old event directly
        conn.execute(
            "INSERT INTO event_log (event_type, user_id, created_at) VALUES ('old_event', 1, '2020-01-01')"
        )
    last30 = get_event_counts(days=30)
    alltime = get_event_counts()
    last30_types = {r["event_type"] for r in last30}
    alltime_types = {r["event_type"] for r in alltime}
    assert "invoice_sent" in last30_types
    assert "old_event" not in last30_types
    assert "old_event" in alltime_types


def test_quote_sent_event_logged():
    """update_quote should log a quote_sent event."""
    lead_id, _ = add_lead("Quote Event", "roofing")
    update_quote(lead_id, 1000.0)
    counts = get_event_counts()
    types = [r["event_type"] for r in counts]
    assert "quote_sent" in types


def test_lead_booked_event_logged():
    """mark_booked should log a lead_booked event."""
    lead_id, _ = add_lead("Book Event", "painting")
    mark_booked(lead_id, "2026-07-01")
    counts = get_event_counts()
    types = [r["event_type"] for r in counts]
    assert "lead_booked" in types


def test_lead_completed_event_logged():
    """mark_completed should log a lead_completed event."""
    lead_id, _ = add_lead("Complete Event", "fencing")
    mark_booked(lead_id, "2026-07-01")
    mark_completed(lead_id)
    counts = get_event_counts()
    types = [r["event_type"] for r in counts]
    assert "lead_completed" in types


def test_invoice_sent_event_logged():
    """mark_invoice_sent should log an invoice_sent event."""
    lead_id, _ = add_lead("Invoice Event", "gutters")
    mark_invoice_sent(lead_id, invoice_amount=500.0)
    counts = get_event_counts()
    types = [r["event_type"] for r in counts]
    assert "invoice_sent" in types


def test_lead_paid_event_logged():
    """mark_paid should log a lead_paid event."""
    lead_id, _ = add_lead("Paid Event", "lawn care")
    mark_paid(lead_id)
    counts = get_event_counts()
    types = [r["event_type"] for r in counts]
    assert "lead_paid" in types


def test_next_service_set_event_logged():
    """set_next_service should log a next_service_set event."""
    lead_id, _ = add_lead("Next Svc Event", "cleaning")
    set_next_service(lead_id, "2027-03-01")
    counts = get_event_counts()
    types = [r["event_type"] for r in counts]
    assert "next_service_set" in types


# ---------------------------------------------------------------------------
# Feature 2 (continued): Reminder dismissal
# ---------------------------------------------------------------------------


def test_dismiss_review_request_hides_from_reminders():
    """After dismissing review_request, lead should not appear in review reminders."""
    lead_id, _ = add_lead("Review Dismiss Test", "lawn care")
    mark_booked(lead_id, "2026-06-01")
    mark_completed(lead_id)
    # Force review_reminder_at to today
    with get_conn() as conn:
        conn.execute("UPDATE leads SET review_reminder_at = date('now') WHERE id = ?", (lead_id,))
    # Confirm it appears before dismissal
    before = [r["id"] for r in get_review_reminders()]
    assert lead_id in before

    # Dismiss it
    ok = dismiss_reminder_standalone(lead_id, "review_request")
    assert ok is True

    # Should not appear anymore
    after = [r["id"] for r in get_review_reminders()]
    assert lead_id not in after


def test_dismiss_review_logs_reminder_dismissed_event():
    """Dismissing a reminder logs a reminder_dismissed event."""
    lead_id, _ = add_lead("Dismiss Event", "painting")
    dismiss_reminder_standalone(lead_id, "review_request")
    counts = get_event_counts()
    types = [r["event_type"] for r in counts]
    assert "reminder_dismissed" in types


def test_dismiss_reactivation_hides_from_results():
    """After dismissing reactivation, lead should not appear in reactivation queries."""
    lead_id, _ = add_lead("Reactivation Dismiss", "roofing")
    # Force last_contact_at to 35 days ago
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET last_contact_at = date('now', '-35 days') WHERE id = ?", (lead_id,)
        )
    before = [r["id"] for r in get_reactivation_leads(30)]
    assert lead_id in before

    dismiss_reminder_standalone(lead_id, "reactivation")

    after = [r["id"] for r in get_reactivation_leads(30)]
    assert lead_id not in after


def test_dismiss_job_today_hides_for_today():
    """After dismissing job_today, lead should not appear in today's jobs."""
    from datetime import date

    today = date.today().isoformat()
    lead_id, _ = add_lead("Job Dismiss Test", "pressure washing")
    mark_booked(lead_id, today)

    before = [r["id"] for r in get_job_today_leads()]
    assert lead_id in before

    dismiss_reminder_standalone(lead_id, "job_today")

    after = [r["id"] for r in get_job_today_leads()]
    assert lead_id not in after


def test_dismiss_invalid_type_returns_false():
    lead_id, _ = add_lead("Bad Dismiss", "roofing")
    result = dismiss_reminder_standalone(lead_id, "nonexistent_type")
    assert result is False


# ---------------------------------------------------------------------------
# Feature 5: Won vs Paid cleanup
# ---------------------------------------------------------------------------


def test_pipeline_summary_counts_won_as_paid():
    """Won leads should be counted in the 'paid' bucket in pipeline summary."""
    from leadclaw.queries import get_pipeline_summary, mark_won

    lead_id, _ = add_lead("Won Lead", "roofing")
    update_quote(lead_id, 1000.0)
    mark_won(lead_id)

    rows, totals = get_pipeline_summary()
    status_map = {r["status"]: r["count"] for r in rows}

    # 'won' should be merged into 'paid' bucket
    assert "won" not in status_map
    assert status_map.get("paid", 0) >= 1

    # Won value counted in won_value (now includes both won and paid)
    assert totals["won_value"] == 1000.0


def test_api_closed_includes_won_leads():
    """api_closed should include leads with status='won'."""
    from leadclaw.queries import mark_won
    from leadclaw.web import api_closed

    lead_id, _ = add_lead("Won Close", "roofing", user_id=1)
    mark_won(lead_id)
    data = api_closed(user_id=1)
    ids = [r["id"] for r in data["closed"]]
    assert lead_id in ids


def test_api_closed_includes_paid_leads():
    """api_closed should include leads with status='paid'."""
    from leadclaw.web import api_closed

    lead_id, _ = add_lead("Paid Close", "lawn care", user_id=1)
    mark_paid(lead_id)
    data = api_closed(user_id=1)
    ids = [r["id"] for r in data["closed"]]
    assert lead_id in ids


# ---------------------------------------------------------------------------
# Web endpoint tests for new features
# ---------------------------------------------------------------------------


def _make_auth_client():
    """Helper to create an authenticated test client."""
    import bcrypt

    from leadclaw.db import create_user, verify_user_email
    from leadclaw.web import app

    app.config["TESTING"] = True
    client = app.test_client()
    email = "feat_test@example.com"
    pw_hash = bcrypt.hashpw(b"pass1234", bcrypt.gensalt()).decode()
    uid = create_user(email, pw_hash, "tok-feat")
    verify_user_email(uid)
    client.post("/login", data={"email": email, "password": "pass1234"})
    client._test_user_id = uid
    return client


def test_web_dismiss_endpoint():
    """POST /api/reminders/dismiss should work."""
    client = _make_auth_client()
    lead_id, _ = add_lead("Web Dismiss", "roofing", user_id=client._test_user_id)

    r = client.post(
        "/api/reminders/dismiss",
        data=json.dumps({"lead_id": lead_id, "reminder_type": "review_request"}),
        content_type="application/json",
    )
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data.get("ok") is True

    # Event logged
    counts = get_event_counts()
    types = [row["event_type"] for row in counts]
    assert "reminder_dismissed" in types


def test_web_dismiss_invalid_type():
    """POST /api/reminders/dismiss with invalid type should 400."""
    client = _make_auth_client()
    lead_id, _ = add_lead("Bad Type", "painting", user_id=client._test_user_id)
    r = client.post(
        "/api/reminders/dismiss",
        data=json.dumps({"lead_id": lead_id, "reminder_type": "invalid_type"}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_web_usage_endpoint():
    """GET /api/usage should return event counts scoped to current user."""
    client = _make_auth_client()
    uid = client._test_user_id
    # Log events for the authenticated user
    with get_conn() as conn:
        log_event(conn, "quote_sent", user_id=uid)
        log_event(conn, "lead_paid", user_id=uid)

    r = client.get("/api/usage")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "last_30_days" in data
    assert "all_time" in data
    all_types = [row["event_type"] for row in data["all_time"]]
    assert "quote_sent" in all_types
    assert "lead_paid" in all_types


def test_web_usage_section_in_more_tab():
    """Dashboard HTML should contain the usage section."""
    from leadclaw.web import _build_dashboard_html

    html = _build_dashboard_html("test@example.com")
    assert "usage-section" in html
    assert "loadUsage" in html
    assert "Usage" in html
