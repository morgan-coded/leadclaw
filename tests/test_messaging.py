"""Tests for communication automation: reminder queries and message templates."""
import os
import pytest

from leadclaw import db
from leadclaw.db import init_db, get_conn
from leadclaw.queries import (
    add_lead,
    mark_booked,
    mark_completed,
    mark_paid,
    get_job_today_leads,
    get_review_reminders,
    get_reactivation_leads,
    set_review_reminder,
)
from leadclaw.drafting import draft_message, MSG_TYPES
from tests.conftest import TEST_DB


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _add(name="Test Lead", service="Lawn care"):
    lead_id, _ = add_lead(name, service, phone="555-0100")
    return lead_id


# ---------------------------------------------------------------------------
# draft_message — template correctness
# ---------------------------------------------------------------------------

def test_draft_message_all_types():
    """draft_message returns a non-empty string for every MSG_TYPE."""
    lead = {
        "name": "Mike Johnson",
        "service": "lawn mowing",
        "quote_amount": 150,
        "scheduled_date": "2026-04-10",
    }
    for t in MSG_TYPES:
        msg = draft_message(lead, t)
        assert isinstance(msg, str), f"Expected str for type {t}"
        assert len(msg) > 10, f"Message too short for type {t}: {msg!r}"


def test_draft_message_uses_first_name():
    lead = {"name": "Mike Johnson", "service": "lawn care", "quote_amount": None, "scheduled_date": None}
    msg = draft_message(lead, "quote_followup")
    assert "Mike" in msg
    assert "Johnson" not in msg


def test_draft_message_includes_service():
    lead = {"name": "Mike", "service": "gutter cleaning", "quote_amount": None, "scheduled_date": None}
    msg = draft_message(lead, "booking_confirmation")
    assert "gutter cleaning" in msg


def test_draft_message_includes_quote_amount():
    lead = {"name": "Sara", "service": "roofing", "quote_amount": 1200, "scheduled_date": None}
    msg = draft_message(lead, "quote_followup")
    assert "1,200" in msg


def test_draft_message_unknown_type():
    lead = {"name": "Mike", "service": "lawn", "quote_amount": None, "scheduled_date": None}
    msg = draft_message(lead, "not_a_real_type")
    assert "Unknown" in msg or "not_a_real_type" in msg


def test_draft_message_no_api_call(monkeypatch):
    """draft_message must never call the Anthropic API."""
    called = []

    def fake_call(*a, **kw):
        called.append(True)
        return "AI response"

    import leadclaw.drafting as drafting_mod
    monkeypatch.setattr(drafting_mod, "_call", fake_call)

    lead = {"name": "Sam", "service": "painting", "quote_amount": None, "scheduled_date": None}
    draft_message(lead, "on_my_way")
    assert not called, "draft_message should not call _call() (no AI)"


def test_draft_message_fallback_name():
    """If name is missing, uses 'there' as fallback."""
    lead = {"name": None, "service": "fencing", "quote_amount": None, "scheduled_date": None}
    msg = draft_message(lead, "on_my_way")
    assert "there" in msg


# ---------------------------------------------------------------------------
# get_job_today_leads
# ---------------------------------------------------------------------------

def test_get_job_today_leads_empty():
    _add()
    assert get_job_today_leads() == []


def test_get_job_today_leads_booked_today():
    from datetime import date
    lead_id = _add()
    mark_booked(lead_id, str(date.today()))
    leads = get_job_today_leads()
    assert any(l["id"] == lead_id for l in leads)


def test_get_job_today_leads_excludes_non_booked():
    """A new lead (not booked) should not appear in jobs-today."""
    lead_id = _add()
    # Force scheduled_date = today but status = 'new'
    with get_conn() as conn:
        from datetime import date
        conn.execute(
            "UPDATE leads SET scheduled_date = ? WHERE id = ?",
            (str(date.today()), lead_id),
        )
    leads = get_job_today_leads()
    assert not any(l["id"] == lead_id for l in leads)


def test_get_job_today_leads_excludes_future_bookings():
    lead_id = _add()
    mark_booked(lead_id, "2099-12-31")
    leads = get_job_today_leads()
    assert not any(l["id"] == lead_id for l in leads)


# ---------------------------------------------------------------------------
# get_review_reminders
# ---------------------------------------------------------------------------

def test_get_review_reminders_empty():
    _add()
    assert get_review_reminders() == []


def test_get_review_reminders_after_complete():
    from datetime import date
    lead_id = _add()
    mark_booked(lead_id, str(date.today()))
    mark_completed(lead_id)
    # review_reminder_at = tomorrow by default; manually set to today
    set_review_reminder(lead_id, days=0)
    reminders = get_review_reminders()
    assert any(l["id"] == lead_id for l in reminders)


def test_get_review_reminders_future_not_shown():
    from datetime import date
    lead_id = _add()
    mark_booked(lead_id, str(date.today()))
    mark_completed(lead_id)
    # review_reminder_at is tomorrow (set by mark_completed) — should NOT appear yet
    reminders = get_review_reminders()
    assert not any(l["id"] == lead_id for l in reminders)


# ---------------------------------------------------------------------------
# set_review_reminder
# ---------------------------------------------------------------------------

def test_set_review_reminder_today():
    lead_id = _add()
    set_review_reminder(lead_id, days=0)
    reminders = get_review_reminders()
    assert any(l["id"] == lead_id for l in reminders)


