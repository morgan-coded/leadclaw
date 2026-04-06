"""
tests/test_commands.py - CLI command logic tests
"""

import os
from unittest.mock import patch

import pytest

from leadclaw import db, queries
from leadclaw.commands import (
    build_parser,
    cmd_digest,
    cmd_export,
    cmd_import,
    cmd_list,
    cmd_quote,
    fmt_lead,
    print_pipeline_summary,
    resolve_lead,
)
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
# Parser tests
# ---------------------------------------------------------------------------


def test_parser_today():
    parser = build_parser()
    args = parser.parse_args(["today"])
    assert args.command == "today"


def test_parser_quote():
    parser = build_parser()
    args = parser.parse_args(["quote", "Mike", "850"])
    assert args.command == "quote"
    assert args.name == "Mike"
    assert args.amount == 850.0


def test_parser_lost_valid_reason():
    parser = build_parser()
    args = parser.parse_args(["lost", "Mike", "price"])
    assert args.reason == "price"


def test_parser_lost_invalid_reason():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["lost", "Mike", "badreason"])


def test_parser_list_flags():
    parser = build_parser()
    args = parser.parse_args(["list", "--all", "--limit", "10", "--offset", "5"])
    assert args.all is True
    assert args.limit == 10
    assert args.offset == 5


def test_parser_plain_flag():
    parser = build_parser()
    args = parser.parse_args(["--plain", "digest"])
    assert args.plain is True


# ---------------------------------------------------------------------------
# resolve_lead tests
# ---------------------------------------------------------------------------


def test_resolve_lead_by_name():
    queries.add_lead("John Smith", "painting")
    lead = resolve_lead("John")
    assert lead is not None
    assert lead["name"] == "John Smith"


def test_resolve_lead_not_found(capsys):
    lead = resolve_lead("Nobody")
    assert lead is None
    out = capsys.readouterr().out
    assert "No lead found" in out


def test_resolve_lead_multiple_warns(capsys):
    queries.add_lead("John A", "painting")
    queries.add_lead("John B", "roofing")
    lead = resolve_lead("John")
    assert lead is not None
    out = capsys.readouterr().out
    assert "Multiple matches" in out


def test_resolve_lead_by_id():
    lead_id, _ = queries.add_lead("By ID", "service")
    lead = resolve_lead("", lead_id=lead_id)
    assert lead is not None
    assert lead["id"] == lead_id


# ---------------------------------------------------------------------------
# cmd_quote tests
# ---------------------------------------------------------------------------


def test_cmd_quote_negative(capsys):
    queries.add_lead("Alice", "roofing")
    parser = build_parser()
    args = parser.parse_args(["quote", "Alice", "-50"])
    cmd_quote(args)
    out = capsys.readouterr().out
    assert "greater than zero" in out


def test_cmd_quote_valid(capsys):
    queries.add_lead("Bob", "fencing")
    parser = build_parser()
    args = parser.parse_args(["quote", "Bob", "1200"])
    cmd_quote(args)
    lead, _ = queries.get_lead_by_name("Bob")
    assert lead["quote_amount"] == 1200.0
    assert lead["status"] == "quoted"


# ---------------------------------------------------------------------------
# cmd_list tests
# ---------------------------------------------------------------------------


def test_cmd_list_active_only(capsys):
    queries.add_lead("Active", "painting")
    id_won, _ = queries.add_lead("Won", "roofing")
    queries.mark_won(id_won)
    parser = build_parser()
    args = parser.parse_args(["list"])
    cmd_list(args)
    out = capsys.readouterr().out
    assert "Active" in out
    assert "Won" not in out


def test_cmd_list_all_includes_won(capsys):
    queries.add_lead("Active", "painting")
    id_won, _ = queries.add_lead("Won", "roofing")
    queries.mark_won(id_won)
    parser = build_parser()
    args = parser.parse_args(["list", "--all"])
    cmd_list(args)
    out = capsys.readouterr().out
    assert "Active" in out
    assert "Won" in out


# ---------------------------------------------------------------------------
# cmd_digest tests
# ---------------------------------------------------------------------------


def test_cmd_digest_output(capsys):
    queries.add_lead("Test Lead", "service")
    parser = build_parser()
    args = parser.parse_args(["digest"])
    cmd_digest(args)
    out = capsys.readouterr().out
    assert "Pipeline Digest" in out


# ---------------------------------------------------------------------------
# cmd_export tests
# ---------------------------------------------------------------------------


def test_cmd_import_valid_csv(tmp_path, capsys):
    csv_content = "name,service,phone,notes\nImport Lead,painting,555-9999,test note\n"
    csv_file = tmp_path / "import.csv"
    csv_file.write_text(csv_content)
    parser = build_parser()
    args = parser.parse_args(["import", "--yes", str(csv_file)])
    cmd_import(args)
    out = capsys.readouterr().out
    assert "Imported 1" in out
    lead, _ = queries.get_lead_by_name("Import Lead")
    assert lead is not None
    assert lead["service"] == "painting"


