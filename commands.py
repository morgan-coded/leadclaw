"""
commands.py - CLI command handlers
"""
import sys
from queries import (
    get_today_leads, get_stale_leads, get_lead_by_name,
    mark_stale_leads_followup_due, get_pipeline_summary,
    get_all_active_leads, get_closed_summary,
    update_quote, mark_won, mark_lost, add_lead
)
from drafting import draft_followup, summarize_lead, summarize_pipeline


def fmt_lead(lead):
    status_emoji = {
        "new": "🆕",
        "quoted": "💬",
        "followup_due": "🔔",
        "won": "✅",
        "lost": "❌",
    }.get(lead["status"], "❓")

    lines = [
        f"{status_emoji} {lead['name']} — {lead['service'] or 'N/A'}",
        f"   Status: {lead['status']}",
    ]
    if lead["quote_amount"]:
        lines.append(f"   Quote:  ${lead['quote_amount']:.0f}")
    if lead["phone"]:
        lines.append(f"   Phone:  {lead['phone']}")
    if lead["follow_up_after"]:
        lines.append(f"   Follow up: {lead['follow_up_after'][:10]}")
    if lead["notes"]:
        lines.append(f"   Notes:  {lead['notes']}")
    return "\n".join(lines)


def cmd_today():
    leads = get_today_leads()
    if not leads:
        print("No leads for today.")
        return
    print(f"=== Today's Leads ({len(leads)}) ===\n")
    for lead in leads:
        print(fmt_lead(lead))
        print()


def cmd_stale():
    leads = get_stale_leads()
    if not leads:
        print("No stale leads.")
        return
    print(f"=== Stale Leads ({len(leads)}) ===\n")
    for lead in leads:
        print(fmt_lead(lead))
        print()


def cmd_lead(name):
    if not name:
        print("Usage: python commands.py lead <name>")
        return
    lead = get_lead_by_name(name)
    if not lead:
        print(f"No lead found matching '{name}'.")
        return
    print(fmt_lead(lead))


def cmd_draft_followup(name):
    if not name:
        print("Usage: python commands.py draft-followup <name>")
        return
    lead = get_lead_by_name(name)
    if not lead:
        print(f"No lead found matching '{name}'.")
        return
    print(f"Drafting follow-up for {lead['name']}...\n")
    draft = draft_followup(dict(lead))
    print("--- Draft ---")
    print(draft)


def cmd_add():
    """Interactive prompt to add a new lead."""
    print("=== Add New Lead ===")
    name = input("Name: ").strip()
    if not name:
        print("Name is required.")
        return
    service = input("Service requested: ").strip()
    phone = input("Phone (optional): ").strip() or None
    email = input("Email (optional): ").strip() or None
    notes = input("Notes (optional): ").strip() or None
    days_str = input("Follow up in how many days? [3]: ").strip()
    followup_days = int(days_str) if days_str.isdigit() else 3

    lead_id = add_lead(name, service, phone=phone, email=email, notes=notes, followup_days=followup_days)
    print(f"\n✅ Lead added (id={lead_id}) — follow-up scheduled in {followup_days} day(s).")


def cmd_summarize(name):
    """AI summary of a single lead."""
    if not name:
        print("Usage: python commands.py summarize <name>")
        return
    lead = get_lead_by_name(name)
    if not lead:
        print(f"No lead found matching '{name}'.")
        return
    print(fmt_lead(lead))
    print("\n--- AI Summary ---")
    print(summarize_lead(dict(lead)))


def cmd_pipeline():
    """AI narrative of full pipeline health."""
    leads = [dict(r) for r in get_all_active_leads()]
    summary, totals = get_pipeline_summary()
    closed, loss_reasons = get_closed_summary()

    status_labels = {
        "new": "🆕 New",
        "quoted": "💬 Quoted",
        "followup_due": "🔔 Follow-up Due",
        "won": "✅ Won",
        "lost": "❌ Lost",
    }
    print("=== Pipeline Summary ===")
    for row in summary:
        label = status_labels.get(row["status"], row["status"])
        val = f"  (${row['total_quoted']:,.0f})" if row["total_quoted"] else ""
        print(f"  {label}: {row['count']}{val}")

    print(f"\n  Open pipeline:  ${totals['open_value']:,.0f}")
    print(f"  Won (closed):   ${totals['won_value']:,.0f}")
    print(f"  Lost (closed):  ${totals['lost_value']:,.0f}")

    if loss_reasons:
        print("\n=== Loss Reasons ===")
        for row in loss_reasons:
            print(f"  {row['lost_reason']}: {row['count']}")

    print("\n--- AI Analysis ---")
    print(summarize_pipeline(leads, list(summary)))


