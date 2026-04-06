"""
availability.py - Owner availability rules for booking.

Stores per-user settings:
  - allowed_weekdays: list of ints, Python weekday convention (0=Mon ... 6=Sun)
  - blocked_dates: list of YYYY-MM-DD strings

All check/compute logic is pure (no DB calls).
get_availability / set_availability are the only DB-touching functions.
"""

import json
from datetime import date, datetime, timedelta
from typing import Optional

from leadclaw.db import get_conn

# Python weekday convention: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_ALLOWED_WEEKDAYS = [0, 1, 2, 3, 4]  # Mon–Fri


def _ensure_row(conn, user_id: int) -> None:
    """Insert a default availability row if one doesn't exist yet."""
    conn.execute(
        """
        INSERT OR IGNORE INTO availability (user_id, allowed_weekdays, blocked_dates)
        VALUES (?, ?, ?)
        """,
        (user_id, json.dumps(DEFAULT_ALLOWED_WEEKDAYS), "[]"),
    )


def get_availability(user_id: int) -> dict:
    """Return availability settings for user_id, creating defaults on first access."""
    with get_conn() as conn:
        _ensure_row(conn, user_id)
        row = conn.execute(
            "SELECT allowed_weekdays, blocked_dates FROM availability WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return {
        "allowed_weekdays": json.loads(row["allowed_weekdays"]),
        "blocked_dates": json.loads(row["blocked_dates"]),
    }


def set_availability(
    user_id: int,
    allowed_weekdays: list,
    blocked_dates: list,
) -> None:
    """Persist availability settings for user_id. Input is sanitized."""
    # Unique ints in 0–6, sorted
    clean_days = sorted({int(d) for d in allowed_weekdays if 0 <= int(d) <= 6})
    # Unique, valid YYYY-MM-DD strings, sorted
    clean_blocked = []
    for d in blocked_dates:
        ds = str(d).strip()
        try:
            datetime.strptime(ds, "%Y-%m-%d")
            clean_blocked.append(ds)
        except ValueError:
            pass
    clean_blocked = sorted(set(clean_blocked))

    with get_conn() as conn:
        _ensure_row(conn, user_id)
        conn.execute(
            """
            UPDATE availability
            SET allowed_weekdays = ?, blocked_dates = ?, updated_at = datetime('now')
            WHERE user_id = ?
            """,
            (json.dumps(clean_days), json.dumps(clean_blocked), user_id),
        )


def check_date(date_str: str, avail: dict) -> dict:
    """
    Check whether a date is available under the given avail settings.

    Returns {'ok': bool, 'reason': str | None}.
    Reasons are human-readable and suitable for display to the owner.
    """
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError, AttributeError):
        return {"ok": False, "reason": "Invalid date format."}

    blocked = avail.get("blocked_dates") or []
    if date_str in blocked:
        return {"ok": False, "reason": f"{date_str} is a blocked date."}

    allowed = avail.get("allowed_weekdays")
    if allowed is None:
        allowed = DEFAULT_ALLOWED_WEEKDAYS

    # If allowed is empty, treat as "all days available" (safety net so nothing hard-blocks)
    if allowed and d.weekday() not in allowed:
        day_name = WEEKDAY_NAMES[d.weekday()]
        return {"ok": False, "reason": f"{day_name}s are not an available day."}

    return {"ok": True, "reason": None}


def next_available_date(avail: dict, from_date: Optional[str] = None) -> Optional[str]:
    """
    Return the nearest available date on or after from_date (default: today).
    Returns None if no days are allowed or no date found within 60 days.
    """
    allowed = avail.get("allowed_weekdays")
    if allowed is None:
        allowed = DEFAULT_ALLOWED_WEEKDAYS
    if not allowed:
        return None

    blocked = set(avail.get("blocked_dates") or [])

    try:
        start = datetime.strptime(from_date, "%Y-%m-%d").date() if from_date else date.today()
    except (ValueError, TypeError):
        start = date.today()

    for i in range(60):
        d = start + timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        if d.weekday() in allowed and ds not in blocked:
            return ds
    return None


def working_days_hint(avail: dict) -> Optional[str]:
    """Return a human-readable string of available days, e.g. 'Mon, Tue, Wed, Thu, Fri'."""
    allowed = avail.get("allowed_weekdays") or []
    if not allowed:
        return None
    return ", ".join(WEEKDAY_NAMES[d] for d in sorted(allowed))