def test_cmd_import_missing_required_column(tmp_path, capsys):
    csv_file = tmp_path / "bad.csv"
    csv_file.write_text("phone,notes\n555-0000,no name or service\n")
    parser = build_parser()
    args = parser.parse_args(["import", "--yes", str(csv_file)])
    cmd_import(args)
    out = capsys.readouterr().out
    assert "missing required column" in out


def test_cmd_import_file_not_found(tmp_path, capsys):
    parser = build_parser()
    args = parser.parse_args(["import", "--yes", str(tmp_path / "nonexistent.csv")])
    cmd_import(args)
    out = capsys.readouterr().out
    assert "File not found" in out


def test_cmd_import_skips_rows_missing_name(tmp_path, capsys):
    csv_content = "name,service\nGood Lead,roofing\n,painting\n"
    csv_file = tmp_path / "partial.csv"
    csv_file.write_text(csv_content)
    parser = build_parser()
    args = parser.parse_args(["import", "--yes", str(csv_file)])
    cmd_import(args)
    out = capsys.readouterr().out
    assert "Imported 1" in out
    assert "skipped 1" in out


def test_cmd_export_creates_file(tmp_path):
    queries.add_lead("Export Me", "painting")
    out_path = str(tmp_path / "test_export.csv")
    parser = build_parser()
    args = parser.parse_args(["export", "--output", out_path])
    cmd_export(args)
    assert os.path.exists(out_path)
    with open(out_path) as f:
        content = f.read()
    assert "Export Me" in content
    assert "name" in content  # header row


# ---------------------------------------------------------------------------
# fmt_lead / print_pipeline_summary tests
# ---------------------------------------------------------------------------


def test_fmt_lead_contains_name():
    lead_id, _ = queries.add_lead("Display Test", "gutters")
    lead = queries.get_lead_by_id(lead_id)
    output = fmt_lead(lead)
    assert "Display Test" in output
    assert "gutters" in output


def test_print_pipeline_summary_output(capsys):
    queries.add_lead("P1", "painting")
    id2, _ = queries.add_lead("P2", "roofing")
    queries.update_quote(id2, 500.0)
    summary, totals = queries.get_pipeline_summary()
    print_pipeline_summary(summary, totals)
    out = capsys.readouterr().out
    assert "Open pipeline" in out
    assert "Paid" in out


# ---------------------------------------------------------------------------
# Plain-mode output tests
# ---------------------------------------------------------------------------


def test_fmt_lead_plain_no_emoji(capsys):
    """fmt_lead() in plain mode must emit bracket labels, not emoji."""
    import leadclaw.commands as cmd_mod

    lead_id, _ = queries.add_lead("Plain Test", "siding")
    lead = queries.get_lead_by_id(lead_id)
    original = cmd_mod._PLAIN
    try:
        cmd_mod._PLAIN = True
        output = cmd_mod.fmt_lead(lead)
    finally:
        cmd_mod._PLAIN = original
    assert "[new]" in output
    assert "\U0001f195" not in output  # 🆕 must be absent


def test_print_pipeline_summary_plain_no_emoji(capsys):
    """print_pipeline_summary() in plain mode must emit bracket labels, not emoji."""
    import leadclaw.commands as cmd_mod

    queries.add_lead("P Plain", "fencing")
    summary, totals = queries.get_pipeline_summary()
    original = cmd_mod._PLAIN
    try:
        cmd_mod._PLAIN = True
        cmd_mod.print_pipeline_summary(summary, totals)
    finally:
        cmd_mod._PLAIN = original
    out = capsys.readouterr().out
    assert "[new]" in out
    assert "\U0001f195" not in out  # 🆕 must be absent


# ---------------------------------------------------------------------------
# AI command mocking
# ---------------------------------------------------------------------------


def test_draft_followup_mocked(capsys):
    queries.add_lead("AI Lead", "pressure washing")
    with patch("leadclaw.commands.draft_followup", return_value="Hey, just checking in!"):
        with patch("leadclaw.commands.check_api_key", return_value=True):
            parser = build_parser()
            args = parser.parse_args(["draft-followup", "AI Lead"])
            from leadclaw.commands import cmd_draft

            cmd_draft(args)
    out = capsys.readouterr().out
    assert "Hey, just checking in!" in out


def test_summarize_mocked(capsys):
    queries.add_lead("Sum Lead", "lawn care")
    with patch("leadclaw.commands.summarize_lead", return_value="This lead needs follow-up."):
        with patch("leadclaw.commands.check_api_key", return_value=True):
            parser = build_parser()
            args = parser.parse_args(["summarize", "Sum Lead"])
            from leadclaw.commands import cmd_summarize

            cmd_summarize(args)
    out = capsys.readouterr().out
    assert "This lead needs follow-up." in out
