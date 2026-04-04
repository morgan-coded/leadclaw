"""
scheduler.py - Daily digest job (cron-ready, no side effects beyond print)
Run once per day: python3 scheduler.py
or via cron: 0 8 * * * cd /path/to/leadclaw && python3 scheduler.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from commands import print_pipeline_summary
from queries import get_pipeline_summary, get_stale_leads, mark_stale_leads_followup_due


def run_daily_digest():
    print("=== LeadClaw Daily Digest ===\n")

    promoted = mark_stale_leads_followup_due()
    if promoted:
        print(f"Auto-promoted {promoted} lead(s) to followup_due\n")

    summary, totals = get_pipeline_summary()
    print_pipeline_summary(summary, totals)

    stale = get_stale_leads()
    if stale:
        print(f"\n=== Top Stale Leads ===")
        for lead in stale[:5]:
            print(f"  [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'} (overdue since {str(lead['follow_up_after'])[:10]})")
        if len(stale) > 5:
            print(f"  ... and {len(stale) - 5} more")
    else:
        print("\nNo stale leads.")


def main():
    run_daily_digest()


if __name__ == "__main__":
    main()
