"""
queries.py - Core SQL queries for lead commands

Note on update_lead: the SET clause is built from an allowlisted dict, not raw user input.
The allowlist (`allowed` set) prevents SQL injection — only whitelisted column names
can appear in the query. Values are always passed as parameterized bindings.
"""

import json
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
                WHERE status NOT IN ('lost', 'paid', 'won')
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
                WHERE status NOT IN ('lost', 'paid', 'won')
                  AND (date(created_at) = ? OR date(follow_up_after) = ?)
                ORDER BY follow_up_after ASC, created_at ASC
                """,
                (today, today),
            ).fetchall()
    return rows


def get_stale_leads(user_id: Optional[int] = None):
    """Leads where follow_up_after has passed and status is not won/lost/paid."""
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT * FROM leads
                WHERE status NOT IN ('lost', 'paid', 'won')
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
                WHERE status NOT IN ('lost', 'paid', 'won')
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
    """All leads not in won/lost/paid state."""
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
    """Return (rows_by_status, totals_row) with open/closed value split.

    Won is treated as equivalent to paid for reporting purposes.
    Existing 'won' rows in the DB are counted under the 'paid' bucket.
    """
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT
                    CASE WHEN status = 'won' THEN 'paid' ELSE status END as status,
                    COUNT(*) as count,
                    COALESCE(SUM(quote_amount), 0) as total_quoted
                FROM leads
                WHERE user_id = ?
                GROUP BY CASE WHEN status = 'won' THEN 'paid' ELSE status END
                ORDER BY status
                """,
                (user_id,),
            ).fetchall()
            totals = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status NOT IN ('won','lost','paid') THEN quote_amount ELSE 0 END), 0) as open_value,
                    COALESCE(SUM(CASE WHEN status IN ('won','paid') THEN quote_amount ELSE 0 END), 0) as paid_value,
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
                    CASE WHEN status = 'won' THEN 'paid' ELSE status END as status,
                    COUNT(*) as count,
                    COALESCE(SUM(quote_amount), 0) as total_quoted
                FROM leads
                GROUP BY CASE WHEN status = 'won' THEN 'paid' ELSE status END
                ORDER BY status
                """
            ).fetchall()
            totals = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status NOT IN ('won','lost','paid') THEN quote_amount ELSE 0 END), 0) as open_value,
                    COALESCE(SUM(CASE WHEN status IN ('won','paid') THEN quote_amount ELSE 0 END), 0) as paid_value,
                    COALESCE(SUM(CASE WHEN status = 'lost' THEN quote_amount ELSE 0 END), 0) as lost_value
                FROM leads
                """
            ).fetchone()
    return rows, totals


def get_closed_summary(user_id: Optional[int] = None):
    """Paid/won/lost breakdown with loss reasons. 'won' is merged into 'paid' for display."""
    uid_clause = "AND user_id = ?" if user_id is not None else ""
    uid_params = (user_id,) if user_id is not None else ()
    with get_conn() as conn:
        closed = conn.execute(
            f"""
            SELECT
                CASE WHEN status = 'won' THEN 'paid' ELSE status END as status,
                COUNT(*) as count,
                COALESCE(SUM(quote_amount), 0) as total
            FROM leads WHERE status IN ('won', 'lost', 'paid') {uid_clause}
            GROUP BY CASE WHEN status = 'won' THEN 'paid' ELSE status END
            """,
            uid_params,
        ).fetchall()
        loss_reasons = conn.execute(
            f"""
            SELECT lost_reason, COUNT(*) as count
            FROM leads
            WHERE status = 'lost' AND lost_reason IS NOT NULL {uid_clause}
            GROUP BY lost_reason ORDER BY count DESC
            """,
            uid_params,
        ).fetchall()
    return closed, loss_reasons


