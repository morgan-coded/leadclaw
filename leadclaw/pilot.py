"""
pilot.py - Pilot candidate tracker: queries, scoring, deduplication
"""
from datetime import datetime
from typing import Optional

from leadclaw.db import get_conn

SOURCES = ("manual_entry", "manual_csv", "auto_found")
STATUSES = ("new", "drafted", "approved", "sent", "replied", "converted", "passed")

# Score weights (0-100 scale)
_SERVICE_TYPE_SCORES = {
    "lawn care": 90, "landscaping": 90,
    "pressure washing": 85, "window cleaning": 85,
    "gutter cleaning": 85, "gutter": 85,
    "painting": 80, "roofing": 75,
    "hvac": 75, "plumbing": 75,
    "fencing": 70, "concrete": 70,
    "tree trimming": 70, "tree service": 70,
    "cleaning": 65, "handyman": 65,
    "electrical": 65, "flooring": 60,
}

DEFAULT_FOLLOWUP_DAYS = 4


def _row_to_dict(row) -> dict:
    return dict(row)


def score_candidate(service_type: Optional[str] = None,
                    has_phone: bool = False,
                    has_email: bool = False,
                    source: str = "manual_entry") -> int:
    """
    Score 0-100 based on fit signals.
    Higher = better pilot candidate.
    """
    score = 50  # base
    if service_type:
        key = service_type.lower().strip()
        score = _SERVICE_TYPE_SCORES.get(key, 50)
    if has_phone:
        score += 10
    if has_email:
        score += 5
    if source == "auto_found":
        score -= 5  # slight penalty: unverified
    return min(score, 100)


def find_duplicates(name: str, phone: Optional[str] = None) -> list:
    """
    Check for existing candidates matching by name (fuzzy) or exact phone.
    Returns list of matching rows.
    """
    results = []
    with get_conn() as conn:
        if phone:
            rows = conn.execute(
                "SELECT * FROM pilot_candidates WHERE phone = ?", (phone,)
            ).fetchall()
            results.extend(rows)
        if name:
            safe = name.replace("%", r"\%").replace("_", r"\_")
            rows = conn.execute(
                "SELECT * FROM pilot_candidates WHERE name LIKE ? ESCAPE '\\' ORDER BY created_at DESC",
                (f"%{safe}%",),
            ).fetchall()
            for r in rows:
                if r["id"] not in {d["id"] for d in results}:
                    results.append(r)
    return results


# ---------------------------------------------------------------------------
# Read queries
# ---------------------------------------------------------------------------


