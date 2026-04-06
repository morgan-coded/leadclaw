"""
queries.py - Core SQL queries for lead commands

Note on update_lead: the SET clause is built from an allowlisted dict, not raw user input.
The allowlist (`allowed` set) prevents SQL injection — only whitelisted column names
can appear in the query. Values are always passed as parameterized bindings.
"""

from datetime import datetime  # noqa: I001
from typing import Optional

from leadclaw.config import DEFAULT_FOLLOWUP_DAYS
from leadclaw.db import get_conn

# ---------------------------------------------------------------------------
# Read queries
# ---------------------------------------------------------------------------


def get_today_leads(user_id: Optional[int] = None):
    """Active leads created today or with a follow_up_after of today."""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT * FROM leads
                WHERE status NOT IN ('won', 'lost', 'paid')
                  AND (date(created_at) = ? OR date(follow_up_after) = ?)
                  AND user_id = ?
                ORDER BY follow_up_after ASC, created_at ASC
                """,
                (today, today, user_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM leads
                WHERE status NOT IN ('won', 'lost', 'paid')
                  AND (date(created_at) = ? OR date(follow_up_after) = ?)
                ORDER BY follow_up_after ASC, created_at ASC
                """,
                (today, today),
            ).fetchall()
    return rows


def get_stale_leads(user_id: Optional[int] = None):
    """Leads where follow_up_after has passed and status is not won/lost."""
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT * FROM leads
                WHERE status NOT IN ('won', 'lost', 'paid')
                  AND date(follow_up_after) < date('now')
                  AND user_id = ?
                ORDER BY follow_up_after ASC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM leads
                WHERE status NOT IN ('won', 'lost', 'paid')
                  AND date(follow_up_after) < date('now')
                ORDER BY follow_up_after ASC
                """
            ).fetchall()
    return rows


def get_lead_by_name(name: str, user_id: Optional[int] = None):
    """
    Find leads by case-insensitive partial match.
    Escapes % and _ to prevent unbounded LIKE matches.
    Returns (best_match_or_None, all_matches_list).
    If user_id is given, only returns that user's leads.
    """
    safe = name.replace("%", r"\%").replace("_", r"\_")
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM leads WHERE name LIKE ? ESCAPE '\\' AND user_id = ? ORDER BY created_at DESC",
                (f"%{safe}%", user_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM leads WHERE name LIKE ? ESCAPE '\\' ORDER BY created_at DESC",
                (f"%{safe}%",),
            ).fetchall()
    if not rows:
        return None, []
    return rows[0], rows


def get_lead_by_id(lead_id: int, user_id: Optional[int] = None):
    """Fetch a lead by id. If user_id is given, also enforce ownership."""
    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                "SELECT * FROM leads WHERE id = ? AND user_id = ?",
                (lead_id, user_id),
            ).fetchone()
        return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


def get_all_active_leads(user_id: Optional[int] = None):
    """All leads not in won/lost state."""
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT * FROM leads
                WHERE status NOT IN ('won', 'lost', 'paid')
                  AND user_id = ?
                ORDER BY follow_up_after ASC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM leads
                WHERE status NOT IN ('won', 'lost', 'paid')
                ORDER BY follow_up_after ASC
                """
            ).fetchall()
    return rows


def get_all_leads(limit: int = 200, offset: int = 0, user_id: Optional[int] = None):
    """Every lead, all statuses, with pagination."""
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM leads WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM leads ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return rows


