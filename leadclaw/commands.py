"""
commands.py - CLI entry point using argparse
"""
import argparse
import os
import sys
from datetime import datetime
from typing import Optional

from leadclaw.config import (
    DEFAULT_FOLLOWUP_DAYS,
    LOST_REASONS,
    MAX_FIELD_LENGTH,
    MAX_LIST_ROWS,
    MAX_NAME_LENGTH,
    STATUS_LABELS,
)

# Runtime flag — set by build_parser() based on --plain
_PLAIN = False

STATUS_LABELS_PLAIN = {
    "new": "[new]",
    "quoted": "[quoted]",
    "followup_due": "[followup_due]",
    "won": "[won]",
    "lost": "[lost]",
}


def _status_label(status: str) -> str:
    if _PLAIN:
        return STATUS_LABELS_PLAIN.get(status, status)
    return STATUS_LABELS.get(status, status)


from leadclaw.drafting import check_api_key, draft_followup, summarize_lead, summarize_pipeline
from leadclaw.queries import (
    add_lead,
    delete_lead,
    get_all_active_leads,
    get_all_leads,
    get_closed_summary,
    get_lead_by_id,
    get_lead_by_name,
    get_pipeline_summary,
    get_stale_leads,
    get_today_leads,
    import_leads_from_rows,
    mark_lost,
    mark_stale_leads_followup_due,
    mark_won,
    update_lead,
    update_quote,
)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def fmt_lead(lead) -> str:
    prefix = _status_label(lead["status"]).split()[0] if not _PLAIN else _status_label(lead["status"])
    lines = [f"{prefix} [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'}"]
    lines.append(f"   Status: {lead['status']}")
    if lead["quote_amount"]:
        lines.append(f"   Quote:  ${lead['quote_amount']:,.0f}")
    if lead["phone"]:
        lines.append(f"   Phone:  {lead['phone']}")
    if lead["email"]:
        lines.append(f"   Email:  {lead['email']}")
    if lead["follow_up_after"]:
        lines.append(f"   Follow up: {str(lead['follow_up_after'])[:10]}")
    if lead["lost_reason"]:
        lines.append(f"   Lost reason: {lead['lost_reason']}")
        if lead["lost_reason_notes"]:
            lines.append(f"   Notes: {lead['lost_reason_notes']}")
    elif lead["notes"]:
        lines.append(f"   Notes:  {lead['notes']}")
    return "\n".join(lines)


def print_pipeline_summary(summary, totals):
    for row in summary:
        label = _status_label(row["status"])
        val = f"  (${row['total_quoted']:,.0f})" if row["total_quoted"] else ""
        print(f"  {label}: {row['count']}{val}")
    print(f"\n  Open pipeline:  ${totals['open_value']:,.0f}")
    print(f"  Won (closed):   ${totals['won_value']:,.0f}")
    print(f"  Lost (closed):  ${totals['lost_value']:,.0f}")


def resolve_lead(name: str, lead_id: Optional[int] = None):
    """Resolve name or id to a single lead, with disambiguation warning."""
    if lead_id:
        lead = get_lead_by_id(lead_id)
        if not lead:
            print(f"No lead found with id {lead_id}.")
        return lead
    if not name:
        print("Provide a name or --id.")
        return None
    lead, all_matches = get_lead_by_name(name)
    if not lead:
        print(f"No lead found matching '{name}'.")
        return None
    if len(all_matches) > 1:
        print(f"Multiple matches for '{name}':")
        for match in all_matches:
            print(f"   [{match['id']}] {match['name']} — {match['service'] or 'N/A'} ({match['status']})")
        print(f"   Using most recent: [{lead['id']}] {lead['name']}")
        print(f"   Tip: use --id <id> to be explicit.\n")
    return lead


def _validate_email(val: str) -> bool:
    return "@" in val and "." in val.split("@")[-1]


