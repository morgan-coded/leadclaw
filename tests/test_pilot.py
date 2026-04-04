"""
tests/test_pilot.py - Pilot candidate tracker tests
"""
import os

import pytest

from leadclaw import db
from leadclaw import pilot as p
from tests.conftest import TEST_DB


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def test_score_known_service():
    assert p.score_candidate("lawn care") == 90 + 0  # no phone/email bonus

def test_score_with_phone_and_email():
    score = p.score_candidate("lawn care", has_phone=True, has_email=True)
    assert score == min(90 + 10 + 5, 100)

def test_score_unknown_service():
    score = p.score_candidate("taxidermy")
    assert score == 50  # base

def test_score_auto_found_penalty():
    score_manual = p.score_candidate("roofing", source="manual_entry")
    score_auto = p.score_candidate("roofing", source="auto_found")
    assert score_auto < score_manual

def test_score_capped_at_100():
    score = p.score_candidate("lawn care", has_phone=True, has_email=True)
    assert score <= 100


# ---------------------------------------------------------------------------
# add_candidate + deduplication
# ---------------------------------------------------------------------------

def test_add_candidate_basic():
    cid, dupes = p.add_candidate("Jane Smith", service_type="roofing", phone="555-0001")
    assert cid > 0
    assert dupes == []

def test_add_candidate_sets_score():
    cid, _ = p.add_candidate("Score Test", service_type="lawn care", phone="555-0002")
    c = p.get_candidate_by_id(cid)
    assert c["score"] >= 90

def test_add_candidate_source_stored():
    cid, _ = p.add_candidate("Source Test", source="manual_csv")
    c = p.get_candidate_by_id(cid)
    assert c["source"] == "manual_csv"

def test_add_candidate_invalid_source_falls_back():
    cid, _ = p.add_candidate("Bad Source", source="definitely_fake")
    c = p.get_candidate_by_id(cid)
    assert c["source"] == "manual_entry"

def test_dedupe_by_name():
    p.add_candidate("John Doe", service_type="painting", phone="555-1111")
    _, dupes = p.add_candidate("John Doe", service_type="roofing")
    assert len(dupes) == 1

def test_dedupe_by_phone():
    p.add_candidate("Alice A", phone="555-9999")
    _, dupes = p.add_candidate("Alice B", phone="555-9999")
    assert len(dupes) >= 1

def test_no_false_positive_dedupe():
    p.add_candidate("Unique Name XYZ", phone="555-0001")
    _, dupes = p.add_candidate("Different Name ABC", phone="555-0002")
    assert dupes == []


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def test_status_flow():
    cid, _ = p.add_candidate("Flow Test", service_type="fencing")
    assert p.get_candidate_by_id(cid)["status"] == "new"
    p.set_draft(cid, "Hey, quick question about your fencing work...")
    assert p.get_candidate_by_id(cid)["status"] == "drafted"
    p.set_status(cid, "approved")
    assert p.get_candidate_by_id(cid)["status"] == "approved"
    p.set_status(cid, "sent", contacted=True)
    c = p.get_candidate_by_id(cid)
    assert c["status"] == "sent"
    assert c["contacted_at"] is not None
    p.log_reply(cid, "Yeah I'd be interested, what is it?")
    assert p.get_candidate_by_id(cid)["status"] == "replied"
    p.set_status(cid, "converted")
    assert p.get_candidate_by_id(cid)["status"] == "converted"

def test_set_status_invalid_raises():
    cid, _ = p.add_candidate("Invalid Status", service_type="cleaning")
    with pytest.raises(ValueError):
        p.set_status(cid, "flying_saucer")

def test_set_draft_stores_text():
    cid, _ = p.add_candidate("Draft Store", service_type="painting")
    p.set_draft(cid, "Here is my draft message.")
    c = p.get_candidate_by_id(cid)
    assert c["outreach_draft"] == "Here is my draft message."