def get_closed_leads(user_id: Optional[int] = None):
    """Return all closed leads (won / lost / paid) directly from SQL."""
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT * FROM leads
                WHERE status IN ('won', 'lost', 'paid')
                  AND user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM leads
                WHERE status IN ('won', 'lost', 'paid')
                ORDER BY created_at DESC
                """
            ).fetchall()
    return rows


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
    lead_source: Optional[str] = None,
    requested_date: Optional[str] = None,
    requested_time_window: Optional[str] = None,
    service_address: Optional[str] = None,
):
    """Insert a new lead. Also returns existing leads with the exact same name (duplicate warning)."""
    _, existing = get_lead_by_name(name, user_id=user_id)
    duplicates = [r for r in existing if r["name"].lower() == name.lower()]

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO leads
                (name, phone, email, service, status, created_at,
                 last_contact_at, follow_up_after, notes, user_id,
                 lead_source, requested_date, requested_time_window, service_address)
            VALUES
                (?, ?, ?, ?, 'new', datetime('now'), datetime('now'),
                 datetime('now', ? || ' days'), ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                phone,
                email,
                service,
                f"+{followup_days}",
                notes,
                user_id,
                lead_source,
                requested_date,
                requested_time_window,
                service_address,
            ),
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
        "name",
        "phone",
        "email",
        "service",
        "notes",
        "follow_up_after",
        "scheduled_date",
        "invoice_amount",
        "next_service_due_at",
        "invoice_reminder_at",
        "service_reminder_at",
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


def log_event(
    conn,
    event_type: str,
    user_id: Optional[int] = None,
    lead_id: Optional[int] = None,
    meta: Optional[dict] = None,
):
    """Insert a usage event into the event_log table.

    Designed to be called inside an existing connection context.
    meta is a dict or None; stored as JSON string.
    """
    meta_str = json.dumps(meta) if meta is not None else None
    conn.execute(
        "INSERT INTO event_log (event_type, user_id, lead_id, meta) VALUES (?, ?, ?, ?)",
        (event_type, user_id, lead_id, meta_str),
    )


def get_event_counts(days: Optional[int] = None, user_id: Optional[int] = None) -> list:
    """Return event counts by type.

    Pass days=30 for last 30 days, None for all-time.
    Pass user_id to scope results to a single user; omit for global counts (CLI).
    """
    with get_conn() as conn:
        if days is not None and user_id is not None:
            rows = conn.execute(
                """
                SELECT event_type, COUNT(*) as count
                FROM event_log
                WHERE date(created_at) >= date('now', ? || ' days')
                  AND user_id = ?
                GROUP BY event_type
                ORDER BY count DESC
                """,
                (f"-{days}", user_id),
            ).fetchall()
        elif days is not None:
            rows = conn.execute(
                """
                SELECT event_type, COUNT(*) as count
                FROM event_log
                WHERE date(created_at) >= date('now', ? || ' days')
                GROUP BY event_type
                ORDER BY count DESC
                """,
                (f"-{days}",),
            ).fetchall()
        elif user_id is not None:
            rows = conn.execute(
                """
                SELECT event_type, COUNT(*) as count
                FROM event_log
                WHERE user_id = ?
                GROUP BY event_type
                ORDER BY count DESC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT event_type, COUNT(*) as count
                FROM event_log
                GROUP BY event_type
                ORDER BY count DESC
                """
            ).fetchall()
    return rows


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
        log_event(conn, "quote_sent", user_id=user_id, lead_id=lead_id)


def mark_won(lead_id: int, user_id: Optional[int] = None):
    """Mark a lead as 'won'. In the new lifecycle 'paid' is preferred.
    'won' is kept for backward-compat but treated as paid in summary queries.
    """
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
        log_event(conn, "lead_paid", user_id=user_id, lead_id=lead_id, meta={"via": "won"})


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
    Supports: name, service, phone, email, notes, followup_days, quote_amount.
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
        quote_amount = None
        raw_quote = (row.get("quote_amount") or "").strip()
        if raw_quote:
            try:
                quote_amount = float(raw_quote)
                if quote_amount <= 0:
                    quote_amount = None
            except (ValueError, TypeError):
                pass  # ignore unparseable quote amounts
        try:
            lead_id, _ = add_lead(
                name, service, phone=phone, email=email, notes=notes, followup_days=followup_days
            )
            if quote_amount is not None:
                update_quote(lead_id, quote_amount, followup_days=followup_days)
            imported += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"Row {i + 1} ({name}): {e}")
            skipped += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


def mark_stale_leads_followup_due(user_id: Optional[int] = None) -> int:
    """Auto-promote overdue new/quoted leads to followup_due. Returns count updated.

    Pass user_id to restrict to a single user (e.g. per-user web action).
    Omit for the global daily scheduler run which covers all users.
    """
    uid_clause = "AND user_id = ?" if user_id is not None else ""
    uid_params = (user_id,) if user_id is not None else ()
    with get_conn() as conn:
        cur = conn.execute(
            f"""
            UPDATE leads
            SET status = 'followup_due'
            WHERE status IN ('new', 'quoted')
              AND date(follow_up_after) < date('now')
              {uid_clause}
            """,
            uid_params,
        )
        return cur.rowcount


def mark_booked(
    lead_id: int,
    scheduled_date: str,
    scheduled_time_window: Optional[str] = None,
    user_id: Optional[int] = None,
):
    """Mark a lead as booked with a scheduled job date and optional confirmed time window."""
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    base_params_tail = (lead_id, user_id) if user_id is not None else (lead_id,)

    # Optionally set scheduled_time_window in the same statement
    tw_set = "scheduled_time_window = ?," if scheduled_time_window is not None else ""
    tw_val: tuple = (scheduled_time_window,) if scheduled_time_window is not None else ()

    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE leads
            SET status = 'booked',
                scheduled_date = ?,
                {tw_set}
                booked_at = datetime('now'),
                last_contact_at = datetime('now'),
                follow_up_after = NULL
            {where}
            """,
            (scheduled_date, *tw_val, *base_params_tail),
        )
        log_event(conn, "lead_booked", user_id=user_id, lead_id=lead_id)


