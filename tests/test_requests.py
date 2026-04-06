"""
tests/test_requests.py - Tests for request-to-book conversion feature.

Covers:
- public_request leads appear in get_public_requests()
- booking a public request updates status to 'booked'
- scheduled_date and scheduled_time_window are stored correctly
- booking_confirmation template works (with and without time window)
- mark_booked does not regress for non-request leads
- get_public_requests filters work (unbooked / booked / all)
"""

import os

import pytest

from leadclaw.db import init_db
from leadclaw.drafting import draft_message
from leadclaw.queries import (
    add_lead,
    get_lead_by_id,
    get_public_requests,
    mark_booked,
    mark_lost,
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
# Helper
# ---------------------------------------------------------------------------


def _add_request(name="Alice Smith", service="Lawn Mowing", **kwargs):
    """Add a public_request lead and return its id."""
    lead_id, _ = add_lead(
        name=name,
        service=service,
        phone="512-555-0001",
        lead_source="public_request",
        requested_date="2026-05-10",
        requested_time_window="morning",
        service_address="123 Main St, Austin TX",
        **kwargs,
    )
    return lead_id


def _add_normal(name="Bob Jones", service="Landscaping"):
    lead_id, _ = add_lead(name=name, service=service, phone="512-555-0002")
    return lead_id


# ---------------------------------------------------------------------------
# get_public_requests — appearance and filters
# ---------------------------------------------------------------------------


def test_public_request_appears_in_get_public_requests():
    _add_request()
    rows = get_public_requests()
    assert len(rows) == 1
    assert rows[0]["lead_source"] == "public_request"
    assert rows[0]["name"] == "Alice Smith"


def test_normal_lead_not_in_get_public_requests():
    _add_request()
    _add_normal()
    rows = get_public_requests()
    assert all(r["lead_source"] == "public_request" for r in rows)
    assert len(rows) == 1


def test_filter_unbooked_returns_pending_only():
    lid = _add_request()
    lid2 = _add_request(name="Carol White")
    # Book one
    mark_booked(lid, "2026-05-12")
    unbooked = get_public_requests(filter="unbooked")
    ids = [r["id"] for r in unbooked]
    assert lid not in ids
    assert lid2 in ids


def test_filter_booked_returns_booked_only():
    lid = _add_request()
    _add_request(name="Carol White")
    mark_booked(lid, "2026-05-12")
    booked = get_public_requests(filter="booked")
    assert len(booked) == 1
    assert booked[0]["id"] == lid
    assert booked[0]["status"] == "booked"


def test_filter_all_returns_all_requests():
    lid1 = _add_request()
    lid2 = _add_request(name="Carol White")
    mark_booked(lid1, "2026-05-12")
    all_rows = get_public_requests(filter="all")
    ids = [r["id"] for r in all_rows]
    assert lid1 in ids
    assert lid2 in ids


def test_filter_lost_not_in_unbooked():
    lid = _add_request()
    mark_lost(lid, "no_response")
    rows = get_public_requests(filter="unbooked")
    assert not any(r["id"] == lid for r in rows)


def test_user_id_scoping():
    from leadclaw.db import create_user, get_conn
    # Create user 2 so FK constraint passes
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, password_hash, email_verified)"
            " VALUES (2, 'u2@test.com', 'hash', 1)"
        )
    lid1 = _add_request(user_id=1)
    lid2 = _add_request(name="User2 Lead", user_id=2)
    rows_u1 = get_public_requests(user_id=1)
    rows_u2 = get_public_requests(user_id=2)
    assert all(r["id"] == lid1 for r in rows_u1)
    assert all(r["id"] == lid2 for r in rows_u2)


# ---------------------------------------------------------------------------
# mark_booked — request-to-book conversion
# ---------------------------------------------------------------------------


def test_booking_request_sets_status_to_booked():
    lid = _add_request()
    mark_booked(lid, "2026-05-15")
    lead = get_lead_by_id(lid)
    assert lead["status"] == "booked"


