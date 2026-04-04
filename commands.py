"""
commands.py - CLI command handlers
"""
import sys
from queries import get_today_leads, get_stale_leads, get_lead_by_name
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


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Commands: today | stale | lead <name> | draft-followup <name>")
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
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
