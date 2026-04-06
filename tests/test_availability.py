"""
tests/test_availability.py - Tests for owner availability settings.

Covers:
- default availability settings
- set/get round-trip
- weekday conflict detection
- blocked date conflict detection
- available date passes check
- next_available_date suggestion
- booking on available date works normally
- booking on unavailable date still works (warn, not block)
- input sanitization (bad values ignored)
- no regressions to existing booking flow
"""

import os

import pytest

from leadclaw.availability import (
    check_date,
    get_availability,
    next_available_date,
    set_availability,
    working_days_hint,
)
from leadclaw.db import init_db
from leadclaw.queries import add_lead, get_lead_by_id, mark_booked
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
# Default availability
# ---------------------------------------------------------------------------


def test_get_availability_returns_defaults_on_first_access():
    avail = get_availability(user_id=1)
    assert avail["allowed_weekdays"] == [0, 1, 2, 3, 4]  # Mon–Fri
    assert avail["blocked_dates"] == []


def test_set_get_round_trip():
    set_availability(user_id=1, allowed_weekdays=[0, 2, 4], blocked_dates=["2026-07-04"])
    avail = get_availability(user_id=1)
    assert avail["allowed_weekdays"] == [0, 2, 4]
    assert "2026-07-04" in avail["blocked_dates"]


# ---------------------------------------------------------------------------
# set_availability sanitization
# ---------------------------------------------------------------------------


def test_set_deduplicates_and_sorts_weekdays():
    set_availability(user_id=1, allowed_weekdays=[4, 0, 0, 2], blocked_dates=[])
    avail = get_availability(user_id=1)
    assert avail["allowed_weekdays"] == [0, 2, 4]


def test_set_ignores_invalid_weekday_values():
    set_availability(user_id=1, allowed_weekdays=[0, 7, -1, 3], blocked_dates=[])
    avail = get_availability(user_id=1)
    assert avail["allowed_weekdays"] == [0, 3]


def test_set_ignores_invalid_blocked_dates():
    set_availability(user_id=1, allowed_weekdays=[0, 1, 2, 3, 4], blocked_dates=["not-a-date", "2026-06-15"])
    avail = get_availability(user_id=1)
    assert avail["blocked_dates"] == ["2026-06-15"]


def test_set_deduplicates_blocked_dates():
    set_availability(user_id=1, allowed_weekdays=[0, 1, 2, 3, 4], blocked_dates=["2026-06-15", "2026-06-15"])
    avail = get_availability(user_id=1)
    assert avail["blocked_dates"].count("2026-06-15") == 1


# ---------------------------------------------------------------------------
# check_date — weekday rules
# ---------------------------------------------------------------------------


def test_check_date_available_weekday_passes():
    # 2026-04-06 is a Monday (weekday 0)
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4], "blocked_dates": []}
    result = check_date("2026-04-06", avail)
    assert result["ok"] is True
    assert result["reason"] is None


def test_check_date_weekend_blocked_by_default_weekdays():
    # 2026-04-11 is a Saturday (weekday 5)
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4], "blocked_dates": []}
    result = check_date("2026-04-11", avail)
    assert result["ok"] is False
    assert "Sat" in result["reason"]


def test_check_date_weekend_allowed_when_configured():
    # 2026-04-11 is a Saturday (weekday 5)
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4, 5, 6], "blocked_dates": []}
    result = check_date("2026-04-11", avail)
    assert result["ok"] is True


def test_check_date_empty_allowed_weekdays_does_not_hard_block():
    """Empty allowed_weekdays = safety net: don't hard-block everything."""
    avail = {"allowed_weekdays": [], "blocked_dates": []}
    result = check_date("2026-04-06", avail)
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# check_date — blocked dates
# ---------------------------------------------------------------------------


def test_check_date_blocked_date_fails():
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4], "blocked_dates": ["2026-05-25"]}
    result = check_date("2026-05-25", avail)
    assert result["ok"] is False
    assert "2026-05-25" in result["reason"]


