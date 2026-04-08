"""
leadclaw/db.py - Database connection and initialization
"""

import os
import sqlite3
from contextlib import contextmanager


def _default_db_path() -> str:
    """Return a sensible default DB path in the user's data directory."""
    env = os.environ.get("LEADCLAW_DB")
    if env:
        return env
    # XDG_DATA_HOME or ~/.local/share on Linux/macOS
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(data_home, "leadclaw", "leads.db")


DB_PATH = _default_db_path()


@contextmanager
def get_conn():
    """Context manager: auto-commits on success, rolls back on error, always closes."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    # Enforce FK constraints for every connection
    conn.execute("PRAGMA foreign_keys = ON")
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
    """Initialize schema, indexes, and run any column-level migrations."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with get_conn() as conn:
        # Enable FK enforcement during init as well
        conn.execute("PRAGMA foreign_keys = ON")

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                email_verified INTEGER NOT NULL DEFAULT 0,
                verify_token   TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email COLLATE NOCASE);

            CREATE TABLE IF NOT EXISTS leads (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                phone             TEXT,
                email             TEXT,
                service           TEXT,
                status            TEXT NOT NULL CHECK(status IN ('new','quoted','followup_due','booked','completed','paid','lost','won')),
                lost_reason       TEXT CHECK(lost_reason IN (
                                      'price','timing','went_competitor',
                                      'no_response','not_qualified','service_area','other'
                                  )),
                lost_reason_notes TEXT,
                quote_amount      REAL,
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                last_contact_at   TEXT,
                follow_up_after   TEXT,
                notes             TEXT,
                review_reminder_at TEXT
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

        # --- Column migrations: add new lifecycle columns to leads ---
        # Note: SQLite cannot ALTER a CHECK constraint on existing tables.
        # App-level validation handles new statuses for existing DBs.
        # New installs get the full CHECK from the CREATE TABLE above.
        # review_reminder_at is already in the CREATE TABLE above; omitted here.
        new_columns = [
            "scheduled_date TEXT",
            "booked_at TEXT",
            "completed_at TEXT",
            "invoice_amount REAL",
            "invoice_sent_at TEXT",
            "paid_at TEXT",
            "next_service_due_at TEXT",
            "invoice_reminder_at TEXT",
            "service_reminder_at TEXT",
            "review_request_sent_at TEXT",
            "reactivation_dismissed_at TEXT",
            "job_reminder_dismissed_at TEXT",
            # Feature: public service request form
            "lead_source TEXT",
            "requested_date TEXT",
            "requested_time_window TEXT",
            "service_address TEXT",
            # Feature: request-to-book conversion
            "scheduled_time_window TEXT",
            # Feature: new request notifications
            "request_seen_at TEXT",
        ]
        for col_def in new_columns:
            try:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {col_def}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

        # --- Column migrations: add user_id to leads ---
        # SQLite doesn't support IF NOT EXISTS on ALTER TABLE ADD COLUMN
        # Also, SQLite doesn't allow REFERENCES in ALTER TABLE with NOT NULL DEFAULT
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

        try:
            conn.execute("CREATE INDEX idx_leads_user_id ON leads(user_id)")
        except sqlite3.OperationalError as e:
            if "already exists" not in str(e).lower():
                raise

        # --- Column migrations: add user_id to pilot_candidates ---
        try:
            conn.execute(
                "ALTER TABLE pilot_candidates ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

        try:
            conn.execute("CREATE INDEX idx_pilot_user_id ON pilot_candidates(user_id)")
        except sqlite3.OperationalError as e:
            if "already exists" not in str(e).lower():
                raise

        # --- Availability settings table ---
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS availability (
                user_id       INTEGER PRIMARY KEY,
                allowed_weekdays TEXT NOT NULL DEFAULT '[0,1,2,3,4]',
                blocked_dates    TEXT NOT NULL DEFAULT '[]',
                updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # --- Event log table for pilot usage tracking ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                user_id INTEGER,
                lead_id INTEGER,
                meta TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_type ON event_log(event_type)")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_log_created ON event_log(created_at)"
            )
        except sqlite3.OperationalError:
            pass

        # --- Column migration: add notify_new_requests to users ---
        try:
            conn.execute(
                "ALTER TABLE users ADD COLUMN notify_new_requests INTEGER NOT NULL DEFAULT 1"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

        # --- Column migrations: Stripe billing fields on users ---
        _stripe_user_cols = [
            "stripe_customer_id TEXT",
            "subscription_status TEXT NOT NULL DEFAULT 'trialing'",
            "trial_ends_at TEXT",
            "subscription_ends_at TEXT",
        ]
        for col_def in _stripe_user_cols:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_def}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

        # --- Column migrations: request slug + business name on users ---
        _slug_user_cols = [
            "request_slug TEXT",
            "business_name TEXT",
        ]
        for col_def in _slug_user_cols:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_def}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        # Unique index on request_slug (CREATE INDEX IF NOT EXISTS is safe to re-run)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_request_slug ON users(request_slug)"
        )

        # Ensure the default CLI user (id=1) exists so FK DEFAULT 1 is always valid
        conn.execute(
            """
            INSERT OR IGNORE INTO users (id, email, password_hash, email_verified)
            VALUES (1, 'cli@localhost', 'cli-no-password', 1)
            """
        )

    print(f"Database initialized at {DB_PATH}")


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------


def get_user_by_email(email: str):
    """Return the users row for the given email, or None."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone()


def get_user_by_id(user_id: int):
    """Return the users row for the given id, or None."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()


def get_user_by_verify_token(token: str):
    """Return user row matching a verification token, or None."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE verify_token = ?",
            (token,),
        ).fetchone()


def create_user(email: str, password_hash: str, verify_token: str) -> int:
    """Insert a new user and return the new id."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, verify_token) VALUES (?, ?, ?)",
            (email, password_hash, verify_token),
        )
        return cur.lastrowid


def verify_user_email(user_id: int):
    """Mark a user's email as verified and clear the token."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET email_verified = 1, verify_token = NULL WHERE id = ?",
            (user_id,),
        )


def update_user_stripe(user_id: int, **fields):
    """Update Stripe-related fields on a user row.

    Valid keys: stripe_customer_id, subscription_status, trial_ends_at, subscription_ends_at.
    """
    _allowed = {"stripe_customer_id", "subscription_status", "trial_ends_at", "subscription_ends_at"}
    updates = {k: v for k, v in fields.items() if k in _allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [user_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", vals)


def get_user_by_slug(slug: str):
    """Return the users row for a given request_slug, or None."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE request_slug = ?",
            (slug,),
        ).fetchone()


def set_user_slug(user_id: int, slug: str):
    """Set the request_slug on a user row."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET request_slug = ? WHERE id = ?",
            (slug, user_id),
        )


def update_verify_token(user_id: int, token: str):
    """Replace the verification token for a user (used for resend flow)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET verify_token = ? WHERE id = ?",
            (token, user_id),
        )


if __name__ == "__main__":
    init_db()