def get_pipeline_summary(user_id: Optional[int] = None):
    """Return (rows_by_status, totals_row) with open/closed value split."""
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT
                    status,
                    COUNT(*) as count,
                    COALESCE(SUM(quote_amount), 0) as total_quoted
                FROM leads
                WHERE user_id = ?
                GROUP BY status
                ORDER BY status
                """,
                (user_id,),
            ).fetchall()
            totals = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status NOT IN ('won','lost') THEN quote_amount ELSE 0 END), 0) as open_value,
                    COALESCE(SUM(CASE WHEN status = 'won'  THEN quote_amount ELSE 0 END), 0) as won_value,
                    COALESCE(SUM(CASE WHEN status = 'lost' THEN quote_amount ELSE 0 END), 0) as lost_value
                FROM leads
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        else:
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
    user_id: int = 1,
):
    """Insert a new lead. Also returns existing leads with the exact same name (duplicate warning)."""
    _, existing = get_lead_by_name(name, user_id=user_id)
    duplicates = [r for r in existing if r["name"].lower() == name.lower()]

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO leads
                (name, phone, email, service, status, created_at,
                 last_contact_at, follow_up_after, notes, user_id)
            VALUES
                (?, ?, ?, ?, 'new', datetime('now'), datetime('now'),
                 datetime('now', ? || ' days'), ?, ?)
            """,
            (name, phone, email, service, f"+{followup_days}", notes, user_id),
        )
        lead_id = cur.lastrowid

    return lead_id, duplicates


def update_lead(lead_id: int, user_id: Optional[int] = None, **fields):
    """
    Generic field updater. Only fields in `allowed` can be updated.
    The SET clause is built from the allowlist (not raw user input),
    so column names are safe. Values are always parameterized.
    If user_id is given, the update is restricted to that user's lead.
    """
    allowed = {
        "name", "phone", "email", "service", "notes", "follow_up_after",
        "scheduled_date", "invoice_amount", "next_service_due_at",
        "invoice_reminder_at", "service_reminder_at",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (
        (*updates.values(), lead_id, user_id)
        if user_id is not None
        else (*updates.values(), lead_id)
    )
    with get_conn() as conn:
        conn.execute(
            f"UPDATE leads SET {set_clause} {where}",  # noqa: S608 — safe, allowlisted
            params,
        )


def delete_lead(lead_id: int, user_id: Optional[int] = None):
    with get_conn() as conn:
        if user_id is not None:
            conn.execute("DELETE FROM leads WHERE id = ? AND user_id = ?", (lead_id, user_id))
        else:
            conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))


def update_quote(
    lead_id: int,
    amount: float,
    followup_days: int = DEFAULT_FOLLOWUP_DAYS,
    user_id: Optional[int] = None,
):
    """Set/update quote, log contact time, and schedule next follow-up."""
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (
        (amount, f"+{followup_days}", lead_id, user_id)
        if user_id is not None
        else (amount, f"+{followup_days}", lead_id)
    )
    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE leads
            SET quote_amount = ?,
                status = 'quoted',
                last_contact_at = datetime('now'),
                follow_up_after = datetime('now', ? || ' days')
            {where}
            """,
            params,
        )


def mark_won(lead_id: int, user_id: Optional[int] = None):
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (lead_id, user_id) if user_id is not None else (lead_id,)
    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE leads
            SET status = 'won',
                last_contact_at = datetime('now'),
                follow_up_after = NULL
            {where}
            """,
            params,
        )


def mark_lost(
    lead_id: int, reason: str, notes: Optional[str] = None, user_id: Optional[int] = None
):
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (reason, notes, lead_id, user_id) if user_id is not None else (reason, notes, lead_id)
    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE leads
            SET status = 'lost',
                lost_reason = ?,
                lost_reason_notes = ?,
                last_contact_at = datetime('now'),
                follow_up_after = NULL
            {where}
            """,
            params,
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
            add_lead(
                name, service, phone=phone, email=email, notes=notes, followup_days=followup_days
            )
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
              AND date(follow_up_after) < date('now')
            """
        )
        return cur.rowcount