def test_booking_request_stores_scheduled_date():
    lid = _add_request()
    mark_booked(lid, "2026-05-20")
    lead = get_lead_by_id(lid)
    assert str(lead["scheduled_date"])[:10] == "2026-05-20"


def test_booking_request_stores_time_window():
    lid = _add_request()
    mark_booked(lid, "2026-05-20", scheduled_time_window="afternoon")
    lead = get_lead_by_id(lid)
    assert lead["scheduled_time_window"] == "afternoon"


def test_booking_without_time_window_leaves_it_null():
    lid = _add_request()
    mark_booked(lid, "2026-05-20")
    lead = get_lead_by_id(lid)
    # Either None or not set — should not be a truthy string
    assert not lead["scheduled_time_window"]


def test_booking_sets_follow_up_after_null():
    lid = _add_request()
    mark_booked(lid, "2026-06-01")
    lead = get_lead_by_id(lid)
    assert lead["follow_up_after"] is None


# ---------------------------------------------------------------------------
# Booking confirmation message template
# ---------------------------------------------------------------------------


def test_booking_confirmation_includes_name_and_service():
    lead = {
        "name": "Alice Smith",
        "service": "Lawn Mowing",
        "scheduled_date": "2026-05-20",
        "scheduled_time_window": None,
        "quote_amount": None,
    }
    msg = draft_message(lead, "booking_confirmation")
    assert "Alice" in msg
    assert "Lawn Mowing" in msg
    assert "2026-05-20" in msg


def test_booking_confirmation_includes_time_window_morning():
    lead = {
        "name": "Alice Smith",
        "service": "Lawn Mowing",
        "scheduled_date": "2026-05-20",
        "scheduled_time_window": "morning",
        "quote_amount": None,
    }
    msg = draft_message(lead, "booking_confirmation")
    assert "morning" in msg.lower()


def test_booking_confirmation_afternoon():
    lead = {
        "name": "Bob Jones",
        "service": "Cleanup",
        "scheduled_date": "2026-05-21",
        "scheduled_time_window": "afternoon",
        "quote_amount": None,
    }
    msg = draft_message(lead, "booking_confirmation")
    assert "afternoon" in msg.lower()


def test_booking_confirmation_flexible_window_not_in_message():
    """'flexible' time window should not appear as a time phrase in the message."""
    lead = {
        "name": "Carol White",
        "service": "Mulching",
        "scheduled_date": "2026-05-22",
        "scheduled_time_window": "flexible",
        "quote_amount": None,
    }
    msg = draft_message(lead, "booking_confirmation")
    # "flexible" should not appear as a scheduling phrase
    assert "flexible" not in msg.lower()


def test_booking_confirmation_no_window():
    lead = {
        "name": "Dan",
        "service": "Pressure Washing",
        "scheduled_date": "2026-05-23",
        "scheduled_time_window": None,
        "quote_amount": None,
    }
    msg = draft_message(lead, "booking_confirmation")
    assert "2026-05-23" in msg
    # No time phrase injected
    assert "morning" not in msg
    assert "afternoon" not in msg


def test_booking_confirmation_no_date():
    lead = {
        "name": "Eve",
        "service": "Gutter Cleaning",
        "scheduled_date": None,
        "scheduled_time_window": None,
        "quote_amount": None,
    }
    msg = draft_message(lead, "booking_confirmation")
    assert "Eve" in msg
    assert "Gutter Cleaning" in msg


# ---------------------------------------------------------------------------
# Regression: existing mark_booked (non-request) still works
# ---------------------------------------------------------------------------


def test_normal_lead_can_be_booked():
    lid = _add_normal()
    mark_booked(lid, "2026-05-18")
    lead = get_lead_by_id(lid)
    assert lead["status"] == "booked"
    assert str(lead["scheduled_date"])[:10] == "2026-05-18"


def test_normal_lead_booked_with_time_window():
    lid = _add_normal()
    mark_booked(lid, "2026-05-18", scheduled_time_window="evening")
    lead = get_lead_by_id(lid)
    assert lead["scheduled_time_window"] == "evening"
