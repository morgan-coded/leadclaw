"""
tests/test_queries.py - Core query and DB logic tests
"""

import os
import sqlite3

import pytest

from leadclaw import db, queries
from tests.conftest import TEST_DB


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def test_db_init_creates_file():
    assert os.path.exists(TEST_DB)


def test_db_init_creates_indexes():
    conn = sqlite3.connect(TEST_DB)
    indexes = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    names = [row[0] for row in indexes]
    conn.close()
    assert "idx_leads_status" in names
    assert "idx_leads_follow_up_after" in names


def test_add_and_retrieve_lead():
    lead_id, dupes = queries.add_lead("John Smith", "lawn care", phone="555-0001")
    assert lead_id > 0
    assert dupes == []
    lead, matches = queries.get_lead_by_name("John")
    assert lead is not None
    assert lead["name"] == "John Smith"
    assert lead["status"] == "new"


def test_duplicate_detection():
    queries.add_lead("Jane Doe", "painting")
    _, dupes = queries.add_lead("Jane Doe", "fence")
    assert len(dupes) == 1
    assert dupes[0]["name"] == "Jane Doe"


def test_update_quote_sets_timestamps():
    lead_id, _ = queries.add_lead("Bob", "roofing")
    queries.update_quote(lead_id, 1500.0)
    lead, _ = queries.get_lead_by_name("Bob")
    assert lead["status"] == "quoted"
    assert lead["quote_amount"] == 1500.0
    assert lead["last_contact_at"] is not None
    assert lead["follow_up_after"] is not None


def test_mark_won_clears_followup():
    lead_id, _ = queries.add_lead("Alice", "pressure washing")
    queries.update_quote(lead_id, 400.0)
    queries.mark_won(lead_id)
    lead, _ = queries.get_lead_by_name("Alice")
    assert lead["status"] == "won"
    assert lead["follow_up_after"] is None
    assert lead["last_contact_at"] is not None


def test_mark_lost_with_reason_and_notes():
    lead_id, _ = queries.add_lead("Carlos", "tree trimming")
    queries.mark_lost(lead_id, "price", notes="Said too expensive")
    lead, _ = queries.get_lead_by_name("Carlos")
    assert lead["status"] == "lost"
    assert lead["lost_reason"] == "price"
    assert lead["lost_reason_notes"] == "Said too expensive"
    assert lead["follow_up_after"] is None
    assert lead["last_contact_at"] is not None


def test_today_excludes_won_lost():
    id_won, _ = queries.add_lead("Won Person", "service")
    id_lost, _ = queries.add_lead("Lost Person", "service")
    queries.mark_won(id_won)
    queries.mark_lost(id_lost, "timing")
    today_leads = queries.get_today_leads()
    names = [lead["name"] for lead in today_leads]
    assert "Won Person" not in names
    assert "Lost Person" not in names


def test_pipeline_summary_open_closed_split():
    id1, _ = queries.add_lead("Open Lead", "painting")
    id2, _ = queries.add_lead("Won Lead", "fencing")
    queries.update_quote(id1, 500.0)
    queries.update_quote(id2, 1000.0)
    queries.mark_won(id2)
    _, totals = queries.get_pipeline_summary()
    assert totals["open_value"] == 500.0
    assert totals["won_value"] == 1000.0
    assert totals["lost_value"] == 0.0


def test_like_escaping():
    queries.add_lead("Test%Lead", "service")
    queries.add_lead("Other Lead", "service")
    _, matches = queries.get_lead_by_name("Test%Lead")
    assert all("Test%Lead" in m["name"] for m in matches)
    assert len(matches) == 1


def test_stale_promotion():
    lead_id, _ = queries.add_lead("Stale Guy", "gutters")
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE leads SET follow_up_after = datetime('now', '-5 days') WHERE id = ?",
            (lead_id,),
        )
    count = queries.mark_stale_leads_followup_due()
    assert count == 1
    lead, _ = queries.get_lead_by_name("Stale Guy")
    assert lead["status"] == "followup_due"


def test_delete_lead():
    lead_id, _ = queries.add_lead("Delete Me", "concrete")
    queries.delete_lead(lead_id)
    lead, _ = queries.get_lead_by_name("Delete Me")
    assert lead is None


def test_get_all_leads_pagination():
    for i in range(5):
        queries.add_lead(f"Lead {i}", "service")
    page1 = queries.get_all_leads(limit=3, offset=0)
    page2 = queries.get_all_leads(limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) <= 3
    ids1 = {r["id"] for r in page1}
    ids2 = {r["id"] for r in page2}
    assert ids1.isdisjoint(ids2)


def test_update_lead_fields():
    lead_id, _ = queries.add_lead("Edit Me", "painting")
    queries.update_lead(lead_id, phone="555-9999", notes="Updated note")
    lead, _ = queries.get_lead_by_name("Edit Me")
    assert lead["phone"] == "555-9999"
    assert lead["notes"] == "Updated note"


def test_update_lead_ignores_unknown_fields():
    lead_id, _ = queries.add_lead("Safe Lead", "roofing")
    queries.update_lead(lead_id, status="won", phone="555-1111")
    lead, _ = queries.get_lead_by_name("Safe Lead")
    assert lead["status"] == "new"
    assert lead["phone"] == "555-1111"
