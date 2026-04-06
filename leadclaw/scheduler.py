"""
scheduler.py - Daily digest job (cron-ready, no side effects beyond print)
Run once per day: python3 scheduler.py
or via cron: 0 8 * * * cd /path/to/leadclaw && python3 scheduler.py
"""

from leadclaw.commands import print_pipeline_summary
from leadclaw.queries import (
    get_invoice_reminders,
    get_job_today_leads,
    get_pipeline_summary,
    get_reactivation_leads,
    get_review_reminders,
    get_service_reminders,
    get_stale_leads,
    mark_stale_leads_followup_due,
)


def run_daily_digest():
    print("=== LeadClaw Daily Digest ===\n")

    promoted = mark_stale_leads_followup_due()
    if promoted:
        print(f"Auto-promoted {promoted} lead(s) to followup_due\n")

    summary, totals = get_pipeline_summary()
    print_pipeline_summary(summary, totals)

    stale = get_stale_leads()
    if stale:
        print("\n=== Top Stale Leads ===")
        for lead in stale[:5]:
            print(
                f"  [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'} (overdue since {str(lead['follow_up_after'])[:10]})"
            )
        if len(stale) > 5:
            print(f"  ... and {len(stale) - 5} more")
    else:
        print("\nNo stale leads.")

    invoice_due = get_invoice_reminders()
    if invoice_due:
        print(f"\n=== Invoice Reminders ({len(invoice_due)}) ===")
        for lead in invoice_due:
            amt = (
                f"${lead['invoice_amount']:,.0f}"
                if lead["invoice_amount"]
                else f"${lead['quote_amount']:,.0f}"
                if lead["quote_amount"]
                else ""
            )
            print(f"  [{lead['id']}] {lead['name']} — {amt} — follow up on payment")

    service_due = get_service_reminders()
    if service_due:
        print(f"\n=== Recurring Service Due ({len(service_due)}) ===")
        for lead in service_due:
            print(
                f"  [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'} — due {str(lead['service_reminder_at'])[:10]}"
            )

    job_today = get_job_today_leads()
    if job_today:
        print(f"\n=== Jobs Today ({len(job_today)}) ===")
        for lead in job_today:
            print(
                f"  [{lead['id']}] {lead['name']} — scheduled {str(lead.get('scheduled_date') or '')[:10]}"
            )

    review_due = get_review_reminders()
    if review_due:
        print(f"\n=== Review Requests Due ({len(review_due)}) ===")
        for lead in review_due:
            print(f"  [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'}")

    for days in [30, 60, 90]:
        react = get_reactivation_leads(days)
        if react:
            label = f"{days}+ days" if days >= 90 else f"{days}–59 days"
            print(f"\n=== Reactivation — {label} ({len(react)}) ===")
            for lead in react:
                print(f"  [{lead['id']}] {lead['name']} — {lead['service'] or 'N/A'}")


def main():
    run_daily_digest()


if __name__ == "__main__":
    main()