def test_set_review_reminder_future_not_in_due():
    lead_id = _add()
    set_review_reminder(lead_id, days=7)
    reminders = get_review_reminders()
    assert not any(l["id"] == lead_id for l in reminders)


# ---------------------------------------------------------------------------
# mark_completed / mark_paid auto-set review_reminder_at
# ---------------------------------------------------------------------------

def test_mark_completed_sets_review_reminder():
    from datetime import date
    lead_id = _add()
    mark_booked(lead_id, str(date.today()))
    mark_completed(lead_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT review_reminder_at FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
    assert row["review_reminder_at"] is not None


def test_mark_paid_sets_review_reminder():
    from datetime import date
    lead_id = _add()
    mark_booked(lead_id, str(date.today()))
    mark_completed(lead_id)
    # Clear it to verify mark_paid sets it independently
    with get_conn() as conn:
        conn.execute("UPDATE leads SET review_reminder_at = NULL WHERE id = ?", (lead_id,))
        conn.commit()
    mark_paid(lead_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT review_reminder_at FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
    assert row["review_reminder_at"] is not None


def test_mark_paid_preserves_existing_review_reminder():
    """COALESCE: mark_paid should not overwrite an existing review_reminder_at."""
    from datetime import date
    lead_id = _add()
    mark_booked(lead_id, str(date.today()))
    mark_completed(lead_id)
    # Set a specific past date
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET review_reminder_at = '2020-01-01' WHERE id = ?", (lead_id,)
        )
        conn.commit()
    mark_paid(lead_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT review_reminder_at FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
    assert row["review_reminder_at"] == "2020-01-01", (
        "mark_paid should not overwrite existing review_reminder_at"
    )


# ---------------------------------------------------------------------------
# get_reactivation_leads — range-based buckets, pre-job statuses only
# ---------------------------------------------------------------------------

def test_get_reactivation_leads_empty():
    _add()
    assert get_reactivation_leads(30) == []


def _add_with_last_contact(days_ago: int, status: str = "new"):
    """Add a lead and backdate last_contact_at by `days_ago` days."""
    lead_id, _ = add_lead(f"Lead {days_ago}d", "service")
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET last_contact_at = date('now', ? || ' days'), status = ? WHERE id = ?",
            (f"-{days_ago}", status, lead_id),
        )
        conn.commit()
    return lead_id


def test_reactivation_30_bucket():
    """Lead last contacted 30 days ago should appear in 30-bucket."""
    lead_id = _add_with_last_contact(30)
    leads = get_reactivation_leads(30)
    assert any(l["id"] == lead_id for l in leads)


def test_reactivation_30_excludes_59_boundary():
    """Lead last contacted 59 days ago is still in 30-bucket (upper: < 60 days)."""
    lead_id = _add_with_last_contact(59)
    leads_30 = get_reactivation_leads(30)
    leads_60 = get_reactivation_leads(60)
    assert any(l["id"] == lead_id for l in leads_30)
    assert not any(l["id"] == lead_id for l in leads_60)


def test_reactivation_60_bucket():
    """Lead last contacted 60 days ago should appear in 60-bucket, not 30-bucket."""
    lead_id = _add_with_last_contact(60)
    leads_30 = get_reactivation_leads(30)
    leads_60 = get_reactivation_leads(60)
    assert not any(l["id"] == lead_id for l in leads_30)
    assert any(l["id"] == lead_id for l in leads_60)


def test_reactivation_90_bucket():
    """Lead last contacted 90 days ago should appear in 90-bucket only."""
    lead_id = _add_with_last_contact(90)
    leads_30 = get_reactivation_leads(30)
    leads_60 = get_reactivation_leads(60)
    leads_90 = get_reactivation_leads(90)
    assert not any(l["id"] == lead_id for l in leads_30)
    assert not any(l["id"] == lead_id for l in leads_60)
    assert any(l["id"] == lead_id for l in leads_90)


def test_reactivation_90_open_upper_bound():
    """Lead last contacted 120 days ago should appear in 90-bucket (open upper bound)."""
    lead_id = _add_with_last_contact(120)
    leads_90 = get_reactivation_leads(90)
    assert any(l["id"] == lead_id for l in leads_90)


def test_reactivation_excludes_post_job_statuses():
    """Booked, completed, paid, won, lost leads should not appear in any reactivation bucket."""
    from datetime import date
    for status in ("booked", "completed", "paid", "won", "lost"):
        lead_id = _add_with_last_contact(45, status=status)
        for days in [30, 60, 90]:
            leads = get_reactivation_leads(days)
            assert not any(l["id"] == lead_id for l in leads), (
                f"Status '{status}' should be excluded from reactivation-{days} bucket"
            )


def test_reactivation_no_overlap_between_buckets():
    """A single lead should appear in exactly one bucket."""
    lead_id = _add_with_last_contact(45)
    in_30 = any(l["id"] == lead_id for l in get_reactivation_leads(30))
    in_60 = any(l["id"] == lead_id for l in get_reactivation_leads(60))
    in_90 = any(l["id"] == lead_id for l in get_reactivation_leads(90))
    assert sum([in_30, in_60, in_90]) == 1, "Lead should appear in exactly one reactivation bucket"
