"""
tests/test_lifecycle.py - Tests for the extended lead lifecycle:
booked -> completed -> paid + invoice/service reminders
"""

import os

import pytest

from leadclaw import db
from tests.conftest import TEST_DB


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _add_lead():
    from leadclaw.queries import add_lead, get_lead_by_id

    lead_id, _ = add_lead("Test Lead", "Lawn Care")
    return get_lead_by_id(lead_id)


def test_mark_booked_sets_status_and_date():
    from leadclaw.queries import get_lead_by_id, mark_booked

    lead = _add_lead()
    mark_booked(lead["id"], "2026-06-01")
    updated = get_lead_by_id(lead["id"])
    assert updated["status"] == "booked"
    assert updated["scheduled_date"] == "2026-06-01"
    assert updated["booked_at"] is not None
    assert updated["follow_up_after"] is None


def test_mark_completed_sets_status_and_timestamp():
    from leadclaw.queries import get_lead_by_id, mark_booked, mark_completed

    lead = _add_lead()
    mark_booked(lead["id"], "2026-06-01")
    mark_completed(lead["id"])
    updated = get_lead_by_id(lead["id"])
    assert updated["status"] == "completed"
    assert updated["completed_at"] is not None


def test_mark_invoice_sent_sets_timestamps():
    from leadclaw.queries import get_lead_by_id, mark_booked, mark_completed, mark_invoice_sent

    lead = _add_lead()
    mark_booked(lead["id"], "2026-06-01")
    mark_completed(lead["id"])
    mark_invoice_sent(lead["id"], invoice_amount=850.0, reminder_days=3)
    updated = get_lead_by_id(lead["id"])
    assert updated["invoice_sent_at"] is not None
    assert updated["invoice_reminder_at"] is not None
    assert updated["invoice_amount"] == 850.0


def test_mark_invoice_sent_defaults_to_quote_amount():
    from leadclaw.queries import get_lead_by_id, mark_invoice_sent, update_quote

    lead = _add_lead()
    update_quote(lead["id"], 750.0)
    mark_invoice_sent(lead["id"])
    updated = get_lead_by_id(lead["id"])
    assert updated["invoice_amount"] == 750.0


def test_mark_paid_sets_status_and_service_reminder():
    from leadclaw.queries import (
        get_lead_by_id,
        mark_booked,
        mark_completed,
        mark_invoice_sent,
        mark_paid,
    )

    lead = _add_lead()
    mark_booked(lead["id"], "2026-06-01")
    mark_completed(lead["id"])
    mark_invoice_sent(lead["id"], invoice_amount=900.0)
    mark_paid(lead["id"], recurring_days=30)
    updated = get_lead_by_id(lead["id"])
    assert updated["status"] == "paid"
    assert updated["paid_at"] is not None
    assert updated["next_service_due_at"] is not None
    assert updated["service_reminder_at"] is not None
    assert updated["invoice_reminder_at"] is None  # cleared on paid


def test_set_next_service_updates_date():
    from leadclaw.queries import (
        get_lead_by_id,
        mark_booked,
        mark_completed,
        mark_invoice_sent,
        mark_paid,
        set_next_service,
    )

    lead = _add_lead()
    mark_booked(lead["id"], "2026-06-01")
    mark_completed(lead["id"])
    mark_invoice_sent(lead["id"])
    mark_paid(lead["id"])
    set_next_service(lead["id"], "2027-01-01")
    updated = get_lead_by_id(lead["id"])
    assert updated["next_service_due_at"] == "2027-01-01"
    assert updated["service_reminder_at"] == "2027-01-01"


def test_get_invoice_reminders_returns_overdue():
    """Set invoice_reminder_at to past date and confirm it shows up."""
    from leadclaw.db import get_conn
    from leadclaw.queries import (
        get_invoice_reminders,
        mark_booked,
        mark_completed,
        mark_invoice_sent,
    )

    lead = _add_lead()
    mark_booked(lead["id"], "2026-06-01")
    mark_completed(lead["id"])
    mark_invoice_sent(lead["id"], invoice_amount=500.0, reminder_days=3)

    # Force reminder to past
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET invoice_reminder_at = '2020-01-01' WHERE id = ?",
            (lead["id"],),
        )

    due = get_invoice_reminders()
    ids = [r["id"] for r in due]
    assert lead["id"] in ids


def test_get_invoice_reminders_excludes_paid():
    from leadclaw.db import get_conn
    from leadclaw.queries import (
        get_invoice_reminders,
        mark_booked,
        mark_completed,
        mark_invoice_sent,
        mark_paid,
    )

    lead = _add_lead()
    mark_booked(lead["id"], "2026-06-01")
    mark_completed(lead["id"])
    mark_invoice_sent(lead["id"])
    # Force reminder to past then mark paid
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET invoice_reminder_at = '2020-01-01' WHERE id = ?",
            (lead["id"],),
        )
    mark_paid(lead["id"])

    due = get_invoice_reminders()
    ids = [r["id"] for r in due]
    assert lead["id"] not in ids


def test_get_service_reminders_returns_overdue():
    from leadclaw.db import get_conn
    from leadclaw.queries import (
        get_service_reminders,
        mark_booked,
        mark_completed,
        mark_invoice_sent,
        mark_paid,
    )

    lead = _add_lead()
    mark_booked(lead["id"], "2026-06-01")
    mark_completed(lead["id"])
    mark_invoice_sent(lead["id"])
    mark_paid(lead["id"], recurring_days=90)

    # Force service_reminder_at to past
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET service_reminder_at = '2020-01-01' WHERE id = ?",
            (lead["id"],),
        )

    due = get_service_reminders()
    ids = [r["id"] for r in due]
    assert lead["id"] in ids


def test_get_service_reminders_excludes_future():
    from leadclaw.queries import (
        get_service_reminders,
        mark_booked,
        mark_completed,
        mark_invoice_sent,
        mark_paid,
    )

    lead = _add_lead()
    mark_booked(lead["id"], "2026-06-01")
    mark_completed(lead["id"])
    mark_invoice_sent(lead["id"])
    mark_paid(lead["id"], recurring_days=3650)  # 10 years out

    due = get_service_reminders()
    ids = [r["id"] for r in due]
    assert lead["id"] not in ids