def test_log_reply_stores_text():
    cid, _ = p.add_candidate("Reply Log", service_type="gutters")
    p.log_reply(cid, "Sounds interesting, tell me more.")
    c = p.get_candidate_by_id(cid)
    assert c["reply_text"] == "Sounds interesting, tell me more."
    assert c["status"] == "replied"

def test_set_reply_summary():
    cid, _ = p.add_candidate("Summary Set", service_type="roofing")
    p.set_reply_summary(cid, "Interested. Next: schedule a call.")
    c = p.get_candidate_by_id(cid)
    assert c["reply_summary"] == "Interested. Next: schedule a call."


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def test_get_all_candidates_unfiltered():
    p.add_candidate("Alpha", service_type="roofing")
    p.add_candidate("Beta", service_type="painting")
    all_c = p.get_all_candidates()
    assert len(all_c) == 2

def test_get_all_candidates_filtered_by_status():
    cid1, _ = p.add_candidate("Sent One", service_type="roofing")
    p.add_candidate("New One", service_type="painting")
    p.set_status(cid1, "sent", contacted=True)
    sent = p.get_all_candidates(status="sent")
    assert len(sent) == 1
    assert sent[0]["name"] == "Sent One"

def test_get_candidate_by_name_partial():
    p.add_candidate("Michael Jordan", service_type="roofing")
    c, all_m = p.get_candidate_by_name("Michael")
    assert c is not None
    assert c["name"] == "Michael Jordan"

def test_get_candidate_by_name_not_found():
    c, all_m = p.get_candidate_by_name("Nonexistent Person XYZ")
    assert c is None
    assert all_m == []

def test_get_followup_due():
    cid, _ = p.add_candidate("Past Due", service_type="fencing", followup_days=0)
    p.set_status(cid, "sent", contacted=True)
    # Force follow_up_after to yesterday
    from leadclaw.db import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE pilot_candidates SET follow_up_after = datetime('now', '-1 day') WHERE id = ?",
            (cid,),
        )
    due = p.get_followup_due()
    assert any(d["id"] == cid for d in due)

def test_get_pilot_summary():
    p.add_candidate("S1", service_type="roofing")
    cid2, _ = p.add_candidate("S2", service_type="painting")
    p.set_status(cid2, "sent", contacted=True)
    summary = p.get_pilot_summary()
    assert summary["total"] == 2
    assert summary["by_status"]["new"] == 1
    assert summary["by_status"]["sent"] == 1


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------

def test_import_candidates_valid():
    rows = [
        {"name": "CSV A", "service_type": "roofing", "phone": "555-1234"},
        {"name": "CSV B", "service_type": "painting"},
    ]
    result = p.import_candidates_from_rows(rows)
    assert result["imported"] == 2
    assert result["skipped"] == 0

def test_import_candidates_missing_name_skipped():
    rows = [
        {"name": "Valid", "service_type": "roofing"},
        {"name": "", "service_type": "painting"},
    ]
    result = p.import_candidates_from_rows(rows)
    assert result["imported"] == 1
    assert result["skipped"] == 1
    assert result["errors"]

def test_import_candidates_source_is_manual_csv():
    rows = [{"name": "Source Check", "service_type": "gutters"}]
    p.import_candidates_from_rows(rows)
    c, _ = p.get_candidate_by_name("Source Check")
    assert c["source"] == "manual_csv"


# ---------------------------------------------------------------------------
# Update / delete
# ---------------------------------------------------------------------------

def test_update_candidate_notes():
    cid, _ = p.add_candidate("Update Me", service_type="fencing")
    p.update_candidate(cid, notes="Updated note here")
    c = p.get_candidate_by_id(cid)
    assert c["notes"] == "Updated note here"

def test_delete_candidate():
    cid, _ = p.add_candidate("Delete Me", service_type="roofing")
    p.delete_candidate(cid)
    assert p.get_candidate_by_id(cid) is None