def cmd_quote(name, amount_str):
    """Set or update quote amount for a lead."""
    try:
        amount = float(amount_str.replace("$", "").replace(",", ""))
    except ValueError:
        print(f"Invalid amount: {amount_str}")
        return
    lead = get_lead_by_name(name)
    if not lead:
        print(f"No lead found matching '{name}'.")
        return
    update_quote(lead["id"], amount)
    print(f"✅ Updated {lead['name']} — quote set to ${amount:,.0f}, status → quoted")


def cmd_won(name):
    """Mark a lead as won."""
    lead = get_lead_by_name(name)
    if not lead:
        print(f"No lead found matching '{name}'.")
        return
    mark_won(lead["id"])
    print(f"✅ {lead['name']} marked as WON 🎉")


def cmd_lost(name, reason):
    """Mark a lead as lost with a reason."""
    valid = {"price", "timing", "went_competitor", "no_response", "not_qualified", "service_area", "other"}
    if reason not in valid:
        print(f"Invalid reason '{reason}'. Choose from: {', '.join(sorted(valid))}")
        return
    lead = get_lead_by_name(name)
    if not lead:
        print(f"No lead found matching '{name}'.")
        return
    mark_lost(lead["id"], reason)
    print(f"❌ {lead['name']} marked as LOST — reason: {reason}")


def cmd_digest():
    """Owner digest: promote stale leads, then summarize the pipeline."""
    promoted = mark_stale_leads_followup_due()
    if promoted:
        print(f"⚡ Auto-promoted {promoted} lead(s) to followup_due\n")

    summary, totals = get_pipeline_summary()

    status_labels = {
        "new": "🆕 New",
        "quoted": "💬 Quoted",
        "followup_due": "🔔 Follow-up Due",
        "won": "✅ Won",
        "lost": "❌ Lost",
    }

    print("=== Pipeline Digest ===")
    total_leads = 0
    for row in summary:
        label = status_labels.get(row["status"], row["status"])
        val = f"  (${row['total_quoted']:,.0f} quoted)" if row["total_quoted"] else ""
        print(f"  {label}: {row['count']}{val}")
        total_leads += row["count"]

    print(f"\n  Open pipeline:  ${totals['open_value']:,.0f}")
    print(f"  Won (closed):   ${totals['won_value']:,.0f}")
    print(f"  Lost (closed):  ${totals['lost_value']:,.0f}")
    print(f"  Total leads:    {total_leads}")

    # Surface stale leads needing action
    stale = get_stale_leads()
    if stale:
        print(f"\n=== Needs Action ({len(stale)}) ===")
        for lead in stale[:5]:
            print(f"  🔔 {lead['name']} — {lead['service'] or 'N/A'} (due {lead['follow_up_after'][:10]})")
        if len(stale) > 5:
            print(f"  ... and {len(stale) - 5} more")


def main():
    _run(sys.argv[1:])


def _run(args):
    if not args:
        print("Commands: add | today | stale | lead <name> | draft-followup <name> | summarize <name> | digest | pipeline | quote <name> <amount> | won <name> | lost <name> <reason>")
        return

    cmd = args[0]

    if cmd == "add":
        cmd_add()
    elif cmd == "today":
        cmd_today()
    elif cmd == "stale":
        cmd_stale()
    elif cmd == "lead":
        cmd_lead(" ".join(args[1:]))
    elif cmd == "draft-followup":
        cmd_draft_followup(" ".join(args[1:]))
    elif cmd == "summarize":
        cmd_summarize(" ".join(args[1:]))
    elif cmd == "digest":
        cmd_digest()
    elif cmd == "pipeline":
        cmd_pipeline()
    elif cmd == "quote":
        # quote <name> <amount>
        if len(args) < 3:
            print("Usage: python commands.py quote <name> <amount>")
            sys.exit(1)
        cmd_quote(" ".join(args[1:-1]), args[-1])
    elif cmd == "won":
        cmd_won(" ".join(args[1:]))
    elif cmd == "lost":
        # lost <name> <reason>
        if len(args) < 3:
            print("Usage: python commands.py lost <name> <reason>")
            print("Reasons: price | timing | went_competitor | no_response | not_qualified | service_area | other")
            sys.exit(1)
        cmd_lost(" ".join(args[1:-1]), args[-1])
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