def get_all_candidates(status: Optional[str] = None, limit: int = 200, offset: int = 0,
                       user_id: Optional[int] = None) -> list:
    with get_conn() as conn:
        if status and user_id is not None:
            return conn.execute(
                "SELECT * FROM pilot_candidates WHERE status = ? AND user_id = ? "
                "ORDER BY score DESC, created_at DESC LIMIT ? OFFSET ?",
                (status, user_id, limit, offset),
            ).fetchall()
        elif status:
            return conn.execute(
                "SELECT * FROM pilot_candidates WHERE status = ? "
                "ORDER BY score DESC, created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        elif user_id is not None:
            return conn.execute(
                "SELECT * FROM pilot_candidates WHERE user_id = ? "
                "ORDER BY score DESC, created_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
        else:
            return conn.execute(
                "SELECT * FROM pilot_candidates ORDER BY score DESC, created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()


def get_candidate_by_id(cid: int, user_id: Optional[int] = None):
    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                "SELECT * FROM pilot_candidates WHERE id = ? AND user_id = ?",
                (cid, user_id),
            ).fetchone()
        return conn.execute("SELECT * FROM pilot_candidates WHERE id = ?", (cid,)).fetchone()


def get_candidate_by_name(name: str):
    safe = name.replace("%", r"\%").replace("_", r"\_")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pilot_candidates WHERE name LIKE ? ESCAPE '\\' ORDER BY score DESC, created_at DESC",
            (f"%{safe}%",),
        ).fetchall()
    if not rows:
        return None, []
    return rows[0], rows


def get_followup_due(user_id: Optional[int] = None) -> list:
    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                """
                SELECT * FROM pilot_candidates
                WHERE status IN ('sent','drafted','approved')
                  AND follow_up_after < datetime('now')
                  AND user_id = ?
                ORDER BY follow_up_after ASC
                """,
                (user_id,),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM pilot_candidates
            WHERE status IN ('sent','drafted','approved')
              AND follow_up_after < datetime('now')
            ORDER BY follow_up_after ASC
            """,
        ).fetchall()


def get_pilot_summary(user_id: Optional[int] = None) -> dict:
    with get_conn() as conn:
        if user_id is not None:
            by_status = conn.execute(
                "SELECT status, COUNT(*) as count FROM pilot_candidates WHERE user_id = ? GROUP BY status",
                (user_id,),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM pilot_candidates WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
        else:
            by_status = conn.execute(
                "SELECT status, COUNT(*) as count FROM pilot_candidates GROUP BY status"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM pilot_candidates").fetchone()[0]
    return {"total": total, "by_status": {r["status"]: r["count"] for r in by_status}}


# ---------------------------------------------------------------------------
# Write queries
# ---------------------------------------------------------------------------


def add_candidate(
    name: str,
    service_type: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    business_name: Optional[str] = None,
    location: Optional[str] = None,
    notes: Optional[str] = None,
    source: str = "manual_entry",
    followup_days: int = DEFAULT_FOLLOWUP_DAYS,
    user_id: int = 1,
) -> tuple:
    """
    Insert a pilot candidate. Returns (id, duplicates).
    Score is computed automatically.
    """
    dupes = find_duplicates(name, phone)
    s = score_candidate(service_type, has_phone=bool(phone), has_email=bool(email), source=source)
    if source not in SOURCES:
        source = "manual_entry"
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO pilot_candidates
                (name, business_name, phone, email, service_type, location,
                 source, score, notes, status, user_id,
                 follow_up_after, created_at, last_updated_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?,
                 datetime('now', ? || ' days'), datetime('now'), datetime('now'))
            """,
            (name, business_name, phone, email, service_type, location,
             source, s, notes, user_id, f"+{followup_days}"),
        )
        cid = cur.lastrowid
    return cid, dupes


def update_candidate(cid: int, **fields):
    allowed = {"name", "business_name", "phone", "email", "service_type",
               "location", "notes", "follow_up_after", "outreach_draft",
               "reply_text", "reply_summary", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    updates["last_updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE pilot_candidates SET {set_clause} WHERE id = ?",  # noqa: S608
            (*updates.values(), cid),
        )


def set_status(cid: int, status: str, contacted: bool = False):
    if status not in STATUSES:
        raise ValueError(f"Invalid status: {status}")
    with get_conn() as conn:
        if contacted:
            conn.execute(
                "UPDATE pilot_candidates SET status=?, contacted_at=datetime('now'), last_updated_at=datetime('now') WHERE id=?",
                (status, cid),
            )
        else:
            conn.execute(
                "UPDATE pilot_candidates SET status=?, last_updated_at=datetime('now') WHERE id=?",
                (status, cid),
            )


def set_draft(cid: int, draft: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pilot_candidates SET outreach_draft=?, status='drafted', last_updated_at=datetime('now') WHERE id=?",
            (draft, cid),
        )


def log_reply(cid: int, reply_text: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pilot_candidates SET reply_text=?, status='replied', last_updated_at=datetime('now') WHERE id=?",
            (reply_text, cid),
        )


def set_reply_summary(cid: int, summary: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pilot_candidates SET reply_summary=?, last_updated_at=datetime('now') WHERE id=?",
            (summary, cid),
        )


def delete_candidate(cid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM pilot_candidates WHERE id = ?", (cid,))


def import_candidates_from_rows(rows: list) -> dict:
    """
    Bulk-insert from pre-validated CSV rows.
    Returns {imported, skipped, errors}.
    """
    imported = skipped = 0
    errors = []
    for i, row in enumerate(rows):
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {i + 1}: missing name — skipped")
            skipped += 1
            continue
        try:
            add_candidate(
                name=name,
                service_type=(row.get("service_type") or row.get("service") or "").strip() or None,
                phone=(row.get("phone") or "").strip() or None,
                email=(row.get("email") or "").strip() or None,
                business_name=(row.get("business_name") or row.get("business") or "").strip() or None,
                location=(row.get("location") or row.get("city") or "").strip() or None,
                notes=(row.get("notes") or "").strip() or None,
                source="manual_csv",
            )
            imported += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"Row {i + 1} ({name}): {e}")
            skipped += 1
    return {"imported": imported, "skipped": skipped, "errors": errors}
