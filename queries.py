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
