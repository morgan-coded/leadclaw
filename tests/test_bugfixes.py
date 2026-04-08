"""
tests/test_bugfixes.py - Tests for targeted bug fixes.

Covers:
1. Resend notification path uses _ureq alias correctly
2. get_event_counts scoped by user_id
3. api_closed uses SQL (get_closed_leads)
4. _lead_to_dict normalizes won→paid
5. Manifest has no broken icon references
6. /request max-length validation
7. next_available_date with empty allowed_weekdays
8. set_availability handles non-int weekday values
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from leadclaw.db import get_conn, init_db
from leadclaw.queries import add_lead, get_event_counts, log_event, mark_won
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
# Helpers
# ---------------------------------------------------------------------------


def _make_auth_client(email="bugfix_test@example.com"):
    import bcrypt

    from leadclaw.db import create_user, verify_user_email
    from leadclaw.web import app

    app.config["TESTING"] = True
    client = app.test_client()
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    token = "bugfix-verify-token"
    uid = create_user(email, pw_hash, token)
    verify_user_email(uid)
    client.post("/login", data={"email": email, "password": "password123"})
    client._test_user_id = uid
    return client


# ---------------------------------------------------------------------------
# Fix 1: Resend notification path
# ---------------------------------------------------------------------------


def test_send_notification_resend_path_uses_alias(monkeypatch):
    """_send_new_request_notification Resend path should call _ureq (the alias), not urllib.request."""
    import leadclaw.web as web_mod

    monkeypatch.setenv("RESEND_API_KEY", "test-key-fake")
    monkeypatch.setenv("OWNER_NOTIFY_EMAIL", "owner@example.com")

    fake_resp = MagicMock()
    mock_urlopen = MagicMock(return_value=fake_resp)
    mock_req_class = MagicMock()

    with patch("urllib.request.urlopen", mock_urlopen):
        with patch("urllib.request.Request", mock_req_class):
            # Should not raise NameError
            web_mod._send_new_request_notification(
                {
                    "name": "Test User",
                    "service": "Lawn Mowing",
                    "phone": "555-1234",
                    "service_address": "123 Main",
                    "requested_date": None,
                    "requested_time_window": None,
                    "notes": None,
                }
            )
    # Either the mock was called or an HTTP error was caught — the key is no NameError raised


def test_send_notification_no_owner_email_skips_silently(monkeypatch, capsys):
    """With no OWNER_NOTIFY_EMAIL and no SMTP_USER, notification should silently skip."""
    import leadclaw.web as web_mod

    monkeypatch.delenv("OWNER_NOTIFY_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    # Should not raise
    web_mod._send_new_request_notification(
        {
            "name": "Test",
            "service": "Gutters",
            "phone": "555-0000",
            "service_address": "456 Elm",
            "requested_date": None,
            "requested_time_window": None,
            "notes": None,
        }
    )


def test_send_notification_dev_fallback_stderr(monkeypatch, capsys):
    """When no RESEND and no SMTP, should log to stderr without raising."""
    import leadclaw.web as web_mod

    monkeypatch.setenv("OWNER_NOTIFY_EMAIL", "owner@example.com")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    web_mod._send_new_request_notification(
        {
            "name": "Dev User",
            "service": "Cleanup",
            "phone": "555-9999",
            "service_address": "789 Oak",
            "requested_date": None,
            "requested_time_window": None,
            "notes": None,
        }
    )
    captured = capsys.readouterr()
    assert "Dev User" in captured.err or "NOTIFY" in captured.err


def test_request_submission_succeeds_if_notification_fails(monkeypatch):
    """Notification failure must not break /request form submission."""
    import leadclaw.web as web_mod

    monkeypatch.setenv("RESEND_API_KEY", "bad-key")
    monkeypatch.setenv("OWNER_NOTIFY_EMAIL", "owner@example.com")

    # Make urlopen always raise
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        client = web_mod.app.test_client()
        r = client.post(
            "/request",
            data={
                "name": "Notify Fail Test",
                "phone": "512-555-7777",
                "service": "Lawn Mowing",
                "service_address": "100 Test St, Austin TX",
                "requested_time_window": "flexible",
            },
        )
    # Should still succeed (200 success page)
    assert r.status_code == 200
    assert b"Request Received" in r.data


# ---------------------------------------------------------------------------
# Fix 2: get_event_counts scoped by user_id
# ---------------------------------------------------------------------------


def test_get_event_counts_user_isolation():
    """Events from user 2 should not appear in user 1's counts."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, password_hash, email_verified)"
            " VALUES (2, 'u2@test.com', 'hash', 1)"
        )
        log_event(conn, "quote_sent", user_id=1)
        log_event(conn, "lead_paid", user_id=2)

    u1 = get_event_counts(user_id=1)
    u2 = get_event_counts(user_id=2)

    u1_types = {r["event_type"] for r in u1}
    u2_types = {r["event_type"] for r in u2}

    assert "quote_sent" in u1_types
    assert "lead_paid" not in u1_types
    assert "lead_paid" in u2_types
    assert "quote_sent" not in u2_types


