"""
commands.py - CLI command handlers
"""
import sys
from queries import (
    get_today_leads, get_stale_leads, get_lead_by_name,
    mark_stale_leads_followup_due, get_pipeline_summary
)
from drafting import draft_followup


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


def cmd_digest():
    """Owner digest: promote stale leads, then summarize the pipeline."""
    promoted = mark_stale_leads_followup_due()
    if promoted:
        print(f"⚡ Auto-promoted {promoted} lead(s) to followup_due\n")

    summary = get_pipeline_summary()

    status_labels = {
        "new": "🆕 New",
        "quoted": "💬 Quoted",
        "followup_due": "🔔 Follow-up Due",
        "won": "✅ Won",
        "lost": "❌ Lost",
    }

    print("=== Pipeline Digest ===")
    total_leads = 0
    total_value = 0.0
    for row in summary:
        label = status_labels.get(row["status"], row["status"])
        val = f"  (${row['total_quoted']:,.0f} quoted)" if row["total_quoted"] else ""
        print(f"  {label}: {row['count']}{val}")
        total_leads += row["count"]
        total_value += row["total_quoted"]

    print(f"\n  Total: {total_leads} leads | ${total_value:,.0f} in pipeline")

    # Surface stale leads needing action
    stale = get_stale_leads()
    if stale:
        print(f"\n=== Needs Action ({len(stale)}) ===")
        for lead in stale[:5]:  # cap at 5 in digest
            print(f"  🔔 {lead['name']} — {lead['service'] or 'N/A'} (due {lead['follow_up_after'][:10]})")
        if len(stale) > 5:
            print(f"  ... and {len(stale) - 5} more")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Commands: today | stale | lead <name> | draft-followup <name> | digest")
        sys.exit(1)

    cmd = args[0]

    if cmd == "today":
        cmd_today()
    elif cmd == "stale":
        cmd_stale()
    elif cmd == "lead":
        cmd_lead(" ".join(args[1:]))
    elif cmd == "draft-followup":
        cmd_draft_followup(" ".join(args[1:]))
    elif cmd == "digest":
        cmd_digest()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
