"""
queries.py - Core SQL queries for lead commands

Note on update_lead: the SET clause is built from an allowlisted dict, not raw user input.
The allowlist (`allowed` set) prevents SQL injection — only whitelisted column names
can appear in the query. Values are always passed as parameterized bindings.
"""
from datetime import datetime
from typing import Optional

from leadclaw.config import DEFAULT_FOLLOWUP_DAYS
from leadclaw.db import get_conn


# ---------------------------------------------------------------------------
# Read queries
# ---------------------------------------------------------------------------


def get_today_leads():
    """Active leads created today or with a follow_up_after of today."""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM leads
            WHERE status NOT IN ('won', 'lost')
              AND (date(created_at) = ? OR date(follow_up_after) = ?)
            ORDER BY follow_up_after ASC, created_at ASC
            """,
            (today, today),
        ).fetchall()
    return rows


def get_stale_leads():
    """Leads where follow_up_after has passed and status is not won/lost."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM leads
            WHERE status NOT IN ('won', 'lost')
              AND follow_up_after < datetime('now')
            ORDER BY follow_up_after ASC
            """
        ).fetchall()
    return rows


def get_lead_by_name(name: str):
    """
    Find leads by case-insensitive partial match.
    Escapes % and _ to prevent unbounded LIKE matches.
    Returns (best_match_or_None, all_matches_list).
    """
    safe = name.replace("%", r"\%").replace("_", r"\_")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM leads WHERE name LIKE ? ESCAPE '\\' ORDER BY created_at DESC",
            (f"%{safe}%",),
        ).fetchall()
    if not rows:
        return None, []
    return rows[0], rows


def get_lead_by_id(lead_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


def get_all_active_leads():
    """All leads not in won/lost state."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM leads
            WHERE status NOT IN ('won', 'lost')
            ORDER BY follow_up_after ASC
            """
        ).fetchall()
    return rows


def get_all_leads(limit: int = 200, offset: int = 0):
    """Every lead, all statuses, with pagination."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM leads ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return rows


def get_pipeline_summary():
    """Return (rows_by_status, totals_row) with open/closed value split."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                status,
                COUNT(*) as count,
                COALESCE(SUM(quote_amount), 0) as total_quoted
            FROM leads
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
        totals = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status NOT IN ('won','lost') THEN quote_amount ELSE 0 END), 0) as open_value,
                COALESCE(SUM(CASE WHEN status = 'won'  THEN quote_amount ELSE 0 END), 0) as won_value,
                COALESCE(SUM(CASE WHEN status = 'lost' THEN quote_amount ELSE 0 END), 0) as lost_value
            FROM leads
            """
        ).fetchone()
    return rows, totals


def get_closed_summary():
    """Won/lost breakdown with loss reasons."""
    with get_conn() as conn:
        closed = conn.execute(
            """
            SELECT status, COUNT(*) as count,
                   COALESCE(SUM(quote_amount), 0) as total
            FROM leads WHERE status IN ('won', 'lost') GROUP BY status
            """
        ).fetchall()
        loss_reasons = conn.execute(
            """
            SELECT lost_reason, COUNT(*) as count
            FROM leads
            WHERE status = 'lost' AND lost_reason IS NOT NULL
            GROUP BY lost_reason ORDER BY count DESC
            """
        ).fetchall()
    return closed, loss_reasons


# ---------------------------------------------------------------------------
# Write queries
# ---------------------------------------------------------------------------


def add_lead(
    name: str,
    service: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    notes: Optional[str] = None,
    followup_days: int = DEFAULT_FOLLOWUP_DAYS,
):
    """Insert a new lead. Also returns existing leads with the exact same name (duplicate warning)."""
    _, existing = get_lead_by_name(name)
    duplicates = [r for r in existing if r["name"].lower() == name.lower()]

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO leads
                (name, phone, email, service, status, created_at,
                 last_contact_at, follow_up_after, notes)
            VALUES
                (?, ?, ?, ?, 'new', datetime('now'), datetime('now'),
                 datetime('now', ? || ' days'), ?)
            """,
            (name, phone, email, service, f"+{followup_days}", notes),
        )
        lead_id = cur.lastrowid

    return lead_id, duplicates


def update_lead(lead_id: int, **fields):
    """
    Generic field updater. Only fields in `allowed` can be updated.
    The SET clause is built from the allowlist (not raw user input),
    so column names are safe. Values are always parameterized.
    """
    allowed = {"name", "phone", "email", "service", "notes", "follow_up_after"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE leads SET {set_clause} WHERE id = ?",  # noqa: S608 — safe, allowlisted
            (*updates.values(), lead_id),
        )


def delete_lead(lead_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))


def update_quote(lead_id: int, amount: float, followup_days: int = DEFAULT_FOLLOWUP_DAYS):
    """Set/update quote, log contact time, and schedule next follow-up."""
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE leads
            SET quote_amount = ?,
                status = 'quoted',
                last_contact_at = datetime('now'),
                follow_up_after = datetime('now', ? || ' days')
            WHERE id = ?
            """,
            (amount, f"+{followup_days}", lead_id),
        )


def mark_won(lead_id: int):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE leads
            SET status = 'won',
                last_contact_at = datetime('now'),
                follow_up_after = NULL
            WHERE id = ?
            """,
            (lead_id,),
        )


def mark_lost(lead_id: int, reason: str, notes: Optional[str] = None):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE leads
            SET status = 'lost',
                lost_reason = ?,
                lost_reason_notes = ?,
                last_contact_at = datetime('now'),
                follow_up_after = NULL
            WHERE id = ?
            """,
            (reason, notes, lead_id),
        )


def import_leads_from_rows(rows: list) -> dict:
    """
    Bulk-insert leads from a list of dicts (pre-validated CSV rows).
    Returns {imported, skipped, errors} summary.
    """
    imported = 0
    skipped = 0
    errors = []

    for i, row in enumerate(rows):
        name = (row.get("name") or "").strip()
        service = (row.get("service") or "").strip()
        if not name or not service:
            errors.append(f"Row {i + 1}: missing name or service — skipped")
            skipped += 1
            continue
        phone = (row.get("phone") or "").strip() or None
        email = (row.get("email") or "").strip() or None
        notes = (row.get("notes") or "").strip() or None
        try:
            followup_days = int(row.get("followup_days") or DEFAULT_FOLLOWUP_DAYS)
            if followup_days < 0:
                followup_days = DEFAULT_FOLLOWUP_DAYS
        except (ValueError, TypeError):
            followup_days = DEFAULT_FOLLOWUP_DAYS
        try:
            add_lead(name, service, phone=phone, email=email,
                     notes=notes, followup_days=followup_days)
            imported += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"Row {i + 1} ({name}): {e}")
            skipped += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


def mark_stale_leads_followup_due() -> int:
    """Auto-promote overdue new/quoted leads to followup_due. Returns count updated."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE leads
            SET status = 'followup_due'
            WHERE status IN ('new', 'quoted')
              AND follow_up_after < datetime('now')
            """
        )
        return cur.rowcount
