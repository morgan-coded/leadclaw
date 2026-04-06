"""
seed.py - Seed demo data (10 fake leads)
"""

from datetime import datetime, timedelta

from leadclaw.db import get_conn, init_db


def seed(force: bool = False):
    today = datetime.now()

    def daysago(n):
        return (today - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")

    def daysfrom(n):
        return (today + timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")

    leads = [
        {
            "name": "Mike Tran",
            "phone": "555-101-0001",
            "email": "mike@example.com",
            "service": "fence installation",
            "status": "new",
            "lost_reason": None,
            "lost_reason_notes": None,
            "quote_amount": None,
            "created_at": daysago(1),
            "last_contact_at": daysago(1),
            "follow_up_after": daysfrom(2),
            "notes": "Wants 6ft wood fence, backyard only",
        },
        {
            "name": "Sandra Lopez",
            "phone": "555-101-0002",
            "email": "sandra@example.com",
            "service": "deck repair",
            "status": "new",
            "lost_reason": None,
            "lost_reason_notes": None,
            "quote_amount": None,
            "created_at": daysago(2),
            "last_contact_at": daysago(2),
            "follow_up_after": daysfrom(1),
            "notes": "Water damage on back deck, ~200 sqft",
        },
        {
            "name": "James Okafor",
            "phone": "555-101-0003",
            "email": None,
            "service": "gutter cleaning",
            "status": "new",
            "lost_reason": None,
            "lost_reason_notes": None,
            "quote_amount": None,
            "created_at": daysago(3),
            "last_contact_at": daysago(3),
            "follow_up_after": daysfrom(0),
            "notes": "Two-story home, needs annual cleaning",
        },
        {
            "name": "Priya Sharma",
            "phone": "555-101-0004",
            "email": "priya@example.com",
            "service": "pressure washing",
            "status": "quoted",
            "lost_reason": None,
            "lost_reason_notes": None,
            "quote_amount": 350.0,
            "created_at": daysago(7),
            "last_contact_at": daysago(5),
            "follow_up_after": daysago(1),
            "notes": "Driveway + walkway, asked about discount",
        },
        {
            "name": "Tom Nguyen",
            "phone": "555-101-0005",
            "email": "tom@example.com",
            "service": "lawn care",
            "status": "quoted",
            "lost_reason": None,
            "lost_reason_notes": None,
            "quote_amount": 120.0,
            "created_at": daysago(6),
            "last_contact_at": daysago(4),
            "follow_up_after": daysago(2),
            "notes": "Bi-weekly mowing, ~1/4 acre lot",
        },
        {
            "name": "Rachel Kim",
            "phone": "555-101-0006",
            "email": "rachel@example.com",
            "service": "painting",
            "status": "quoted",
            "lost_reason": None,
            "lost_reason_notes": None,
            "quote_amount": 2400.0,
            "created_at": daysago(10),
            "last_contact_at": daysago(8),
            "follow_up_after": daysago(3),
            "notes": "Exterior paint, 2-story colonial, navy + white trim",
        },
        {
            "name": "Carlos Mendez",
            "phone": "555-101-0007",
            "email": None,
            "service": "tree trimming",
            "status": "followup_due",
            "lost_reason": None,
            "lost_reason_notes": None,
            "quote_amount": 600.0,
            "created_at": daysago(14),
            "last_contact_at": daysago(10),
            "follow_up_after": daysago(5),
            "notes": "Three oaks, one close to power line",
        },
        {
            "name": "Beth Walters",
            "phone": "555-101-0008",
            "email": "beth@example.com",
            "service": "concrete work",
            "status": "followup_due",
            "lost_reason": None,
            "lost_reason_notes": None,
            "quote_amount": 1800.0,
            "created_at": daysago(12),
            "last_contact_at": daysago(9),
            "follow_up_after": daysago(4),
            "notes": "Wants new patio slab, 20x20",
        },
        {
            "name": "Dan Foster",
            "phone": "555-101-0009",
            "email": "dan@example.com",
            "service": "roof inspection",
            "status": "followup_due",
            "lost_reason": None,
            "lost_reason_notes": None,
            "quote_amount": None,
            "created_at": daysago(9),
            "last_contact_at": daysago(7),
            "follow_up_after": daysago(2),
            "notes": "Called after hail storm, hasn't responded to quote request",
        },
        {
            "name": "Lisa Chen",
            "phone": "555-101-0010",
            "email": "lisa@example.com",
            "service": "window cleaning",
            "status": "followup_due",
            "lost_reason": None,
            "lost_reason_notes": None,
            "quote_amount": 275.0,
            "created_at": daysago(11),
            "last_contact_at": daysago(8),
            "follow_up_after": daysago(3),
            "notes": "30-window home, wants interior + exterior",
        },
    ]

    with get_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count > 1 and not force:
            print(
                f"ERROR: {user_count} users exist in this database. "
                "Refusing to wipe leads. Pass --force to override."
            )
            return
        conn.execute("DELETE FROM leads")
        conn.executemany(
            """
            INSERT INTO leads
                (name, phone, email, service, status, lost_reason, lost_reason_notes,
                 quote_amount, created_at, last_contact_at, follow_up_after, notes)
            VALUES
                (:name, :phone, :email, :service, :status, :lost_reason, :lost_reason_notes,
                 :quote_amount, :created_at, :last_contact_at, :follow_up_after, :notes)
            """,
            leads,
        )

    print(f"Seeded {len(leads)} demo leads.")


def main():
    import sys
    force = "--force" in sys.argv
    init_db()
    seed(force=force)


if __name__ == "__main__":
    main()
