"""
leadclaw/db.py - Database connection and initialization
"""
import os
import sqlite3
from contextlib import contextmanager

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv("LEADCLAW_DB", os.path.join(_BASE, "data", "leads.db"))


@contextmanager
def get_conn():
    """Context manager: auto-commits on success, rolls back on error, always closes."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Initialize schema and indexes."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS leads (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                phone             TEXT,
                email             TEXT,
                service           TEXT,
                status            TEXT NOT NULL CHECK(status IN ('new','quoted','followup_due','won','lost')),
                lost_reason       TEXT CHECK(lost_reason IN (
                                      'price','timing','went_competitor',
                                      'no_response','not_qualified','service_area','other'
                                  )),
                lost_reason_notes TEXT,
                quote_amount      REAL,
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                last_contact_at   TEXT,
                follow_up_after   TEXT,
                notes             TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_leads_status          ON leads(status);
            CREATE INDEX IF NOT EXISTS idx_leads_follow_up_after ON leads(follow_up_after);
            CREATE INDEX IF NOT EXISTS idx_leads_created_at      ON leads(created_at);
            CREATE INDEX IF NOT EXISTS idx_leads_name            ON leads(name COLLATE NOCASE);

            CREATE TABLE IF NOT EXISTS pilot_candidates (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                name             TEXT NOT NULL,
                business_name    TEXT,
                phone            TEXT,
                email            TEXT,
                service_type     TEXT,
                location         TEXT,
                source           TEXT NOT NULL DEFAULT 'manual_entry',
                score            INTEGER DEFAULT 0,
                status           TEXT NOT NULL DEFAULT 'new'
                                     CHECK(status IN ('new','drafted','approved','sent','replied','converted','passed')),
                notes            TEXT,
                outreach_draft   TEXT,
                reply_text       TEXT,
                reply_summary    TEXT,
                contacted_at     TEXT,
                follow_up_after  TEXT,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                last_updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_pilot_status  ON pilot_candidates(status);
            CREATE INDEX IF NOT EXISTS idx_pilot_score   ON pilot_candidates(score DESC);
            CREATE INDEX IF NOT EXISTS idx_pilot_name    ON pilot_candidates(name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_pilot_phone   ON pilot_candidates(phone);
            CREATE INDEX IF NOT EXISTS idx_pilot_followup ON pilot_candidates(follow_up_after);
        """)
    print(f"Database initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db()
