"""
commands.py - CLI entry point using argparse
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Optional

import leadclaw.pilot as _pilot
from leadclaw.config import (
    DEFAULT_FOLLOWUP_DAYS,
    LOST_REASONS,
    MAX_FIELD_LENGTH,
    MAX_LIST_ROWS,
    MAX_NAME_LENGTH,
    STATUS_LABELS,
)
from leadclaw.drafting import (
    MSG_TYPES,
    check_api_key,
    draft_followup,
    draft_message,
    draft_pilot_outreach,
    summarize_lead,
    summarize_pilot_reply,
    summarize_pipeline,
)
from leadclaw.queries import (
    DISMISSAL_FIELDS,
    add_lead,
    delete_lead,
    dismiss_reminder_standalone,
    get_all_active_leads,
    get_all_leads,
    get_closed_summary,
    get_event_counts,
    get_invoice_reminders,
    get_job_today_leads,
    get_lead_by_id,
    get_lead_by_name,
    get_pipeline_summary,
    get_reactivation_leads,
    get_review_reminders,
    get_service_reminders,
    get_stale_leads,
    get_today_leads,
    import_leads_from_rows,
    mark_booked,
    mark_completed,
    mark_invoice_sent,
    mark_lost,
    mark_paid,
    mark_stale_leads_followup_due,
    mark_won,
    set_next_service,
    update_lead,
    update_quote,
)

# Runtime flag — set by build_parser() based on --plain
_PLAIN = False

STATUS_LABELS_PLAIN = {
    "new": "[new]",
    "quoted": "[quoted]",
    "followup_due": "[followup_due]",
    "booked": "[booked]",
    "completed": "[completed]",
    "paid": "[paid]",
    "won": "[won]",
    "lost": "[lost]",
}


def _status_label(status: str) -> str:
    if _PLAIN:
        return STATUS_LABELS_PLAIN.get(status, status)
    return STATUS_LABELS.get(status, status)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _safe(row, key, default=None):
    """Safe dict/Row access for columns that may not exist on old rows."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def fmt_lead(lead) -> str:
    prefix = (
        _status_label(lead["status"]).split()[0] if not _PLAIN else _status_label(lead["status"])
    )
    lines = [f"{prefix} [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'}"]
    lines.append(f"   Status: {lead['status']}")
    if lead["quote_amount"]:
        lines.append(f"   Quote:  ${lead['quote_amount']:,.0f}")
    if _safe(lead, "scheduled_date"):
        lines.append(f"   Scheduled: {str(_safe(lead, 'scheduled_date'))[:10]}")
    if _safe(lead, "invoice_amount"):
        lines.append(f"   Invoice: ${_safe(lead, 'invoice_amount'):,.0f}")
    if _safe(lead, "paid_at"):
        lines.append(f"   Paid:    {str(_safe(lead, 'paid_at'))[:10]}")
    if _safe(lead, "next_service_due_at"):
        lines.append(f"   Next svc: {str(_safe(lead, 'next_service_due_at'))[:10]}")
    if _safe(lead, "invoice_reminder_at"):
        lines.append(f"   Invoice reminder: {str(_safe(lead, 'invoice_reminder_at'))[:10]}")
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
    if lead_id is not None:
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
            print(
                f"   [{match['id']}] {match['name']} — {match['service'] or 'N/A'} ({match['status']})"
            )
        print(f"   Using most recent: [{lead['id']}] {lead['name']}")
        print("   Tip: use --id <id> to be explicit.\n")
    return lead


def _validate_email(val: str) -> bool:
    return "@" in val and "." in val.split("@")[-1]