def _validate_date(val: str) -> bool:
    try:
        datetime.strptime(val, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _prompt_str(label: str, current: str = "", required: bool = False, max_len: int = MAX_FIELD_LENGTH) -> Optional[str]:
    """Prompt for a string field with optional validation."""
    while True:
        val = input(f"  {label}: ").strip()
        if not val:
            if required:
                print("  This field is required.")
                continue
            return None
        if len(val) > max_len:
            print(f"  Max {max_len} characters.")
            continue
        return val


def _prompt_int(label: str, default: int, min_val: int = 0) -> int:
    """Prompt for a positive integer with a default."""
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        if not raw:
            return default
        if not raw.lstrip("-").isdigit() or int(raw) < min_val:
            print(f"  Enter a whole number >= {min_val}.")
            continue
        return int(raw)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_today(args):
    leads = get_today_leads()
    if not leads:
        print("No leads for today.")
        return
    print(f"=== Today's Leads ({len(leads)}) ===\n")
    for lead in leads:
        print(fmt_lead(lead))
        print()


def cmd_stale(args):
    leads = get_stale_leads()
    if not leads:
        print("No stale leads. You're on top of it.")
        return
    print(f"=== Stale Leads ({len(leads)}) ===\n")
    for lead in leads:
        print(fmt_lead(lead))
        print()


def cmd_list(args):
    limit = getattr(args, "limit", 50)
    offset = getattr(args, "offset", 0)
    if args.all:
        leads = get_all_leads(limit=limit, offset=offset)
        label = "All Leads"
    else:
        leads = get_all_active_leads()
        label = "Active Leads"
    if not leads:
        print("No leads found.")
        return
    print(f"=== {label} ({len(leads)}) ===\n")
    for lead in leads:
        print(fmt_lead(lead))
        print()
    if args.all and len(leads) == limit:
        print(f"  Showing {limit} rows. Use --offset {offset + limit} for more.")


def cmd_lead(args):
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if lead:
        print(fmt_lead(lead))


def cmd_add(args):
    print("=== Add New Lead ===")
    name = _prompt_str("Name", required=True, max_len=MAX_NAME_LENGTH)
    service = _prompt_str("Service requested", required=True)
    phone = _prompt_str("Phone (optional)")
    # basic email validation
    while True:
        email = _prompt_str("Email (optional)")
        if email is None or _validate_email(email):
            break
        print("  Invalid email format.")
    notes = _prompt_str("Notes (optional)")
    followup_days = _prompt_int(f"Follow up in how many days?", DEFAULT_FOLLOWUP_DAYS, min_val=0)

    lead_id, dupes = add_lead(name, service, phone=phone, email=email,
                               notes=notes, followup_days=followup_days)
    if dupes:
        print(f"\nWarning: {len(dupes)} existing lead(s) with same name:")
        for dup in dupes:
            print(f"   [{dup['id']}] {dup['name']} — {dup['service'] or 'N/A'} ({dup['status']})")
    print(f"\nLead added (id={lead_id}) — follow-up in {followup_days} day(s).")


def cmd_edit(args):
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    print(f"Editing [{lead['id']}] {lead['name']} (leave blank to keep current)\n")

    fields = {}
    for field, label in [
        ("name", f"Name [{lead['name']}]"),
        ("service", f"Service [{lead['service'] or ''}]"),
        ("phone", f"Phone [{lead['phone'] or ''}]"),
        ("email", f"Email [{lead['email'] or ''}]"),
        ("notes", f"Notes [{lead['notes'] or ''}]"),
        ("follow_up_after", f"Follow-up date [{str(lead['follow_up_after'] or '')[:10]}] (YYYY-MM-DD)"),
    ]:
        val = input(f"  {label}: ").strip()
        if val:
            if len(val) > MAX_FIELD_LENGTH:
                print(f"  Skipping {field} — too long (max {MAX_FIELD_LENGTH} chars).")
                continue
            if field == "email" and not _validate_email(val):
                print("  Skipping email — invalid format.")
                continue
            if field == "follow_up_after" and not _validate_date(val):
                print("  Skipping follow_up_after — use YYYY-MM-DD format.")
                continue
            fields[field] = val

    if not fields:
        print("No changes made.")
        return
    update_lead(lead["id"], **fields)
    print(f"Updated {len(fields)} field(s) on [{lead['id']}] {lead['name']}.")


def cmd_delete(args):
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    confirm = input(f"Delete [{lead['id']}] {lead['name']}? This cannot be undone. (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Cancelled.")
        return
    delete_lead(lead["id"])
    print(f"Deleted [{lead['id']}] {lead['name']}.")


def cmd_quote(args):
    if args.amount <= 0:
        print("Quote amount must be greater than zero.")
        return
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    update_quote(lead["id"], args.amount)
    print(f"[{lead['id']}] {lead['name']} — quote set to ${args.amount:,.0f}, follow-up in {DEFAULT_FOLLOWUP_DAYS} days.")


def cmd_won(args):
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    mark_won(lead["id"])
    print(f"[{lead['id']}] {lead['name']} marked as WON")


def cmd_lost(args):
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    notes = None
    if args.reason == "other":
        while True:
            notes = input("Notes for 'other' reason (required): ").strip()
            if notes:
                break
            print("Notes are required when reason is 'other'.")
    mark_lost(lead["id"], args.reason, notes=notes)
    print(f"[{lead['id']}] {lead['name']} marked as LOST — reason: {args.reason}")


def cmd_draft(args):
    if not check_api_key():
        print("ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key.")
        return
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    print(f"Drafting follow-up for {lead['name']}...\n")
    draft = draft_followup(dict(lead))
    if draft:
        print("--- Draft ---")
        print(draft)


def cmd_summarize(args):
    if not check_api_key():
        print("ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key.")
        return
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    print(fmt_lead(lead))
    print("\n--- AI Summary ---")
    result = summarize_lead(dict(lead))
    if result:
        print(result)


def cmd_digest(args):
    promoted = mark_stale_leads_followup_due()
    if promoted:
        print(f"Auto-promoted {promoted} lead(s) to followup_due\n")

    summary, totals = get_pipeline_summary()
    print("=== Pipeline Digest ===")
    print_pipeline_summary(summary, totals)

    stale = get_stale_leads()
    if stale:
        print(f"\n=== Needs Action ({len(stale)}) ===")
        for lead in stale[:5]:
            print(f"  [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'} (due {str(lead['follow_up_after'])[:10]})")
        if len(stale) > 5:
            print(f"  ... and {len(stale) - 5} more")

    if not check_api_key():
        print("\n(AI analysis unavailable — ANTHROPIC_API_KEY not set)")


def cmd_pipeline(args):
    if not check_api_key():
        print("ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key.")
        return
    leads = [dict(r) for r in get_all_active_leads()]
    summary, totals = get_pipeline_summary()
    closed, loss_reasons = get_closed_summary()

    print("=== Pipeline Summary ===")
    print_pipeline_summary(summary, totals)

    if loss_reasons:
        print("\n=== Loss Reasons ===")
        for row in loss_reasons:
            print(f"  {row['lost_reason']}: {row['count']}")

    print("\n--- AI Analysis ---")
    result = summarize_pipeline(leads, list(summary))
    if result:
        print(result)


def cmd_import(args):
    """Import leads from a CSV file."""
    import csv

    path = args.file
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            print("CSV appears empty or has no header row.")
            return

        # Normalize header names: lowercase + strip whitespace
        reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]

        required = {"name", "service"}
        missing = required - set(reader.fieldnames)
        if missing:
            print(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
            print("Required: name, service")
            print("Optional: phone, email, notes, followup_days")
            return

        rows = list(reader)

    if not rows:
        print("CSV has a header row but no data rows.")
        return

    if not args.yes:
        confirm = input(f"Import {len(rows)} row(s) from {path}? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("Cancelled.")
            return

    result = import_leads_from_rows(rows)
    print(f"Imported {result['imported']} lead(s), skipped {result['skipped']}.")
    for err in result["errors"]:
        print(f"  ! {err}")


def cmd_export(args):
    """Export all leads to CSV."""
    import csv

    leads = get_all_leads(limit=100000)
    if not leads:
        print("No leads to export.")
        return

    out = args.output or "leads_export.csv"
    fields = ["id", "name", "phone", "email", "service", "status",
              "lost_reason", "lost_reason_notes", "quote_amount",
              "created_at", "last_contact_at", "follow_up_after", "notes"]

    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            writer.writerow(dict(lead))

    print(f"Exported {len(leads)} leads to {out}")


# ---------------------------------------------------------------------------
# CLI setup
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="leadclaw",
        description="Lightweight lead tracking for local service businesses.",
    )
    parser.add_argument("--plain", action="store_true", help="Plain text output (no emoji)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("today", help="Leads due today")
    sub.add_parser("stale", help="Overdue follow-ups")

    p_list = sub.add_parser("list", help="List leads")
    p_list.add_argument("--all", action="store_true", help="Include won/lost leads")
    p_list.add_argument("--limit", type=int, default=MAX_LIST_ROWS, help=f"Max rows (default {MAX_LIST_ROWS})")
    p_list.add_argument("--offset", type=int, default=0, help="Row offset for pagination")

    p_lead = sub.add_parser("lead", help="Look up a lead",
                             epilog="Examples:\n  leadclaw lead Mike\n  leadclaw lead --id 7",
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    p_lead.add_argument("name", nargs="?", default="")
    p_lead.add_argument("--id", type=int, help="Look up by lead ID")

    sub.add_parser("add", help="Add a new lead (interactive)")

    p_edit = sub.add_parser("edit", help="Edit a lead (interactive)",
                             epilog="Examples:\n  leadclaw edit Mike\n  leadclaw edit --id 7",
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    p_edit.add_argument("name", nargs="?", default="")
    p_edit.add_argument("--id", type=int)

    p_del = sub.add_parser("delete", help="Delete a lead",
                            epilog="Examples:\n  leadclaw delete Mike\n  leadclaw delete --id 7",
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    p_del.add_argument("name", nargs="?", default="")
    p_del.add_argument("--id", type=int)

    p_quote = sub.add_parser("quote", help="Set a quote amount",
                              epilog="Examples:\n  leadclaw quote Mike 850\n  leadclaw quote --id 7 850",
                              formatter_class=argparse.RawDescriptionHelpFormatter)
    p_quote.add_argument("name", nargs="?", default="")
    p_quote.add_argument("amount", type=float, help="Quote amount (must be > 0)")
    p_quote.add_argument("--id", type=int)

    p_won = sub.add_parser("won", help="Mark a lead as won",
                            epilog="Examples:\n  leadclaw won Mike\n  leadclaw won --id 7",
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    p_won.add_argument("name", nargs="?", default="")
    p_won.add_argument("--id", type=int)

    p_lost = sub.add_parser("lost", help="Mark a lead as lost",
                             epilog=f"Reasons: {', '.join(LOST_REASONS)}\nExamples:\n  leadclaw lost Mike price\n  leadclaw lost --id 7 other",
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    p_lost.add_argument("name", nargs="?", default="")
    p_lost.add_argument("reason", choices=LOST_REASONS)
    p_lost.add_argument("--id", type=int)

    p_draft = sub.add_parser("draft-followup", help="Draft a follow-up text via AI",
                              epilog="Examples:\n  leadclaw draft-followup Mike\n  leadclaw draft-followup --id 7",
                              formatter_class=argparse.RawDescriptionHelpFormatter)
    p_draft.add_argument("name", nargs="?", default="")
    p_draft.add_argument("--id", type=int)

    p_sum = sub.add_parser("summarize", help="AI summary of a lead",
                            epilog="Examples:\n  leadclaw summarize Mike\n  leadclaw summarize --id 7",
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    p_sum.add_argument("name", nargs="?", default="")
    p_sum.add_argument("--id", type=int)

    sub.add_parser("digest", help="Pipeline snapshot + promote stale leads")
    sub.add_parser("pipeline", help="Full AI pipeline analysis")

    p_export = sub.add_parser("export", help="Export all leads to CSV")
    p_export.add_argument("--output", "-o", default=None, help="Output file (default: leads_export.csv)")

    p_import = sub.add_parser(
        "import",
        help="Import leads from a CSV file",
        epilog=(
            "Required columns: name, service\n"
            "Optional columns: phone, email, notes, followup_days\n"
            "Example: leadclaw import leads.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_import.add_argument("file", help="Path to CSV file")
    p_import.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    return parser


COMMAND_MAP = {
    "today": cmd_today,
    "stale": cmd_stale,
    "list": cmd_list,
    "lead": cmd_lead,
    "add": cmd_add,
    "edit": cmd_edit,
    "delete": cmd_delete,
    "quote": cmd_quote,
    "won": cmd_won,
    "lost": cmd_lost,
    "draft-followup": cmd_draft,
    "summarize": cmd_summarize,
    "digest": cmd_digest,
    "pipeline": cmd_pipeline,
    "export": cmd_export,
    "import": cmd_import,
}


def main():
    global _PLAIN
    parser = build_parser()
    args = parser.parse_args()
    _PLAIN = getattr(args, "plain", False)
    try:
        COMMAND_MAP[args.command](args)
    except KeyboardInterrupt:
        print("\nAborted.")
    except SystemExit:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