def mark_completed(lead_id: int, user_id: Optional[int] = None):
    """Mark a booked job as completed. Auto-sets review_reminder_at to tomorrow.
    Also auto-fills next_service_due_at based on service type if not already set.
    """
    from leadclaw.service_defaults import get_service_interval

    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (lead_id, user_id) if user_id is not None else (lead_id,)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT service, next_service_due_at FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        service_type = row["service"] if row else ""
        already_set = row["next_service_due_at"] if row else None

        # Only auto-set service dates when not already explicitly scheduled.
        # svc_fields ends with a comma so it can be embedded before the next SET item.
        svc_fields = ""
        if not already_set:
            interval = get_service_interval(service_type or "")
            svc_fields = (
                f"next_service_due_at = date('now', '+{interval} days'),"
                f" service_reminder_at = date('now', '+{interval} days'),"
            )

        conn.execute(
            f"""
            UPDATE leads
            SET status = 'completed',
                completed_at = datetime('now'),
                last_contact_at = datetime('now'),
                {svc_fields}
                review_reminder_at = date('now', '+1 days')
            {where}
            """,
            params,
        )
        log_event(conn, "lead_completed", user_id=user_id, lead_id=lead_id)


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
        amount = (
            invoice_amount if invoice_amount is not None else (row["quote_amount"] if row else None)
        )

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
        log_event(conn, "invoice_sent", user_id=user_id, lead_id=lead_id)


def mark_paid(
    lead_id: int,
    recurring_days: Optional[int] = None,
    user_id: Optional[int] = None,
):
    """Mark a lead as paid. Optionally schedule a recurring service reminder.

    Auto-sets next_service_due_at from service type intervals if not already set.
    Does NOT override an existing explicit next_service_due_at.
    Also sets review_reminder_at to tomorrow if not already set.
    """
    from leadclaw.service_defaults import get_service_interval

    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params_base = (lead_id, user_id) if user_id is not None else (lead_id,)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT service, next_service_due_at FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        already_set = row["next_service_due_at"] if row else None
        service_type = row["service"] if row else ""

        # Only auto-set service dates when not already explicitly scheduled.
        # svc_fields ends with a comma so it can be embedded before the next SET item.
        svc_fields = ""
        if not already_set:
            # explicit arg > service-type default
            days = (
                recurring_days
                if recurring_days is not None
                else get_service_interval(service_type or "")
            )
            svc_fields = (
                f"next_service_due_at = date('now', '+{days} days'),"
                f" service_reminder_at = date('now', '+{days} days'),"
            )

        conn.execute(
            f"""
            UPDATE leads
            SET status = 'paid',
                paid_at = datetime('now'),
                invoice_reminder_at = NULL,
                {svc_fields}
                last_contact_at = datetime('now'),
                follow_up_after = NULL,
                review_reminder_at = COALESCE(review_reminder_at, date('now', '+1 days'))
            {where}
            """,
            params_base,
        )
        log_event(conn, "lead_paid", user_id=user_id, lead_id=lead_id)