def test_get_event_counts_global_includes_all_users():
    """Global counts (no user_id) should include events from all users."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, password_hash, email_verified)"
            " VALUES (2, 'u2@test.com', 'hash', 1)"
        )
        log_event(conn, "quote_sent", user_id=1)
        log_event(conn, "lead_paid", user_id=2)

    counts = get_event_counts()
    types = {r["event_type"] for r in counts}
    assert "quote_sent" in types
    assert "lead_paid" in types


def test_api_usage_scoped_to_current_user():
    """GET /api/usage should only return events for the logged-in user."""

    # Create two users
    u1_id = 1  # created by init_db or first auth client
    client = _make_auth_client()
    u1_id = client._test_user_id

    # Create user 2
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, password_hash, email_verified)"
            " VALUES (99, 'other@test.com', 'hash', 1)"
        )
        log_event(conn, "quote_sent", user_id=u1_id)
        log_event(conn, "lead_paid", user_id=99)

    r = client.get("/api/usage")
    assert r.status_code == 200
    data = json.loads(r.data)
    all_types = {row["event_type"] for row in data["all_time"]}
    assert "quote_sent" in all_types
    # lead_paid belongs to user 99, not current user
    assert "lead_paid" not in all_types


# ---------------------------------------------------------------------------
# Fix 3: api_closed uses SQL (not Python filter)
# ---------------------------------------------------------------------------


def test_api_closed_only_closed_statuses():
    """api_closed should return won/lost/paid, not active leads."""
    from leadclaw.queries import mark_lost, mark_paid
    from leadclaw.web import api_closed

    id_active, _ = add_lead("Active Lead", "gutters", user_id=1)
    id_won, _ = add_lead("Won Lead", "roofing", user_id=1)
    id_lost, _ = add_lead("Lost Lead", "painting", user_id=1)
    id_paid, _ = add_lead("Paid Lead", "landscaping", user_id=1)

    mark_won(id_won)
    mark_lost(id_lost, "price")
    mark_paid(id_paid)

    data = api_closed(user_id=1)
    names = {r["name"] for r in data["closed"]}
    assert "Active Lead" not in names
    assert "Won Lead" in names
    assert "Lost Lead" in names
    assert "Paid Lead" in names


def test_get_closed_leads_direct_sql():
    """get_closed_leads() should return closed rows without Python filtering."""
    from leadclaw.queries import get_closed_leads, mark_paid

    id_active, _ = add_lead("Active2", "gutters", user_id=1)
    id_won, _ = add_lead("Won2", "roofing", user_id=1)
    id_paid, _ = add_lead("Paid2", "lawn care", user_id=1)

    mark_won(id_won)
    mark_paid(id_paid)

    rows = get_closed_leads(user_id=1)
    ids = {r["id"] for r in rows}
    assert id_active not in ids
    assert id_won in ids
    assert id_paid in ids


# ---------------------------------------------------------------------------
# Fix 4: _lead_to_dict normalizes won→paid
# ---------------------------------------------------------------------------


def test_lead_to_dict_normalizes_won_to_paid():
    """_lead_to_dict must return status='paid' for a DB row with status='won'."""
    from leadclaw.web import _lead_to_dict

    lead_id, _ = add_lead("Won Status Lead", "roofing", user_id=1)
    mark_won(lead_id)

    from leadclaw.queries import get_lead_by_id

    row = get_lead_by_id(lead_id)
    assert row["status"] == "won"  # raw DB value is still 'won'

    d = _lead_to_dict(row)
    assert d["status"] == "paid"  # normalized in API output


def test_api_closed_never_returns_won_status():
    """Closed leads API must never return status='won' — only 'paid', 'lost'."""
    from leadclaw.web import api_closed

    lead_id, _ = add_lead("Old Won Lead", "fencing", user_id=1)
    mark_won(lead_id)

    data = api_closed(user_id=1)
    statuses = {r["status"] for r in data["closed"]}
    assert "won" not in statuses
    assert "paid" in statuses


def test_pipeline_summary_cli_says_paid_not_won(capsys):
    """CLI pipeline summary should say 'Paid (closed)', not 'Won (closed)'."""
    from leadclaw.commands import print_pipeline_summary
    from leadclaw.queries import get_pipeline_summary

    summary, totals = get_pipeline_summary()
    print_pipeline_summary(summary, totals)
    out = capsys.readouterr().out
    assert "Paid (closed)" in out
    assert "Won (closed)" not in out


# ---------------------------------------------------------------------------
# Fix 5: Manifest has no broken icon references
# ---------------------------------------------------------------------------


def test_manifest_no_broken_icons():
    """Manifest must not reference static icon files that don't exist."""
    import os

    from leadclaw.web import app

    client = app.test_client()
    r = client.get("/manifest.json")
    assert r.status_code == 200
    data = json.loads(r.data)

    static_dir = os.path.join(os.path.dirname(__file__), "..", "leadclaw", "static")
    for icon in data.get("icons", []):
        src = icon.get("src", "")
        if src.startswith("/static/"):
            fname = src[len("/static/") :]
            full = os.path.join(static_dir, fname)
            assert os.path.exists(full), f"Manifest references missing icon: {src}"