def mark_booked(lead_id: int, scheduled_date: str, user_id: Optional[int] = None):
    """Mark a lead as booked with a scheduled job date."""
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (scheduled_date, lead_id, user_id) if user_id is not None else (scheduled_date, lead_id)
    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE leads
            SET status = 'booked',
                scheduled_date = ?,
                booked_at = datetime('now'),
                last_contact_at = datetime('now'),
                follow_up_after = NULL
            {where}
            """,
            params,
        )


def mark_completed(lead_id: int, user_id: Optional[int] = None):
    """Mark a booked job as completed."""
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (lead_id, user_id) if user_id is not None else (lead_id,)
    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE leads
            SET status = 'completed',
                completed_at = datetime('now'),
                last_contact_at = datetime('now')
            {where}
            """,
            params,
        )


def mark_invoice_sent(
    lead_id: int,
    invoice_amount: Optional[float] = None,
    reminder_days: int = 3,
    user_id: Optional[int] = None,
):
    """Record that an invoice was sent. Optionally override the amount. Schedules a reminder."""
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params_base = (lead_id, user_id) if user_id is not None else (lead_id,)

    with get_conn() as conn:
        # Fetch current quote_amount to use as default invoice_amount
        row = conn.execute("SELECT quote_amount FROM leads WHERE id = ?", (lead_id,)).fetchone()
        amount = invoice_amount if invoice_amount is not None else (row["quote_amount"] if row else None)

        conn.execute(
            f"""
            UPDATE leads
            SET invoice_amount = COALESCE(?, invoice_amount, quote_amount),
                invoice_sent_at = datetime('now'),
                invoice_reminder_at = datetime('now', '+{reminder_days} days'),
                last_contact_at = datetime('now')
            {where}
            """,
            (amount, *params_base),
        )


def mark_paid(
    lead_id: int,
    recurring_days: Optional[int] = None,
    user_id: Optional[int] = None,
):
    """Mark a lead as paid. Optionally schedule a recurring service reminder."""
    from leadclaw.config import DEFAULT_RECURRING_DAYS
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params_base = (lead_id, user_id) if user_id is not None else (lead_id,)
    days = recurring_days if recurring_days is not None else DEFAULT_RECURRING_DAYS

    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE leads
            SET status = 'paid',
                paid_at = datetime('now'),
                invoice_reminder_at = NULL,
                next_service_due_at = date('now', '+{days} days'),
                service_reminder_at = date('now', '+{days} days'),
                last_contact_at = datetime('now'),
                follow_up_after = NULL
            {where}
            """,
            params_base,
        )


def set_next_service(
    lead_id: int,
    next_service_due_at: str,
    user_id: Optional[int] = None,
):
    """Manually set or update the next_service_due_at date."""
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (next_service_due_at, next_service_due_at, lead_id, user_id) if user_id is not None else (next_service_due_at, next_service_due_at, lead_id)
    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE leads
            SET next_service_due_at = ?,
                service_reminder_at = ?
            {where}
            """,
            params,
        )


def get_invoice_reminders(user_id: Optional[int] = None):
    """Leads where invoice_reminder_at has passed and lead is not yet paid."""
    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                """
                SELECT * FROM leads
                WHERE status NOT IN ('paid', 'lost')
                  AND invoice_reminder_at IS NOT NULL
                  AND date(invoice_reminder_at) <= date('now')
                  AND user_id = ?
                ORDER BY invoice_reminder_at ASC
                """,
                (user_id,),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM leads
            WHERE status NOT IN ('paid', 'lost')
              AND invoice_reminder_at IS NOT NULL
              AND date(invoice_reminder_at) <= date('now')
            ORDER BY invoice_reminder_at ASC
            """
        ).fetchall()


def get_service_reminders(user_id: Optional[int] = None):
    """Leads where service_reminder_at is today or past (recurring service due)."""
    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                """
                SELECT * FROM leads
                WHERE service_reminder_at IS NOT NULL
                  AND date(service_reminder_at) <= date('now')
                  AND user_id = ?
                ORDER BY service_reminder_at ASC
                """,
                (user_id,),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM leads
            WHERE service_reminder_at IS NOT NULL
              AND date(service_reminder_at) <= date('now')
            ORDER BY service_reminder_at ASC
            """
        ).fetchall()
