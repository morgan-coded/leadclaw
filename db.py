"""
db.py - Database connection and initialization
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "leads.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            phone       TEXT,
            email       TEXT,
            service     TEXT,
            status      TEXT NOT NULL CHECK(status IN ('new', 'quoted', 'followup_due', 'won', 'lost')),
            lost_reason TEXT CHECK(lost_reason IN (
                            'price', 'timing', 'went_competitor',
                            'no_response', 'not_qualified', 'service_area', 'other'
                        )),
            lost_reason_notes TEXT,
            quote_amount      REAL,
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            last_contact_at   TEXT,
            follow_up_after   TEXT,
            notes             TEXT
        );
    """)

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db()