def test_manifest_contains_required_fields():
    """Manifest must have name, start_url, display."""
    from leadclaw.web import app

    client = app.test_client()
    r = client.get("/manifest.json")
    data = json.loads(r.data)
    assert data["name"] == "LeadClaw"
    assert data["start_url"] == "/"
    assert data["display"] == "standalone"


# ---------------------------------------------------------------------------
# Fix 6: /request max-length validation
# ---------------------------------------------------------------------------


def _request_post(client, **kwargs):
    defaults = {
        "name": "Valid Name",
        "phone": "512-555-1234",
        "service": "Lawn Mowing",
        "service_address": "123 Main St, Austin TX",
        "requested_time_window": "flexible",
    }
    defaults.update(kwargs)
    return client.post("/request", data=defaults)


def test_request_overlong_name_rejected():
    from leadclaw.config import MAX_NAME_LENGTH
    from leadclaw.web import app

    client = app.test_client()
    r = _request_post(client, name="A" * (MAX_NAME_LENGTH + 1))
    assert r.status_code == 422
    assert b"Name" in r.data or b"name" in r.data.lower()


def test_request_overlong_phone_rejected():
    from leadclaw.web import app

    client = app.test_client()
    r = _request_post(client, phone="5" * 31)
    assert r.status_code == 400


def test_request_overlong_service_address_rejected():
    from leadclaw.config import MAX_FIELD_LENGTH
    from leadclaw.web import app

    client = app.test_client()
    r = _request_post(client, service_address="X" * (MAX_FIELD_LENGTH + 1))
    assert r.status_code == 422
    assert b"address" in r.data.lower() or b"service" in r.data.lower()


def test_request_overlong_notes_rejected():
    from leadclaw.web import app

    client = app.test_client()
    r = _request_post(client, notes="N" * 2001)
    assert r.status_code == 400


def test_request_valid_lengths_accepted():
    from leadclaw.web import app

    client = app.test_client()
    r = _request_post(client)
    assert r.status_code == 200
    assert b"Request Received" in r.data