def _validate_date(val: str) -> bool:
    try:
        datetime.strptime(val, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _prompt_str(
    label: str, current: str = "", required: bool = False, max_len: int = MAX_FIELD_LENGTH
) -> Optional[str]:
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
        try:
            val = int(raw)
        except ValueError:
            print(f"  Enter a whole number >= {min_val}.")
            continue
        if val < min_val:
            print(f"  Enter a whole number >= {min_val}.")
            continue
        return val


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
    followup_days = _prompt_int("Follow up in how many days?", DEFAULT_FOLLOWUP_DAYS, min_val=0)

    lead_id, dupes = add_lead(
        name, service, phone=phone, email=email, notes=notes, followup_days=followup_days
    )
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
        (
            "follow_up_after",
            f"Follow-up date [{str(lead['follow_up_after'] or '')[:10]}] (YYYY-MM-DD)",
        ),
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
    confirm = (
        input(f"Delete [{lead['id']}] {lead['name']}? This cannot be undone. (yes/no): ")
        .strip()
        .lower()
    )
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
    print(
        f"[{lead['id']}] {lead['name']} — quote set to ${args.amount:,.0f}, follow-up in {DEFAULT_FOLLOWUP_DAYS} days."
    )


def cmd_won(args):
    """Backward-compatible: 'won' is now an alias for 'paid' in the lifecycle.
    Use 'leadclaw paid' for the preferred command.
    """
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    mark_won(lead["id"])
    print(f"[{lead['id']}] {lead['name']} marked as WON (tip: use 'paid' for the full lifecycle)")


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


def cmd_book(args):
    """Mark a lead as booked with a scheduled date."""
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    scheduled = args.date
    if not _validate_date(scheduled):
        print("Invalid date format. Use YYYY-MM-DD.")
        return
    mark_booked(lead["id"], scheduled)
    print(f"[{lead['id']}] {lead['name']} → booked for {scheduled}.")


def cmd_complete(args):
    """Mark a booked job as completed."""
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    mark_completed(lead["id"])
    print(
        f"[{lead['id']}] {lead['name']} → completed. Run: leadclaw invoice {lead['name']} to send invoice."
    )


def cmd_invoice(args):
    """Record that an invoice was sent (optionally override amount)."""
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    amount = args.amount  # may be None
    if amount is not None and amount <= 0:
        print("Invoice amount must be greater than zero.")
        return
    from leadclaw.config import DEFAULT_INVOICE_REMINDER_DAYS

    mark_invoice_sent(
        lead["id"], invoice_amount=amount, reminder_days=DEFAULT_INVOICE_REMINDER_DAYS
    )
    display_amount = amount or lead["quote_amount"]
    amt_str = f"${display_amount:,.0f}" if display_amount else "(no amount)"
    print(
        f"[{lead['id']}] {lead['name']} — invoice sent {amt_str}. Reminder in {DEFAULT_INVOICE_REMINDER_DAYS} days."
    )


def cmd_paid(args):
    """Mark a lead as paid and optionally schedule next service."""
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    from leadclaw.config import DEFAULT_RECURRING_DAYS

    recurring = (
        args.recurring
        if hasattr(args, "recurring") and args.recurring is not None
        else DEFAULT_RECURRING_DAYS
    )
    mark_paid(lead["id"], recurring_days=recurring)
    print(f"[{lead['id']}] {lead['name']} → PAID 💰. Next service reminder in {recurring} days.")


def cmd_next_service(args):
    """Set or update the next service due date for a lead."""
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    date_val = args.date
    if not _validate_date(date_val):
        print("Invalid date format. Use YYYY-MM-DD.")
        return
    set_next_service(lead["id"], date_val)
    print(f"[{lead['id']}] {lead['name']} — next service set to {date_val}.")


def cmd_reminders(args):
    """Show all pending reminders across all categories."""
    job_today = get_job_today_leads()
    invoice_due = get_invoice_reminders()
    review_due = get_review_reminders()
    service_due = get_service_reminders()
    react_30 = get_reactivation_leads(30)
    react_60 = get_reactivation_leads(60)
    react_90 = get_reactivation_leads(90)

    any_results = any(
        [job_today, invoice_due, review_due, service_due, react_30, react_60, react_90]
    )

    if not any_results:
        print("No pending reminders.")
        return

    def _print_section(title, leads, extra_fn=None):
        if not leads:
            return
        print(f"\n=== {title} ({len(leads)}) ===")
        for lead in leads:
            base = f"  [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'}"
            extra = extra_fn(lead) if extra_fn else ""
            print(base + extra)

    def _invoice_extra(lead):
        if lead.get("invoice_amount"):
            return f" — ${lead['invoice_amount']:,.0f}"
        if lead.get("quote_amount"):
            return f" — ${lead['quote_amount']:,.0f}"
        return ""

    _print_section(
        "Jobs Today",
        job_today,
        lambda lead: f" — scheduled {str(lead.get('scheduled_date') or '')[:10]}",
    )
    _print_section("Invoice Reminders", invoice_due, _invoice_extra)
    _print_section(
        "Review Requests",
        review_due,
        lambda lead: f" — completed {str(lead.get('completed_at') or '')[:10]}",
    )
    _print_section(
        "Recurring Service Due",
        service_due,
        lambda lead: f" — due {str(lead.get('service_reminder_at') or '')[:10]}",
    )
    _print_section("Reactivation — 30 days", react_30)
    _print_section("Reactivation — 60 days", react_60)
    _print_section("Reactivation — 90 days", react_90)

    print("\nTip: leadclaw draft-message <name> --type <type>")
    print("Tip: leadclaw dismiss-reminder <name> --type <review_request|reactivation|job_today>")


def cmd_dismiss_reminder(args):
    """Mark a reminder as dismissed/sent for a lead."""
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    reminder_type = args.type
    if reminder_type not in DISMISSAL_FIELDS:
        print(
            f"Unknown reminder type '{reminder_type}'. Valid types: {', '.join(DISMISSAL_FIELDS)}"
        )
        return
    ok = dismiss_reminder_standalone(lead["id"], reminder_type)
    if ok:
        label = {
            "review_request": "Review request marked sent",
            "reactivation": "Reactivation reminder dismissed",
            "job_today": "Job reminder dismissed for today",
        }.get(reminder_type, "Dismissed")
        print(f"[{lead['id']}] {lead['name']} — {label}.")
    else:
        print("Could not dismiss reminder (lead not found or no change).")


def cmd_usage(args):
    """Show event counts by type for the last 30 days and all-time."""
    last30 = get_event_counts(days=30)
    alltime = get_event_counts()

    alltime_by_type = {row["event_type"]: row["count"] for row in alltime}

    if not alltime:
        print("No usage events recorded yet.")
        return

    print("=== Usage Stats ===")
    print(f"{'Event Type':<25} {'Last 30 Days':>12} {'All Time':>10}")
    print("-" * 50)
    last30_by_type = {row["event_type"]: row["count"] for row in last30}
    for event_type, total in sorted(alltime_by_type.items(), key=lambda x: -x[1]):
        last_30 = last30_by_type.get(event_type, 0)
        print(f"{event_type:<25} {last_30:>12} {total:>10}")
    print("-" * 50)
    total_all = sum(alltime_by_type.values())
    total_30 = sum(last30_by_type.values())
    print(f"{'TOTAL':<25} {total_30:>12} {total_all:>10}")


def cmd_draft_message(args):
    """Generate a copy-ready message for a lead by type."""
    lead = resolve_lead(getattr(args, "name", ""), getattr(args, "id", None))
    if not lead:
        return
    msg_type = args.type
    if msg_type not in MSG_TYPES:
        print(f"Unknown type '{msg_type}'. Valid types:")
        for t in MSG_TYPES:
            print(f"  {t}")
        return
    msg = draft_message(dict(lead), msg_type)
    print(f"\n--- {msg_type.replace('_', ' ').title()} ---")
    print(msg)
    print()


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
            print(
                f"  [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'} (due {str(lead['follow_up_after'])[:10]})"
            )
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
    fields = [
        "id",
        "name",
        "phone",
        "email",
        "service",
        "status",
        "lost_reason",
        "lost_reason_notes",
        "quote_amount",
        "created_at",
        "last_contact_at",
        "follow_up_after",
        "notes",
    ]

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
    p_list.add_argument(
        "--limit", type=int, default=MAX_LIST_ROWS, help=f"Max rows (default {MAX_LIST_ROWS})"
    )
    p_list.add_argument("--offset", type=int, default=0, help="Row offset for pagination")

    p_lead = sub.add_parser(
        "lead",
        help="Look up a lead",
        epilog="Examples:\n  leadclaw lead Mike\n  leadclaw lead --id 7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_lead.add_argument("name", nargs="?", default="")
    p_lead.add_argument("--id", type=int, help="Look up by lead ID")

    sub.add_parser("add", help="Add a new lead (interactive)")

    p_edit = sub.add_parser(
        "edit",
        help="Edit a lead (interactive)",
        epilog="Examples:\n  leadclaw edit Mike\n  leadclaw edit --id 7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_edit.add_argument("name", nargs="?", default="")
    p_edit.add_argument("--id", type=int)

    p_del = sub.add_parser(
        "delete",
        help="Delete a lead",
        epilog="Examples:\n  leadclaw delete Mike\n  leadclaw delete --id 7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_del.add_argument("name", nargs="?", default="")
    p_del.add_argument("--id", type=int)

    p_quote = sub.add_parser(
        "quote",
        help="Set a quote amount",
        epilog="Examples:\n  leadclaw quote Mike 850\n  leadclaw quote --id 7 850",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_quote.add_argument("name", nargs="?", default="")
    p_quote.add_argument("amount", type=float, help="Quote amount (must be > 0)")
    p_quote.add_argument("--id", type=int)

    p_won = sub.add_parser(
        "won",
        help="Mark a lead as won",
        epilog="Examples:\n  leadclaw won Mike\n  leadclaw won --id 7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_won.add_argument("name", nargs="?", default="")
    p_won.add_argument("--id", type=int)

    p_lost = sub.add_parser(
        "lost",
        help="Mark a lead as lost",
        epilog=f"Reasons: {', '.join(LOST_REASONS)}\nExamples:\n  leadclaw lost Mike price\n  leadclaw lost --id 7 other",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_lost.add_argument("name", nargs="?", default="")
    p_lost.add_argument("reason", choices=LOST_REASONS)
    p_lost.add_argument("--id", type=int)

    p_book = sub.add_parser(
        "book",
        help="Mark a lead as booked with a scheduled date",
        epilog="Examples:\n  leadclaw book Mike 2026-04-10\n  leadclaw book --id 7 2026-04-10",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_book.add_argument("name", nargs="?", default="")
    p_book.add_argument("date", help="Scheduled date (YYYY-MM-DD)")
    p_book.add_argument("--id", type=int)

    p_complete = sub.add_parser(
        "complete",
        help="Mark a booked job as completed",
        epilog="Examples:\n  leadclaw complete Mike\n  leadclaw complete --id 7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_complete.add_argument("name", nargs="?", default="")
    p_complete.add_argument("--id", type=int)

    p_invoice = sub.add_parser(
        "invoice",
        help="Record invoice sent (optionally override amount)",
        epilog="Examples:\n  leadclaw invoice Mike\n  leadclaw invoice Mike 950\n  leadclaw invoice --id 7 950",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_invoice.add_argument("name", nargs="?", default="")
    p_invoice.add_argument(
        "amount",
        type=float,
        nargs="?",
        default=None,
        help="Invoice amount (default: same as quote)",
    )
    p_invoice.add_argument("--id", type=int)

    p_paid = sub.add_parser(
        "paid",
        help="Mark a lead as paid",
        epilog="Examples:\n  leadclaw paid Mike\n  leadclaw paid Mike --recurring 30\n  leadclaw paid --id 7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_paid.add_argument("name", nargs="?", default="")
    p_paid.add_argument("--id", type=int)
    p_paid.add_argument(
        "--recurring",
        type=int,
        default=None,
        help="Days until next service reminder (default: LEADCLAW_RECURRING_DAYS or 90)",
    )

    p_nextsvc = sub.add_parser(
        "next-service",
        help="Set or update next service due date",
        epilog="Examples:\n  leadclaw next-service Mike 2026-07-01\n  leadclaw next-service --id 7 2026-07-01",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_nextsvc.add_argument("name", nargs="?", default="")
    p_nextsvc.add_argument("date", help="Next service date (YYYY-MM-DD)")
    p_nextsvc.add_argument("--id", type=int)

    sub.add_parser(
        "reminders", help="Show all pending reminders (jobs, invoices, reviews, reactivations)"
    )

    p_dismiss = sub.add_parser(
        "dismiss-reminder",
        help="Mark a reminder as sent/dismissed for a lead",
        epilog=(
            "Types: review_request, reactivation, job_today\n\n"
            "Examples:\n"
            "  leadclaw dismiss-reminder Mike --type review_request\n"
            "  leadclaw dismiss-reminder --id 7 --type reactivation"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_dismiss.add_argument("name", nargs="?", default="")
    p_dismiss.add_argument("--id", type=int)
    p_dismiss.add_argument(
        "--type",
        required=True,
        dest="type",
        help="Reminder type: review_request, reactivation, job_today",
    )

    sub.add_parser("usage", help="Show pilot usage event counts by type")

    p_dm = sub.add_parser(
        "draft-message",
        help="Generate a copy-ready message for a lead",
        epilog=(
            "Types: quote_followup, booking_confirmation, on_my_way, running_late,\n"
            "       review_request, reactivation_30, reactivation_60, reactivation_90\n\n"
            "Examples:\n"
            "  leadclaw draft-message Mike --type quote_followup\n"
            "  leadclaw draft-message --id 7 --type on_my_way"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_dm.add_argument("name", nargs="?", default="")
    p_dm.add_argument("--id", type=int)
    p_dm.add_argument(
        "--type", required=True, dest="type", help="Message type (see --help for full list)"
    )

    p_draft = sub.add_parser(
        "draft-followup",
        help="Draft a follow-up text via AI",
        epilog="Examples:\n  leadclaw draft-followup Mike\n  leadclaw draft-followup --id 7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_draft.add_argument("name", nargs="?", default="")
    p_draft.add_argument("--id", type=int)

    p_sum = sub.add_parser(
        "summarize",
        help="AI summary of a lead",
        epilog="Examples:\n  leadclaw summarize Mike\n  leadclaw summarize --id 7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_sum.add_argument("name", nargs="?", default="")
    p_sum.add_argument("--id", type=int)

    sub.add_parser("digest", help="Pipeline snapshot + promote stale leads")
    sub.add_parser("pipeline", help="Full AI pipeline analysis")

    p_export = sub.add_parser("export", help="Export all leads to CSV")
    p_export.add_argument(
        "--output", "-o", default=None, help="Output file (default: leads_export.csv)"
    )

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

    # ---------------------------------------------------------------------------
    # Pilot subcommands
    # ---------------------------------------------------------------------------
    pilot_p = sub.add_parser("pilot", help="Pilot candidate tracker")
    pilot_sub = pilot_p.add_subparsers(dest="pilot_cmd", required=True)

    pilot_sub.add_parser("status", help="Pilot tracker summary")

    p_plist = pilot_sub.add_parser("list", help="List pilot candidates")
    p_plist.add_argument("--status", default=None, choices=_pilot.STATUSES, help="Filter by status")
    p_plist.add_argument("--limit", type=int, default=50)

    p_pshow = pilot_sub.add_parser("show", help="Show a candidate")
    p_pshow.add_argument("name", nargs="?", default="")
    p_pshow.add_argument("--id", type=int)

    pilot_sub.add_parser("add", help="Add a candidate (interactive)")

    p_pimp = pilot_sub.add_parser(
        "import",
        help="Import candidates from CSV",
        epilog="Required: name. Optional: service_type, phone, email, business_name, location, notes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_pimp.add_argument("file")
    p_pimp.add_argument("--yes", "-y", action="store_true")

    p_pdraft = pilot_sub.add_parser("draft", help="AI outreach draft for a candidate")
    p_pdraft.add_argument("name", nargs="?", default="")
    p_pdraft.add_argument("--id", type=int)

    p_papprove = pilot_sub.add_parser("approve", help="Approve a draft for sending")
    p_papprove.add_argument("name", nargs="?", default="")
    p_papprove.add_argument("--id", type=int)

    p_psent = pilot_sub.add_parser("mark-sent", help="Mark outreach as sent")
    p_psent.add_argument("name", nargs="?", default="")
    p_psent.add_argument("--id", type=int)

    p_preply = pilot_sub.add_parser("log-reply", help="Log a reply and get AI summary")
    p_preply.add_argument("name", nargs="?", default="")
    p_preply.add_argument("--id", type=int)

    p_pconv = pilot_sub.add_parser("convert", help="Mark candidate as converted to pilot user")
    p_pconv.add_argument("name", nargs="?", default="")
    p_pconv.add_argument("--id", type=int)

    p_ppass = pilot_sub.add_parser("pass", help="Mark candidate as passed")
    p_ppass.add_argument("name", nargs="?", default="")
    p_ppass.add_argument("--id", type=int)

    pilot_sub.add_parser("followups", help="Candidates overdue for follow-up")

    p_pexport = pilot_sub.add_parser("export", help="Export candidates to CSV")
    p_pexport.add_argument("--output", "-o", default=None)

    return parser


# ---------------------------------------------------------------------------
# Pilot command handlers
# ---------------------------------------------------------------------------


def _resolve_candidate(name: str, cid: Optional[int] = None):
    if cid:
        c = _pilot.get_candidate_by_id(cid)
        if not c:
            print(f"No candidate with id {cid}.")
        return c
    if not name:
        print("Provide a name or --id.")
        return None
    c, all_matches = _pilot.get_candidate_by_name(name)
    if not c:
        print(f"No candidate matching '{name}'.")
        return None
    if len(all_matches) > 1:
        print(f"Multiple matches for '{name}':")
        for m in all_matches:
            print(f"   [{m['id']}] {m['name']} — {m['service_type'] or 'N/A'} ({m['status']})")
        print(f"   Using: [{c['id']}] {c['name']}. Use --id to be explicit.\n")
    return c


def _fmt_candidate(c) -> str:
    lines = [f"[{c['status']}] [{c['id']}] {c['name']}"]
    if c["business_name"] and c["business_name"] != c["name"]:
        lines.append(f"   Business: {c['business_name']}")
    lines.append(f"   Service:  {c['service_type'] or 'N/A'}")
    if c["location"]:
        lines.append(f"   Location: {c['location']}")
    if c["phone"]:
        lines.append(f"   Phone:    {c['phone']}")
    if c["email"]:
        lines.append(f"   Email:    {c['email']}")
    lines.append(f"   Score:    {c['score']}/100  Source: {c['source']}")
    if c["follow_up_after"]:
        lines.append(f"   Follow up: {str(c['follow_up_after'])[:10]}")
    if c["outreach_draft"]:
        lines.append(
            f"   Draft:    {c['outreach_draft'][:80]}{'...' if len(c['outreach_draft']) > 80 else ''}"
        )
    if c["reply_summary"]:
        lines.append(f"   Reply summary: {c['reply_summary']}")
    if c["notes"]:
        lines.append(f"   Notes:    {c['notes']}")
    return "\n".join(lines)


def cmd_pilot(args):
    subcmd = args.pilot_cmd

    if subcmd == "status":
        summary = _pilot.get_pilot_summary()
        print(f"=== Pilot Tracker ({summary['total']} total) ===")
        for s in _pilot.STATUSES:
            count = summary["by_status"].get(s, 0)
            if count:
                print(f"  {s}: {count}")
        followups = _pilot.get_followup_due()
        if followups:
            print(f"\n  {len(followups)} overdue follow-up(s) — run: leadclaw pilot followups")

    elif subcmd == "list":
        candidates = _pilot.get_all_candidates(status=args.status, limit=args.limit)
        label = f"Pilot Candidates — {args.status or 'all'}"
        print(f"=== {label} ({len(candidates)}) ===\n")
        if not candidates:
            print("None found.")
            return
        for c in candidates:
            print(_fmt_candidate(c))
            print()

    elif subcmd == "show":
        c = _resolve_candidate(getattr(args, "name", ""), getattr(args, "id", None))
        if c:
            print(_fmt_candidate(c))
            if c["outreach_draft"]:
                print(f"\n--- Draft ---\n{c['outreach_draft']}")
            if c["reply_text"]:
                print(f"\n--- Reply ---\n{c['reply_text']}")
            if c["reply_summary"]:
                print(f"\n--- Reply Summary ---\n{c['reply_summary']}")

    elif subcmd == "add":
        print("=== Add Pilot Candidate ===")
        name = _prompt_str("Name", required=True, max_len=MAX_NAME_LENGTH)
        business = _prompt_str("Business name (optional)")
        service = _prompt_str("Service type (e.g. lawn care, roofing)")
        phone = _prompt_str("Phone (optional)")
        email = _prompt_str("Email (optional)")
        location = _prompt_str("Location/city (optional)")
        notes = _prompt_str("Notes (optional)")
        cid, dupes = _pilot.add_candidate(
            name=name,
            business_name=business,
            service_type=service,
            phone=phone,
            email=email,
            location=location,
            notes=notes,
            source="manual_entry",
        )
        if dupes:
            print(f"\nWarning: {len(dupes)} existing candidate(s) with similar name:")
            for d in dupes:
                print(f"   [{d['id']}] {d['name']} — {d['service_type'] or 'N/A'} ({d['status']})")
        c = _pilot.get_candidate_by_id(cid)
        print(f"\nAdded [{cid}] {name} — score {c['score']}/100")

    elif subcmd == "import":
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
            reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
            if "name" not in reader.fieldnames:
                print("CSV must have a 'name' column.")
                print("Optional: service_type, phone, email, business_name, location, notes")
                return
            rows = list(reader)
        if not rows:
            print("No data rows found.")
            return
        if not args.yes:
            confirm = (
                input(f"Import {len(rows)} candidate(s) from {path}? (yes/no): ").strip().lower()
            )
            if confirm != "yes":
                print("Cancelled.")
                return
        result = _pilot.import_candidates_from_rows(rows)
        print(f"Imported {result['imported']} candidate(s), skipped {result['skipped']}.")
        for err in result["errors"]:
            print(f"  ! {err}")

    elif subcmd == "draft":
        if not check_api_key():
            print("ANTHROPIC_API_KEY not set — add it to .env")
            return
        c = _resolve_candidate(getattr(args, "name", ""), getattr(args, "id", None))
        if not c:
            return
        print(f"Drafting outreach for {c['name']}...\n")
        draft = draft_pilot_outreach(dict(c))
        if draft:
            print("--- Draft (review before approving) ---")
            print(draft)
            print()
            save = input("Save this draft? (yes/no): ").strip().lower()
            if save == "yes":
                _pilot.set_draft(c["id"], draft)
                print(f"Draft saved. Status → drafted. Run: leadclaw pilot approve {c['name']}")

    elif subcmd == "approve":
        c = _resolve_candidate(getattr(args, "name", ""), getattr(args, "id", None))
        if not c:
            return
        if not c["outreach_draft"]:
            print(f"No draft for [{c['id']}] {c['name']}. Run: leadclaw pilot draft {c['name']}")
            return
        print(f"--- Draft for {c['name']} ---")
        print(c["outreach_draft"])
        print()
        confirm = input("Approve for sending? (yes/no): ").strip().lower()
        if confirm == "yes":
            _pilot.set_status(c["id"], "approved")
            print(
                f"[{c['id']}] {c['name']} → approved. Mark sent when you've sent it: leadclaw pilot mark-sent {c['name']}"
            )
        else:
            print("Not approved. Run pilot draft again to regenerate.")

    elif subcmd == "mark-sent":
        c = _resolve_candidate(getattr(args, "name", ""), getattr(args, "id", None))
        if not c:
            return
        if c["status"] not in ("approved", "drafted"):
            print(f"Warning: status is '{c['status']}' — expected 'approved'. Marking sent anyway.")
        _pilot.set_status(c["id"], "sent", contacted=True)
        print(f"[{c['id']}] {c['name']} → sent. Follow-up scheduled.")

    elif subcmd == "log-reply":
        if not check_api_key():
            print("ANTHROPIC_API_KEY not set — add it to .env")
            return
        c = _resolve_candidate(getattr(args, "name", ""), getattr(args, "id", None))
        if not c:
            return
        print(f"Paste their reply for {c['name']} (press Enter twice when done):")
        lines = []
        while True:
            line = input()
            if not line and lines and not lines[-1]:
                break
            lines.append(line)
        reply = "\n".join(lines).strip()
        if not reply:
            print("No reply entered.")
            return
        _pilot.log_reply(c["id"], reply)
        print("Reply logged. Summarizing...")
        summary = summarize_pilot_reply(dict(c), reply)
        if summary:
            print(f"\n--- Summary ---\n{summary}")
            _pilot.set_reply_summary(c["id"], summary)
        print(
            f"\nStatus → replied. Next: leadclaw pilot convert {c['name']} or leadclaw pilot pass {c['name']}"
        )

    elif subcmd == "convert":
        c = _resolve_candidate(getattr(args, "name", ""), getattr(args, "id", None))
        if not c:
            return
        _pilot.set_status(c["id"], "converted")
        print(f"[{c['id']}] {c['name']} → converted pilot user! 🎉")

    elif subcmd == "pass":
        c = _resolve_candidate(getattr(args, "name", ""), getattr(args, "id", None))
        if not c:
            return
        _pilot.set_status(c["id"], "passed")
        print(f"[{c['id']}] {c['name']} → passed.")

    elif subcmd == "followups":
        candidates = _pilot.get_followup_due()
        if not candidates:
            print("No overdue pilot follow-ups.")
            return
        print(f"=== Pilot Follow-ups Due ({len(candidates)}) ===\n")
        for c in candidates:
            print(_fmt_candidate(c))
            print()

    elif subcmd == "export":
        import csv

        candidates = _pilot.get_all_candidates(limit=100000)
        if not candidates:
            print("No candidates to export.")
            return
        out = args.output or "pilot_export.csv"
        fields = [
            "id",
            "name",
            "business_name",
            "phone",
            "email",
            "service_type",
            "location",
            "source",
            "score",
            "status",
            "notes",
            "outreach_draft",
            "reply_text",
            "reply_summary",
            "contacted_at",
            "follow_up_after",
            "created_at",
        ]
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for c in candidates:
                writer.writerow(dict(c))
        print(f"Exported {len(candidates)} candidates to {out}")


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
    "book": cmd_book,
    "complete": cmd_complete,
    "invoice": cmd_invoice,
    "paid": cmd_paid,
    "next-service": cmd_next_service,
    "reminders": cmd_reminders,
    "dismiss-reminder": cmd_dismiss_reminder,
    "usage": cmd_usage,
    "draft-message": cmd_draft_message,
    "draft-followup": cmd_draft,
    "summarize": cmd_summarize,
    "digest": cmd_digest,
    "pipeline": cmd_pipeline,
    "export": cmd_export,
    "import": cmd_import,
    "pilot": cmd_pilot,
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
