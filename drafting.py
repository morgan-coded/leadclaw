"""
drafting.py - Claude-powered drafts and summaries
"""
import os
from datetime import datetime
import anthropic
from config import MODEL

# Singleton client — created once, reused across calls
from typing import Optional
_client: "Optional[anthropic.Anthropic]" = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY=your_key\n"
                "Or add it to a .env file in the project root."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _call(prompt: str, max_tokens: int = 300) -> str:
    """Make an API call with unified error handling."""
    try:
        msg = get_client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except anthropic.AuthenticationError:
        raise SystemExit("❌ Invalid Anthropic API key. Check your ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise SystemExit("❌ Anthropic rate limit hit. Wait a moment and try again.")
    except anthropic.APIConnectionError:
        raise SystemExit("❌ Could not reach Anthropic API. Check your internet connection.")
    except anthropic.APIError as e:
        raise SystemExit(f"❌ Anthropic API error: {e}")


def _days_overdue(lead: dict):
    if not lead.get("follow_up_after"):
        return None
    try:
        due = datetime.strptime(str(lead["follow_up_after"])[:19], "%Y-%m-%d %H:%M:%S")
        delta = (datetime.now() - due).days
        return delta if delta > 0 else None
    except ValueError:
        return None


def draft_followup(lead: dict) -> str:
    name = lead["name"]
    service = lead.get("service") or "your service request"
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

    if status == "new":
        tone = "First follow-up. Be warm and curious — ask if they're still interested."
    elif status == "quoted":
        tone = "Quote already sent. Follow up gently — ask if they have questions or want to move forward."
    elif overdue and overdue > 7:
        tone = "It's been a while. Be brief, low-pressure — give them an easy out if they've moved on."
    else:
        tone = "Check in naturally. Short and friendly."

    prompt = f"""
You are writing a short follow-up text message for a local service business owner.

Lead context:
{chr(10).join(context_lines)}

Tone: {tone}

Rules: 2-4 sentences. Sound like a real person. No subject lines, sign-offs, or placeholders.
""".strip()

    return _call(prompt, max_tokens=256)


def summarize_lead(lead: dict) -> str:
    overdue = _days_overdue(lead)
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
    if overdue:
        fields.append(f"Days overdue: {overdue}")

    prompt = f"""
Summarize this lead for a small business owner in 2-3 sentences.
Be concrete and actionable — what's the situation and what should they do next?

{chr(10).join(fields)}
""".strip()

    return _call(prompt, max_tokens=200)


def summarize_pipeline(leads: list, summary_rows: list) -> str:
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
- New: {status_counts.get('new', 0)}
- Quoted: {status_counts.get('quoted', 0)}
- Follow-up due: {status_counts.get('followup_due', 0)}
- Won: {status_counts.get('won', 0)}
- Lost: {status_counts.get('lost', 0)}
- Open pipeline value: ${total_value:,.0f}
- Stale leads: {len(stale)}

Top opportunities:
{chr(10).join(f"  - {l['name']}: ${l['quote_amount']:.0f} ({l['service']})" for l in high_value) or '  none'}
""".strip()

    prompt = f"""
You are advising a local service business owner on their sales pipeline.

{context}

Write 3-5 sentences: overall health, what to prioritize today, any patterns worth noting.
Be direct and practical — no fluff.
""".strip()

    return _call(prompt, max_tokens=300)