def test_request_required_fields_still_enforced():
    from leadclaw.web import app

    client = app.test_client()
    r = client.post("/request", data={"service": "Lawn Mowing"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Fix 7: next_available_date with empty allowed_weekdays
# ---------------------------------------------------------------------------


def test_next_available_date_empty_weekdays_returns_from_date():
    """Empty allowed_weekdays = all days available (consistent with check_date)."""
    from leadclaw.availability import next_available_date

    avail = {"allowed_weekdays": [], "blocked_dates": []}
    result = next_available_date(avail, from_date="2026-04-13")
    # Should return the start date immediately (no weekday restriction)
    assert result == "2026-04-13"


def test_next_available_date_empty_weekdays_skips_blocked():
    """Empty allowed_weekdays with a blocked start date should find the next day."""
    from leadclaw.availability import next_available_date

    avail = {"allowed_weekdays": [], "blocked_dates": ["2026-04-13"]}
    result = next_available_date(avail, from_date="2026-04-13")
    assert result == "2026-04-14"


def test_check_date_empty_weekdays_consistent_with_next_available():
    """check_date and next_available_date must agree on what 'empty weekdays' means."""
    from leadclaw.availability import check_date, next_available_date

    avail = {"allowed_weekdays": [], "blocked_dates": []}
    check_result = check_date("2026-04-13", avail)
    next_result = next_available_date(avail, from_date="2026-04-13")

    # Both should treat all days as available
    assert check_result["ok"] is True
    assert next_result is not None


def test_next_available_date_all_blocked_returns_none():
    """If every day within 60 days is blocked, return None."""
    from datetime import date, timedelta

    from leadclaw.availability import next_available_date

    start = date(2026, 4, 13)
    blocked = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4, 5, 6], "blocked_dates": blocked}
    result = next_available_date(avail, from_date="2026-04-13")
    assert result is None


# ---------------------------------------------------------------------------
# Fix 8: set_availability handles non-int weekday values
# ---------------------------------------------------------------------------


def test_set_availability_ignores_non_int_weekdays():
    """Non-integer weekday values must be silently ignored, not raise ValueError."""
    from leadclaw.availability import get_availability, set_availability

    # Should not raise
    set_availability(user_id=1, allowed_weekdays=["not-a-number", 0, 3, "foo"], blocked_dates=[])
    avail = get_availability(user_id=1)
    assert avail["allowed_weekdays"] == [0, 3]


def test_set_availability_ignores_none_weekday():
    """None values in weekday list must be ignored."""
    from leadclaw.availability import get_availability, set_availability

    set_availability(user_id=1, allowed_weekdays=[None, 1, 2], blocked_dates=[])
    avail = get_availability(user_id=1)
    assert avail["allowed_weekdays"] == [1, 2]


def test_set_availability_mixed_valid_invalid():
    """Mixed valid/invalid weekday inputs: only valid 0-6 ints survive."""
    from leadclaw.availability import get_availability, set_availability

    set_availability(
        user_id=1,
        allowed_weekdays=[0, "bad", 7, -1, 6, None, 3.7],
        blocked_dates=[],
    )
    avail = get_availability(user_id=1)
    # 0 and 6 are valid; "bad" → ignored, 7 → out of range, -1 → out of range,
    # None → ignored, 3.7 → int(3.7)=3 which is valid
    assert set(avail["allowed_weekdays"]).issubset({0, 1, 2, 3, 4, 5, 6})
    assert 0 in avail["allowed_weekdays"]
    assert 6 in avail["allowed_weekdays"]
    assert 7 not in avail["allowed_weekdays"]


def test_set_availability_duplicates_deduplicated():
    """Duplicate weekday values should be deduplicated and sorted."""
    from leadclaw.availability import get_availability, set_availability

    set_availability(user_id=1, allowed_weekdays=[2, 0, 2, 0, 4], blocked_dates=[])
    avail = get_availability(user_id=1)
    assert avail["allowed_weekdays"] == [0, 2, 4]


# ---------------------------------------------------------------------------
# NOTIFY_FROM_EMAIL wired into both Resend and SMTP paths
# ---------------------------------------------------------------------------


