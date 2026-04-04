"""
commands.py - CLI entry point using argparse
"""
import sys
import argparse
from queries import (
    get_today_leads, get_stale_leads, get_lead_by_name, get_lead_by_id,
    get_all_active_leads, get_all_leads, get_pipeline_summary, get_closed_summary,
    add_lead, update_lead, delete_lead, update_quote, mark_won, mark_lost,
    mark_stale_leads_followup_due,
)
from drafting import draft_followup, summarize_lead, summarize_pipeline
from config import STATUS_LABELS, LOST_REASONS, DEFAULT_FOLLOWUP_DAYS


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def fmt_lead(lead):
    emoji = STATUS_LABELS.get(lead["status"], "❓").split()[0]
    lines = [f"{emoji} [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'}"]
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
        label = STATUS_LABELS.get(row["status"], row["status"])
        val = f"  (${row['total_quoted']:,.0f})" if row["total_quoted"] else ""
        print(f"  {label}: {row['count']}{val}")
    print(f"\n  Open pipeline:  ${totals['open_value']:,.0f}")
    print(f"  Won (closed):   ${totals['won_value']:,.0f}")
    print(f"  Lost (closed):  ${totals['lost_value']:,.0f}")


def resolve_lead(name):
    """
    Resolve a name to a single lead. Warns if multiple matches found.
    Returns lead row or None.
    """
    lead, all_matches = get_lead_by_name(name)
    if not lead:
        print(f"No lead found matching '{name}'.")
        return None
    if len(all_matches) > 1:
        print(f"⚠️  Multiple matches for '{name}':")
        for m in all_matches:
            print(f"   [{m['id']}] {m['name']} — {m['service'] or 'N/A'} ({m['status']})")
        print(f"   Using most recent: [{lead['id']}] {lead['name']}")
        print(f"   (Use --id <id> if you want a different one)\n")
    return lead


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
        print("No stale leads. You're on top of it. ✅")
        return
    print(f"=== Stale Leads ({len(leads)}) ===\n")
    for lead in leads:
        print(fmt_lead(lead))
        print()


def cmd_list(args):
    leads = get_all_leads() if args.all else get_all_active_leads()
    if not leads:
        print("No leads found.")
        return
    label = "All Leads" if args.all else "Active Leads"
    print(f"=== {label} ({len(leads)}) ===\n")
    for lead in leads:
        print(fmt_lead(lead))
        print()


def cmd_lead(args):
    if args.id:
        lead = get_lead_by_id(args.id)
        if not lead:
            print(f"No lead found with id {args.id}.")
            return
    else:
        lead = resolve_lead(args.name)
        if not lead:
            return
    print(fmt_lead(lead))


def cmd_add(args):
    print("=== Add New Lead ===")
    name = input("Name: ").strip()
    if not name:
        print("Name is required.")
        return
    service = input("Service requested: ").strip()
    phone = input("Phone (optional): ").strip() or None
    email = input("Email (optional): ").strip() or None
    notes = input("Notes (optional): ").strip() or None
    days_str = input(f"Follow up in how many days? [{DEFAULT_FOLLOWUP_DAYS}]: ").strip()
    if days_str and not days_str.lstrip("-").isdigit():
        print(f"Invalid number '{days_str}', using {DEFAULT_FOLLOWUP_DAYS} days.")
        followup_days = DEFAULT_FOLLOWUP_DAYS
    elif days_str and int(days_str) < 0:
        print(f"Negative follow-up days not allowed, using {DEFAULT_FOLLOWUP_DAYS}.")
        followup_days = DEFAULT_FOLLOWUP_DAYS
    else:
        followup_days = int(days_str) if days_str else DEFAULT_FOLLOWUP_DAYS

    lead_id, dupes = add_lead(name, service, phone=phone, email=email,
                               notes=notes, followup_days=followup_days)
    if dupes:
        print(f"\n⚠️  Warning: {len(dupes)} existing lead(s) with similar name:")
        for d in dupes:
            print(f"   [{d['id']}] {d['name']} — {d['service'] or 'N/A'} ({d['status']})")
    print(f"\n✅ Lead added (id={lead_id}) — follow-up in {followup_days} day(s).")


def cmd_edit(args):
    lead = resolve_lead(args.name) if not args.id else get_lead_by_id(args.id)
    if not lead:
        print(f"No lead found.")
        return
    print(f"Editing [{lead['id']}] {lead['name']} (leave blank to keep current value)\n")

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
            fields[field] = val

    if not fields:
        print("No changes made.")
        return
    update_lead(lead["id"], **fields)
    print(f"✅ Updated {len(fields)} field(s) on [{lead['id']}] {lead['name']}.")


def cmd_delete(args):
    lead = resolve_lead(args.name) if not args.id else get_lead_by_id(args.id)
    if not lead:
        return
    confirm = input(f"Delete [{lead['id']}] {lead['name']}? This cannot be undone. (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Cancelled.")
        return
    delete_lead(lead["id"])
    print(f"🗑️  Deleted [{lead['id']}] {lead['name']}.")