def set_next_service(
    lead_id: int,
    next_service_due_at: str,
    user_id: Optional[int] = None,
):
    """Manually set or update the next_service_due_at date."""
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (
        (next_service_due_at, next_service_due_at, lead_id, user_id)
        if user_id is not None
        else (next_service_due_at, next_service_due_at, lead_id)
    )
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
        log_event(
            conn,
            "next_service_set",
            user_id=user_id,
            lead_id=lead_id,
            meta={"date": next_service_due_at},
        )


def get_invoice_reminders(user_id: Optional[int] = None):
    """Leads where invoice_reminder_at has passed and lead is not yet paid."""
    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                """
                SELECT * FROM leads
                WHERE status NOT IN ('paid', 'won', 'lost')
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
            WHERE status NOT IN ('paid', 'won', 'lost')
              AND invoice_reminder_at IS NOT NULL
              AND date(invoice_reminder_at) <= date('now')
            ORDER BY invoice_reminder_at ASC
            """
        ).fetchall()


def get_job_today_leads(user_id: Optional[int] = None):
    """Booked leads with scheduled_date = today (not dismissed for today)."""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                """
                SELECT * FROM leads
                WHERE status = 'booked'
                  AND date(scheduled_date) = ?
                  AND (job_reminder_dismissed_at IS NULL OR job_reminder_dismissed_at != ?)
                  AND user_id = ?
                ORDER BY scheduled_date ASC
                """,
                (today, today, user_id),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM leads
            WHERE status = 'booked'
              AND date(scheduled_date) = ?
              AND (job_reminder_dismissed_at IS NULL OR job_reminder_dismissed_at != ?)
            ORDER BY scheduled_date ASC
            """,
            (today, today),
        ).fetchall()


def get_review_reminders(user_id: Optional[int] = None):
    """Leads where review_reminder_at is today or past, and review not yet sent.

    review_request_sent_at being set indicates the review was already requested — exclude those.
    """
    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                """
                SELECT * FROM leads
                WHERE review_reminder_at IS NOT NULL
                  AND date(review_reminder_at) <= date('now')
                  AND review_request_sent_at IS NULL
                  AND user_id = ?
                ORDER BY review_reminder_at ASC
                """,
                (user_id,),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM leads
            WHERE review_reminder_at IS NOT NULL
              AND date(review_reminder_at) <= date('now')
              AND review_request_sent_at IS NULL
            ORDER BY review_reminder_at ASC
            """
        ).fetchall()


def get_reactivation_leads(days: int, user_id: Optional[int] = None):
    """
    Leads in pre-job statuses (new, quoted, followup_due) with no last_contact_at
    activity in the given range. Excludes dismissed reactivations.

    Bucket ranges (non-overlapping):
      days=30  → last_contact_at is 30-59 days ago
      days=60  → last_contact_at is 60-89 days ago
      days=90  → last_contact_at is 90+ days ago (no upper bound)

    Accepts `days` as the lower bound; upper bound is days+30 (except 90 which is open).
    """
    lower = f"-{days}"
    if days >= 90:
        # Open upper bound: 90+ days
        upper_clause = ""
        upper_params: tuple = ()
    else:
        upper_clause = "AND date(last_contact_at) > date('now', ? || ' days')"
        upper_params = (f"-{days + 30}",)

    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                f"""
                SELECT * FROM leads
                WHERE status IN ('new', 'quoted', 'followup_due')
                  AND last_contact_at IS NOT NULL
                  AND date(last_contact_at) <= date('now', ? || ' days')
                  {upper_clause}
                  AND reactivation_dismissed_at IS NULL
                  AND user_id = ?
                ORDER BY last_contact_at ASC
                """,
                (lower, *upper_params, user_id),
            ).fetchall()
        return conn.execute(
            f"""
            SELECT * FROM leads
            WHERE status IN ('new', 'quoted', 'followup_due')
              AND last_contact_at IS NOT NULL
              AND date(last_contact_at) <= date('now', ? || ' days')
              {upper_clause}
              AND reactivation_dismissed_at IS NULL
            ORDER BY last_contact_at ASC
            """,
            (lower, *upper_params),
        ).fetchall()


