"""
queries.py - Core SQL queries for lead commands
"""
from db import get_conn
from datetime import datetime


def get_today_leads():
    """Leads created today or with a follow_up_after of today."""
    conn = get_conn()
    cur = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cur.execute(
        """
        SELECT * FROM leads
        WHERE date(created_at) = ?
           OR date(follow_up_after) = ?
        ORDER BY follow_up_after ASC, created_at ASC
        """,
        (today, today),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_stale_leads():
    """Leads where follow_up_after has passed and status is not won/lost."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM leads
        WHERE status NOT IN ('won', 'lost')
          AND follow_up_after < datetime('now')
        ORDER BY follow_up_after ASC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_lead_by_name(name):
    """Find a lead by name (case-insensitive partial match)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM leads WHERE name LIKE ? ORDER BY created_at DESC LIMIT 1",
        (f"%{name}%",),
    )
    row = cur.fetchone()
    conn.close()
    return row


def mark_stale_leads_followup_due():
    """
    Auto-promote any quoted/new lead whose follow_up_after has passed
    to 'followup_due'. Returns count of updated rows.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE leads
        SET status = 'followup_due'
        WHERE status IN ('new', 'quoted')
          AND follow_up_after < datetime('now')
        """
    )
    updated = cur.rowcount
    conn.commit()
    conn.close()
    return updated


def update_lead_status(lead_id, status, lost_reason=None, lost_reason_notes=None):
    """Update the status (and optionally lost_reason) of a lead."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE leads
        SET status = ?,
            lost_reason = ?,
            lost_reason_notes = ?
        WHERE id = ?
        """,
        (status, lost_reason, lost_reason_notes, lead_id),
    )
    conn.commit()
    conn.close()


def get_pipeline_summary():
    """Return counts and total quote value by status."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            status,
            COUNT(*) as count,
            COALESCE(SUM(quote_amount), 0) as total_quoted
        FROM leads
        GROUP BY status
        ORDER BY status
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_active_leads():
    """All leads not in won/lost state."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM leads
        WHERE status NOT IN ('won', 'lost')
        ORDER BY follow_up_after ASC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows
