"""
scheduler.py - Scheduled jobs (run via cron or manually)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from queries import mark_stale_leads_followup_due, get_stale_leads, get_pipeline_summary


def run_daily_digest():
    """
    Daily digest job:
    1. Auto-promote overdue leads to followup_due
    2. Print pipeline summary
    3. Print top stale leads needing action

    Run this once a day (e.g., 8am via cron).
    """
    print("=== LeadClaw Daily Digest ===\n")

    promoted = mark_stale_leads_followup_due()
    if promoted:
        print(f"⚡ Promoted {promoted} lead(s) to followup_due\n")

    summary = get_pipeline_summary()
    status_labels = {
        "new": "🆕 New",
        "quoted": "💬 Quoted",
        "followup_due": "🔔 Follow-up Due",
        "won": "✅ Won",
        "lost": "❌ Lost",
    }

    total_leads = 0
    total_value = 0.0
    for row in summary:
        label = status_labels.get(row["status"], row["status"])
        val = f"  (${row['total_quoted']:,.0f} quoted)" if row["total_quoted"] else ""
        print(f"  {label}: {row['count']}{val}")
        total_leads += row["count"]
        total_value += row["total_quoted"]

    print(f"\n  Total: {total_leads} leads | ${total_value:,.0f} in pipeline")

    stale = get_stale_leads()
    if stale:
        print(f"\n=== Top Stale Leads ===")
        for lead in stale[:5]:
            print(f"  🔔 {lead['name']} — {lead['service'] or 'N/A'} (overdue since {lead['follow_up_after'][:10]})")
        if len(stale) > 5:
            print(f"  ... and {len(stale) - 5} more")
    else:
        print("\n✅ No stale leads — you're on top of it.")


if __name__ == "__main__":
    run_daily_digest()