def get_unseen_requests(user_id: Optional[int] = None):
    """Public request leads that the owner hasn't seen yet and are still unbooked/actionable."""
    uid_clause = "AND user_id = ?" if user_id is not None else ""
    uid_params = (user_id,) if user_id is not None else ()
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT * FROM leads
            WHERE lead_source = 'public_request'
              AND request_seen_at IS NULL
              AND status NOT IN ('booked','completed','paid','won','lost')
              {uid_clause}
            ORDER BY created_at DESC
            """,
            uid_params,
        ).fetchall()


def mark_request_seen(lead_id: int, user_id: Optional[int] = None) -> bool:
    """Mark a public request as seen. Returns True if the lead was found."""
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (lead_id, user_id) if user_id is not None else (lead_id,)
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE leads SET request_seen_at = datetime('now') {where}",
            params,
        )
        return cur.rowcount > 0


def mark_all_requests_seen(user_id: Optional[int] = None) -> int:
    """Mark all unseen public requests as seen. Returns count updated."""
    uid_clause = "AND user_id = ?" if user_id is not None else ""
    uid_params = (user_id,) if user_id is not None else ()
    with get_conn() as conn:
        cur = conn.execute(
            f"""
            UPDATE leads SET request_seen_at = datetime('now')
            WHERE lead_source = 'public_request'
              AND request_seen_at IS NULL
              {uid_clause}
            """,
            uid_params,
        )
        return cur.rowcount


def get_public_requests(
    user_id: Optional[int] = None,
    filter: str = "unbooked",
):
    """Return leads where lead_source = 'public_request'.

    filter='unbooked' (default): pending, not yet booked/closed
    filter='booked':  booked/completed/paid/won requests
    filter='all':     every public_request lead
    """
    if filter == "unbooked":
        status_clause = "AND status NOT IN ('booked','completed','paid','won','lost')"
    elif filter == "booked":
        status_clause = "AND status IN ('booked','completed','paid','won')"
    else:
        status_clause = ""

    uid_clause = "AND user_id = ?" if user_id is not None else ""
    uid_params = (user_id,) if user_id is not None else ()

    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT * FROM leads
            WHERE lead_source = 'public_request'
              {status_clause}
              {uid_clause}
            ORDER BY created_at DESC
            """,
            uid_params,
        ).fetchall()


def set_review_reminder(lead_id: int, days: int = 1, user_id: Optional[int] = None):
    """Manually set or update review_reminder_at for a lead.

    Pass days=0 to set it to today (useful in tests).
    A future 'dismiss' action can call this with NULL or just UPDATE directly.
    """
    modifier = f"+{days} days"
    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params = (modifier, lead_id, user_id) if user_id is not None else (modifier, lead_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE leads SET review_reminder_at = date('now', ?) {where}",
            params,
        )


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


# ---------------------------------------------------------------------------
# Reminder dismissal
# ---------------------------------------------------------------------------

DISMISSAL_FIELDS = {
    "review_request": "review_request_sent_at",
    "reactivation": "reactivation_dismissed_at",
    "job_today": "job_reminder_dismissed_at",
}


def dismiss_reminder(
    conn,
    lead_id: int,
    reminder_type: str,
    user_id: Optional[int] = None,
) -> bool:
    """Set the dismissal timestamp for a reminder type on a lead.

    reminder_type must be one of: review_request, reactivation, job_today.
    For job_today, stores the date string (YYYY-MM-DD) so it resets daily.
    Returns True if the lead was found and updated, False otherwise.

    Designed to be called inside an existing connection context.
    """
    col = DISMISSAL_FIELDS.get(reminder_type)
    if not col:
        return False

    where = "WHERE id = ? AND user_id = ?" if user_id is not None else "WHERE id = ?"
    params_base = (lead_id, user_id) if user_id is not None else (lead_id,)

    if reminder_type == "job_today":
        # Store current date so the dismissal resets the next day
        value = datetime.now().strftime("%Y-%m-%d")
    else:
        value = datetime.now().isoformat()

    cur = conn.execute(
        f"UPDATE leads SET {col} = ? {where}",  # noqa: S608 — col is from allowlist
        (value, *params_base),
    )
    log_event(
        conn, "reminder_dismissed", user_id=user_id, lead_id=lead_id, meta={"type": reminder_type}
    )
    return cur.rowcount > 0


def dismiss_reminder_standalone(
    lead_id: int,
    reminder_type: str,
    user_id: Optional[int] = None,
) -> bool:
    """Standalone version of dismiss_reminder that opens its own connection."""
    with get_conn() as conn:
        return dismiss_reminder(conn, lead_id, reminder_type, user_id=user_id)