def test_notify_from_email_used_in_resend_path(monkeypatch):
    """NOTIFY_FROM_EMAIL must be used as 'from' in the Resend payload."""
    import json as _json

    import leadclaw.web as web_mod

    monkeypatch.setenv("NOTIFY_FROM_EMAIL", "Custom Sender <custom@example.com>")
    monkeypatch.setenv("RESEND_API_KEY", "test-key-fake")
    monkeypatch.setenv("OWNER_NOTIFY_EMAIL", "owner@example.com")
    monkeypatch.delenv("SMTP_HOST", raising=False)

    captured_payload = {}

    class FakeReq:
        def __init__(self, url, data=None, method=None, headers=None):
            if data:
                captured_payload.update(_json.loads(data))

    fake_resp = MagicMock()
    mock_urlopen = MagicMock(return_value=fake_resp)

    with patch("urllib.request.Request", FakeReq):
        with patch("urllib.request.urlopen", mock_urlopen):
            web_mod._send_new_request_notification(
                {
                    "name": "Test User",
                    "service": "Lawn Mowing",
                    "phone": "555-1234",
                    "service_address": "123 Main",
                    "requested_date": None,
                    "requested_time_window": None,
                    "notes": None,
                }
            )

    assert captured_payload.get("from") == "Custom Sender <custom@example.com>"


def test_notify_from_email_used_in_smtp_path(monkeypatch):
    """NOTIFY_FROM_EMAIL must be used as the From address in the SMTP MIMEText message."""
    import leadclaw.web as web_mod

    monkeypatch.setenv("NOTIFY_FROM_EMAIL", "smtp-custom@example.com")
    monkeypatch.setenv("OWNER_NOTIFY_EMAIL", "owner@example.com")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "smtpuser@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")

    sent_from = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, pw):
            pass

        def sendmail(self, from_addr, to_addrs, msg_str):
            sent_from["from"] = from_addr

    with patch("smtplib.SMTP", FakeSMTP):
        web_mod._send_new_request_notification(
            {
                "name": "SMTP User",
                "service": "Pressure Washing",
                "phone": "555-4321",
                "service_address": "456 Oak",
                "requested_date": None,
                "requested_time_window": None,
                "notes": None,
            }
        )

    assert sent_from.get("from") == "smtp-custom@example.com"


def test_notify_from_email_default_resend_path(monkeypatch):
    """Without NOTIFY_FROM_EMAIL set, the Resend 'from' falls back to the default."""
    import json as _json

    import leadclaw.web as web_mod

    monkeypatch.delenv("NOTIFY_FROM_EMAIL", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "test-key-fake")
    monkeypatch.setenv("OWNER_NOTIFY_EMAIL", "owner@example.com")
    monkeypatch.delenv("SMTP_HOST", raising=False)

    captured_payload = {}

    class FakeReq:
        def __init__(self, url, data=None, method=None, headers=None):
            if data:
                captured_payload.update(_json.loads(data))

    fake_resp = MagicMock()
    mock_urlopen = MagicMock(return_value=fake_resp)

    with patch("urllib.request.Request", FakeReq):
        with patch("urllib.request.urlopen", mock_urlopen):
            web_mod._send_new_request_notification(
                {
                    "name": "Test",
                    "service": "Gutters",
                    "phone": "555-0000",
                    "service_address": "789 Elm",
                    "requested_date": None,
                    "requested_time_window": None,
                    "notes": None,
                }
            )

    # Should be the default noreply address
    assert captured_payload.get("from") is not None
    assert "@" in captured_payload["from"]


# ---------------------------------------------------------------------------


def test_set_availability_invalid_blocked_dates_ignored():
    """Invalid blocked date strings should be silently ignored."""
    from leadclaw.availability import get_availability, set_availability

    set_availability(
        user_id=1,
        allowed_weekdays=[0, 1, 2, 3, 4],
        blocked_dates=["not-a-date", "2026-12-25", "bad", ""],
    )
    avail = get_availability(user_id=1)
    assert avail["blocked_dates"] == ["2026-12-25"]
