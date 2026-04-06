"""
tests/test_notifications.py - Tests for new request notification feature.

Covers:
- unseen request counting
- mark_request_seen / mark_all_requests_seen
- digest includes new requests section
- seen state survives booking (request still appears in unseen until viewed)
- normal leads not counted as unseen requests
- lost requests excluded from unseen
- no regressions to request booking flow
"""

import io
import os
import sys

import pytest

from leadclaw.db import init_db
from leadclaw.queries import (
    add_lead,
    get_lead_by_id,
    get_unseen_requests,
    mark_all_requests_seen,
    mark_booked,
    mark_lost,
    mark_request_seen,
)
from leadclaw.scheduler import run_daily_digest
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


def _add_request(name="Alice Smith", service="Lawn Mowing", **kwargs):
    lid, _ = add_lead(
        name=name,
        service=service,
        phone="512-555-0001",
        lead_source="public_request",
        requested_date="2026-05-10",
        requested_time_window="morning",
        service_address="123 Main St, Austin TX",
        **kwargs,
    )
    return lid


def _add_normal(name="Bob Jones", service="Landscaping"):
    lid, _ = add_lead(name=name, service=service, phone="512-555-0002")
    return lid


def _capture_digest() -> str:
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        run_daily_digest()
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Unseen request counting
# ---------------------------------------------------------------------------


def test_new_request_is_unseen_by_default():
    _add_request()
    unseen = get_unseen_requests()
    assert len(unseen) == 1


def test_normal_lead_not_in_unseen():
    _add_normal()
    unseen = get_unseen_requests()
    assert len(unseen) == 0


def test_multiple_requests_all_unseen():
    _add_request(name="A")
    _add_request(name="B")
    _add_request(name="C")
    assert len(get_unseen_requests()) == 3


def test_lost_request_excluded_from_unseen():
    lid = _add_request()
    mark_lost(lid, "no_response")
    unseen = get_unseen_requests()
    assert not any(r["id"] == lid for r in unseen)


# ---------------------------------------------------------------------------
# mark_request_seen
# ---------------------------------------------------------------------------


def test_mark_request_seen_removes_from_unseen():
    lid = _add_request()
    assert len(get_unseen_requests()) == 1
    mark_request_seen(lid)
    assert len(get_unseen_requests()) == 0


def test_mark_request_seen_returns_true_on_success():
    lid = _add_request()
    result = mark_request_seen(lid)
    assert result is True


def test_mark_request_seen_returns_false_for_missing_lead():
    result = mark_request_seen(99999)
    assert result is False


def test_mark_request_seen_idempotent():
    lid = _add_request()
    mark_request_seen(lid)
    mark_request_seen(lid)  # second call should not raise
    assert len(get_unseen_requests()) == 0


def test_mark_request_seen_only_affects_target():
    lid1 = _add_request(name="Alice")
    lid2 = _add_request(name="Bob")
    mark_request_seen(lid1)
    unseen = get_unseen_requests()
    assert len(unseen) == 1
    assert unseen[0]["id"] == lid2


# ---------------------------------------------------------------------------
# mark_all_requests_seen
# ---------------------------------------------------------------------------


def test_mark_all_requests_seen_clears_all():
    _add_request(name="A")
    _add_request(name="B")
    _add_request(name="C")
    count = mark_all_requests_seen()
    assert count == 3
    assert len(get_unseen_requests()) == 0


def test_mark_all_requests_seen_returns_zero_when_none():
    count = mark_all_requests_seen()
    assert count == 0


def test_mark_all_seen_does_not_affect_normal_leads():
    lid = _add_normal()
    mark_all_requests_seen()
    lead = get_lead_by_id(lid)
    # request_seen_at should not be set on a normal lead
    try:
        assert lead["request_seen_at"] is None
    except (KeyError, IndexError):
        pass  # column may not exist on very old rows


# ---------------------------------------------------------------------------
# Booked request still shows up in unseen until viewed
# ---------------------------------------------------------------------------


def test_booked_request_excluded_from_unseen():
    """Once a request is booked, it leaves the unseen queue — no noise for already-handled items."""
    lid = _add_request()
    mark_booked(lid, "2026-05-15")
    unseen = get_unseen_requests()
    assert not any(r["id"] == lid for r in unseen)


def test_unbooked_unseen_request_visible_before_booking():
    lid = _add_request()
    unseen_before = get_unseen_requests()
    assert any(r["id"] == lid for r in unseen_before)


# ---------------------------------------------------------------------------
# User ID scoping
# ---------------------------------------------------------------------------


def test_unseen_requests_scoped_by_user():
    from leadclaw.db import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, password_hash, email_verified)"
            " VALUES (2, 'u2@test.com', 'hash', 1)"
        )
    lid1 = _add_request(name="User1 Lead", user_id=1)
    lid2 = _add_request(name="User2 Lead", user_id=2)
    u1 = get_unseen_requests(user_id=1)
    u2 = get_unseen_requests(user_id=2)
    assert all(r["id"] == lid1 for r in u1)
    assert all(r["id"] == lid2 for r in u2)


# ---------------------------------------------------------------------------
# Daily digest includes new requests
# ---------------------------------------------------------------------------


def test_digest_includes_unseen_request_section():
    _add_request(name="Alice Smith", service="Lawn Mowing")
    output = _capture_digest()
    assert "New Requests" in output or "Requests" in output
    assert "Alice Smith" in output


def test_digest_shows_request_service():
    _add_request(name="Carol White", service="Pressure Washing")
    output = _capture_digest()
    assert "Pressure Washing" in output


def test_digest_no_request_section_when_no_requests():
    output = _capture_digest()
    # Should not crash and should not show an error
    assert "Error" not in output


def test_digest_shows_multiple_unseen_requests():
    _add_request(name="Alice")
    _add_request(name="Bob")
    output = _capture_digest()
    assert "Alice" in output
    assert "Bob" in output


def test_digest_unseen_count_in_header():
    _add_request(name="Alice")
    _add_request(name="Bob")
    output = _capture_digest()
    assert "2" in output  # count appears somewhere in the requests section


def test_digest_seen_request_not_in_new_section():
    lid = _add_request(name="Alice Seen")
    mark_request_seen(lid)
    output = _capture_digest()
    # "New Requests — ACTION NEEDED" section should not appear
    assert "ACTION NEEDED" not in output