def test_check_date_blocked_date_takes_priority_over_weekday():
    # 2026-05-25 = Monday, normally allowed, but blocked
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4], "blocked_dates": ["2026-05-25"]}
    result = check_date("2026-05-25", avail)
    assert result["ok"] is False


def test_check_date_non_blocked_date_on_allowed_day():
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4], "blocked_dates": ["2026-05-26"]}
    result = check_date("2026-05-25", avail)  # 2026-05-25 Mon, not blocked
    assert result["ok"] is True


def test_check_date_invalid_format():
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4], "blocked_dates": []}
    result = check_date("not-a-date", avail)
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# next_available_date
# ---------------------------------------------------------------------------


def test_next_available_date_skips_weekend():
    # From 2026-04-11 (Saturday) with Mon-Fri allowed → next is Mon 2026-04-13
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4], "blocked_dates": []}
    result = next_available_date(avail, from_date="2026-04-11")
    assert result == "2026-04-13"


def test_next_available_date_skips_blocked():
    # From 2026-04-13 (Mon), but Mon is blocked → next is 2026-04-14 (Tue)
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4], "blocked_dates": ["2026-04-13"]}
    result = next_available_date(avail, from_date="2026-04-13")
    assert result == "2026-04-14"


def test_next_available_date_available_day_returns_same():
    # 2026-04-13 is Monday (weekday 0), allowed
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4], "blocked_dates": []}
    result = next_available_date(avail, from_date="2026-04-13")
    assert result == "2026-04-13"


def test_next_available_date_empty_weekdays_all_days_available():
    """Empty allowed_weekdays = all days available (consistent with check_date safety net)."""
    avail = {"allowed_weekdays": [], "blocked_dates": []}
    result = next_available_date(avail, from_date="2026-04-13")
    # 2026-04-13 is Monday — should be returned immediately since all days are available
    assert result == "2026-04-13"


# ---------------------------------------------------------------------------
# working_days_hint
# ---------------------------------------------------------------------------


def test_working_days_hint_mon_fri():
    avail = {"allowed_weekdays": [0, 1, 2, 3, 4]}
    hint = working_days_hint(avail)
    assert hint == "Mon, Tue, Wed, Thu, Fri"


def test_working_days_hint_empty_returns_none():
    avail = {"allowed_weekdays": []}
    hint = working_days_hint(avail)
    assert hint is None


# ---------------------------------------------------------------------------
# Booking still works regardless of availability (warn, not block)
# ---------------------------------------------------------------------------


def test_booking_on_available_date_succeeds():
    lid, _ = add_lead("Test User", "Lawn Mowing")
    # 2026-04-13 is a Monday
    set_availability(user_id=1, allowed_weekdays=[0, 1, 2, 3, 4], blocked_dates=[])
    mark_booked(lid, "2026-04-13")
    lead = get_lead_by_id(lid)
    assert lead["status"] == "booked"
    assert str(lead["scheduled_date"])[:10] == "2026-04-13"


def test_booking_on_blocked_date_still_succeeds():
    """Availability is advisory only \u2014 the owner can override."""
    lid, _ = add_lead("Test User", "Cleanup")
    set_availability(user_id=1, allowed_weekdays=[0, 1, 2, 3, 4], blocked_dates=["2026-04-14"])
    # Booking a blocked date should still work at the data layer
    mark_booked(lid, "2026-04-14")
    lead = get_lead_by_id(lid)
    assert lead["status"] == "booked"
    assert str(lead["scheduled_date"])[:10] == "2026-04-14"


def test_booking_on_weekend_still_succeeds():
    """Owner can book on a Saturday even if weekend isn't in allowed_weekdays."""
    lid, _ = add_lead("Test User", "Pressure Washing")
    set_availability(user_id=1, allowed_weekdays=[0, 1, 2, 3, 4], blocked_dates=[])
    # 2026-04-11 is Saturday
    mark_booked(lid, "2026-04-11")
    lead = get_lead_by_id(lid)
    assert lead["status"] == "booked"
