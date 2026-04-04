"""
scheduler.py - Daily digest job (cron-ready)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from queries import mark_stale_leads_followup_due, get_stale_leads, get_pipeline_summary
from commands import print_pipeline_summary
from config import STATUS_LABELS


def run_daily_digest():
    print("=== LeadClaw Daily Digest ===\n")

    promoted = mark_stale_leads_followup_due()
    if promoted:
        print(f"⚡ Promoted {promoted} lead(s) to followup_due\n")

    summary, totals = get_pipeline_summary()
    print_pipeline_summary(summary, totals)

    stale = get_stale_leads()
    if stale:
        print(f"\n=== Top Stale Leads ===")
        for lead in stale[:5]:
            print(f"  🔔 {lead['name']} — {lead['service'] or 'N/A'} (overdue since {str(lead['follow_up_after'])[:10]})")
        if len(stale) > 5:
            print(f"  ... and {len(stale) - 5} more")
    else:
        print("\n✅ No stale leads.")


if __name__ == "__main__":
    run_daily_digest()
