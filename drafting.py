"""
drafting.py - Claude-powered drafts and summaries
"""
import os
from datetime import datetime
import anthropic


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY=your_key"
        )
    return anthropic.Anthropic(api_key=api_key)


def _days_overdue(lead):
    """Return how many days past follow_up_after, or None."""
    if not lead.get("follow_up_after"):
        return None
    try:
        due = datetime.strptime(lead["follow_up_after"][:19], "%Y-%m-%d %H:%M:%S")
        delta = (datetime.now() - due).days
        return delta if delta > 0 else None
    except ValueError:
        return None


def draft_followup(lead: dict) -> str:
    """
    Generate a follow-up text message for a lead.
    Prompt is context-aware: uses status, days overdue, quote amount.
    """
    name = lead["name"]
    service = lead["service"] or "your service request"
    status = lead["status"]
    quote = f"${lead['quote_amount']:.0f}" if lead.get("quote_amount") else None
    notes = lead.get("notes") or ""
    overdue = _days_overdue(lead)

    context_lines = [f"- Name: {name}", f"- Service: {service}", f"- Status: {status}"]
    if quote:
        context_lines.append(f"- Quote sent: {quote}")
    if overdue:
        context_lines.append(f"- Days since follow-up was due: {overdue}")
    if notes:
        context_lines.append(f"- Notes: {notes}")

    tone_hint = ""
    if status == "new":
        tone_hint = "This is the first follow-up. Be warm and curious — ask if they're still interested."
    elif status == "quoted":
        tone_hint = "You've already sent a quote. Follow up gently — ask if they have questions or want to move forward."
    elif status == "followup_due":
        if overdue and overdue > 7:
            tone_hint = "It's been a while. Be brief, low-pressure — give them an easy out if they've moved on."
        else:
            tone_hint = "Check in naturally. Don't sound desperate. Short and friendly."

    prompt = f"""
You are writing a short, professional follow-up text message for a local service business owner.

Lead context:
{chr(10).join(context_lines)}

Tone guidance: {tone_hint}

Rules:
- 2-4 sentences max
- Sound like a real person, not a template
- No subject lines, no sign-offs, no placeholders like [Name]
- Just the message body
""".strip()

    msg = _client().messages.create(
        model="claude-3-5-haiku-20241022",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def summarize_lead(lead: dict) -> str:
    """
    Generate a narrative summary of a single lead's situation.
    """
    fields = [
        f"Name: {lead['name']}",
        f"Service: {lead.get('service') or 'N/A'}",
        f"Status: {lead['status']}",
        f"Quote: ${lead['quote_amount']:.0f}" if lead.get("quote_amount") else "Quote: none",
        f"Created: {str(lead.get('created_at', ''))[:10]}",
        f"Last contact: {str(lead.get('last_contact_at', ''))[:10]}",
        f"Follow-up due: {str(lead.get('follow_up_after', ''))[:10]}",
        f"Notes: {lead.get('notes') or 'none'}",
    ]
    overdue = _days_overdue(lead)
    if overdue:
        fields.append(f"Days overdue: {overdue}")

    prompt = f"""
Summarize this lead for a small business owner in 2-3 sentences.
Be concrete and actionable — what's the situation and what should they do next?

{chr(10).join(fields)}
""".strip()

    msg = _client().messages.create(
        model="claude-3-5-haiku-20241022",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def summarize_pipeline(leads: list, summary_rows: list) -> str:
    """
    Generate an AI narrative of the overall pipeline state.
    leads: list of active lead dicts
    summary_rows: from get_pipeline_summary() rows (not the totals tuple)
    """
    status_counts = {row["status"]: row["count"] for row in summary_rows}
    open_statuses = {"new", "quoted", "followup_due"}
    total_value = sum(
        row["total_quoted"] for row in summary_rows if row["status"] in open_statuses
    )

    stale = [l for l in leads if l["status"] == "followup_due"]
    high_value = sorted(
        [l for l in leads if l.get("quote_amount")],
        key=lambda x: x["quote_amount"],
        reverse=True,
    )[:3]

    context = f"""
Pipeline snapshot:
- New leads: {status_counts.get('new', 0)}
- Quoted: {status_counts.get('quoted', 0)}
- Follow-up due: {status_counts.get('followup_due', 0)}
- Won: {status_counts.get('won', 0)}
- Lost: {status_counts.get('lost', 0)}
- Total pipeline value: ${total_value:,.0f}
- Stale leads needing action: {len(stale)}

Top opportunities by value:
{chr(10).join(f"  - {l['name']}: ${l['quote_amount']:.0f} ({l['service']})" for l in high_value) or '  none'}
""".strip()

    prompt = f"""
You are advising a local service business owner on their sales pipeline.

{context}

Write a 3-5 sentence narrative summary: what's the overall health of the pipeline, 
what should they prioritize today, and any patterns worth noting?
Be direct and practical — no fluff.
""".strip()

    msg = _client().messages.create(
        model="claude-3-5-haiku-20241022",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()