def cmd_quote(args):
    lead = resolve_lead(args.name) if not args.id else get_lead_by_id(args.id)
    if not lead:
        return
    update_quote(lead["id"], args.amount)
    print(f"✅ [{lead['id']}] {lead['name']} — quote set to ${args.amount:,.0f}, follow-up in {DEFAULT_FOLLOWUP_DAYS} days.")


def cmd_won(args):
    lead = resolve_lead(args.name) if not args.id else get_lead_by_id(args.id)
    if not lead:
        return
    mark_won(lead["id"])
    print(f"✅ [{lead['id']}] {lead['name']} marked as WON 🎉")


def cmd_lost(args):
    lead = resolve_lead(args.name) if not args.id else get_lead_by_id(args.id)
    if not lead:
        return
    notes = None
    if args.reason == "other":
        notes = input("Notes for 'other' reason (required): ").strip() or None
    mark_lost(lead["id"], args.reason, notes=notes)
    print(f"❌ [{lead['id']}] {lead['name']} marked as LOST — reason: {args.reason}")


def cmd_draft(args):
    lead = resolve_lead(args.name) if not args.id else get_lead_by_id(args.id)
    if not lead:
        return
    print(f"Drafting follow-up for {lead['name']}...\n")
    draft = draft_followup(dict(lead))
    print("--- Draft ---")
    print(draft)


def cmd_summarize(args):
    lead = resolve_lead(args.name) if not args.id else get_lead_by_id(args.id)
    if not lead:
        return
    print(fmt_lead(lead))
    print("\n--- AI Summary ---")
    print(summarize_lead(dict(lead)))


def cmd_digest(args):
    promoted = mark_stale_leads_followup_due()
    if promoted:
        print(f"⚡ Auto-promoted {promoted} lead(s) to followup_due\n")

    summary, totals = get_pipeline_summary()
    print("=== Pipeline Digest ===")
    print_pipeline_summary(summary, totals)

    stale = get_stale_leads()
    if stale:
        print(f"\n=== Needs Action ({len(stale)}) ===")
        for lead in stale[:5]:
            print(f"  🔔 {lead['name']} — {lead['service'] or 'N/A'} (due {str(lead['follow_up_after'])[:10]})")
        if len(stale) > 5:
            print(f"  ... and {len(stale) - 5} more")


def cmd_pipeline(args):
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
    print(summarize_pipeline(leads, list(summary)))


# ---------------------------------------------------------------------------
# CLI setup
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="leadclaw",
        description="Lightweight lead tracking for local service businesses.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # today
    sub.add_parser("today", help="Leads due today")

    # stale
    sub.add_parser("stale", help="Overdue follow-ups")

    # list
    p_list = sub.add_parser("list", help="List all active leads (--all for everything)")
    p_list.add_argument("--all", action="store_true", help="Include won/lost leads")

    # lead
    p_lead = sub.add_parser("lead", help="Look up a lead by name")
    p_lead.add_argument("name", nargs="?", default="")
    p_lead.add_argument("--id", type=int, help="Look up by lead ID instead")

    # add
    sub.add_parser("add", help="Add a new lead (interactive)")

    # edit
    p_edit = sub.add_parser("edit", help="Edit a lead's fields (interactive)")
    p_edit.add_argument("name", nargs="?", default="")
    p_edit.add_argument("--id", type=int)

    # delete
    p_del = sub.add_parser("delete", help="Delete a lead")
    p_del.add_argument("name", nargs="?", default="")
    p_del.add_argument("--id", type=int)

    # quote
    p_quote = sub.add_parser("quote", help="Set or update a quote amount")
    p_quote.add_argument("name", nargs="?", default="")
    p_quote.add_argument("amount", type=float)
    p_quote.add_argument("--id", type=int)

    # won
    p_won = sub.add_parser("won", help="Mark a lead as won")
    p_won.add_argument("name", nargs="?", default="")
    p_won.add_argument("--id", type=int)

    # lost
    p_lost = sub.add_parser("lost", help="Mark a lead as lost")
    p_lost.add_argument("name", nargs="?", default="")
    p_lost.add_argument("reason", choices=LOST_REASONS)
    p_lost.add_argument("--id", type=int)

    # draft-followup
    p_draft = sub.add_parser("draft-followup", help="Draft a follow-up text via AI")
    p_draft.add_argument("name", nargs="?", default="")
    p_draft.add_argument("--id", type=int)

    # summarize
    p_sum = sub.add_parser("summarize", help="AI summary of a lead")
    p_sum.add_argument("name", nargs="?", default="")
    p_sum.add_argument("--id", type=int)

    # digest
    sub.add_parser("digest", help="Pipeline snapshot + promote stale leads")

    # pipeline
    sub.add_parser("pipeline", help="Full AI pipeline analysis")

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
}


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        COMMAND_MAP[args.command](args)
    except KeyboardInterrupt:
        print("\nAborted.")
    except SystemExit:
        raise
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
