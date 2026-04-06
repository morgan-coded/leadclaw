"""
drafting.py - Claude-powered drafts and summaries
"""

import os
from datetime import datetime
from typing import Optional

import anthropic

from leadclaw.config import MODEL

# Singleton client — created once, reused across calls
_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise OSError(
                "ANTHROPIC_API_KEY not set.\n"
                "Copy .env.example to .env and add your key, or run:\n"
                "  export ANTHROPIC_API_KEY=your_key_here"
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def check_api_key() -> bool:
    """Return True if API key is present, False otherwise (no exception)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _call(prompt: str, max_tokens: int = 300) -> Optional[str]:
    """Make an API call. Returns None on error (caller decides what to do)."""
    try:
        msg = get_client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except OSError as e:
        print(f"Error: {e}")
        return None
    except anthropic.AuthenticationError:
        print("Invalid Anthropic API key. Check your ANTHROPIC_API_KEY.")
        return None
    except anthropic.RateLimitError:
        print("Anthropic rate limit hit. Wait a moment and try again.")
        return None
    except anthropic.APIConnectionError:
        print("Could not reach Anthropic API. Check your internet connection.")
        return None
    except anthropic.APIError as e:
        print(f"Anthropic API error: {e}")
        return None


def _days_overdue(lead: dict) -> Optional[int]:
    raw = lead.get("follow_up_after")
    if not raw:
        return None
    try:
        due = datetime.fromisoformat(str(raw).replace(" ", "T").split(".")[0])
        delta = (datetime.now() - due).days
        return delta if delta > 0 else None
    except (ValueError, TypeError):
        return None


def draft_followup(lead: dict) -> Optional[str]:
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
        tone = (
            "It's been a while. Be brief, low-pressure — give them an easy out if they've moved on."
        )
    else:
        tone = "Check in naturally. Short and friendly."

    prompt = (
        "You are writing a short follow-up text message for a local service business owner.\n\n"
        f"Lead context:\n{chr(10).join(context_lines)}\n\n"
        f"Tone: {tone}\n\n"
        "Rules: 2-4 sentences. Sound like a real person. No subject lines, sign-offs, or placeholders."
    )
    return _call(prompt, max_tokens=256)


def summarize_lead(lead: dict) -> Optional[str]:
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

    prompt = (
        "Summarize this lead for a small business owner in 2-3 sentences.\n"
        "Be concrete and actionable — what's the situation and what should they do next?\n\n"
        + "\n".join(fields)
    )
    return _call(prompt, max_tokens=200)


def draft_pilot_outreach(candidate: dict) -> Optional[str]:
    """
    Draft a personalized outreach text message for a pilot candidate.
    Keeps it human, specific, and low-pressure.
    """
    name = candidate.get("name") or "there"
    business = candidate.get("business_name") or name
    service = candidate.get("service_type") or "your business"
    location = candidate.get("location") or ""
    notes = candidate.get("notes") or ""

    context = [
        f"Name: {name}",
        f"Business: {business}",
        f"Service type: {service}",
    ]
    if location:
        context.append(f"Location: {location}")
    if notes:
        context.append(f"Notes: {notes}")

    prompt = (
        "You are writing a short, personal outreach text message on behalf of a software founder."
        " They are looking for local service business owners to try a free lead-tracking tool called LeadClaw."
        " They want honest pilot feedback, not a sale.\n\n"
        f"Candidate context:\n{chr(10).join(context)}\n\n"
        "Write 2-4 sentences. Be direct, genuine, and specific to their business type."
        " No emojis. No fluff. Sound like a real person reaching out, not a template."
        " Do not mention pricing. End with a simple yes/no question."
    )
    return _call(prompt, max_tokens=256)


def summarize_pilot_reply(candidate: dict, reply_text: str) -> Optional[str]:
    """
    Summarize a pilot candidate's reply and suggest a next action.
    """
    name = candidate.get("name") or "the candidate"
    prompt = (
        f"A pilot outreach message was sent to {name}, a local {candidate.get('service_type') or 'service'} business owner."
        " Here is their reply:\n\n"
        f"{reply_text}\n\n"
        "In 2-3 sentences: what is their sentiment (interested / neutral / not interested)?"
        " What is the recommended next action?"
    )
    return _call(prompt, max_tokens=200)


# ---------------------------------------------------------------------------
# Message templates (no AI — pure string formatting, always deterministic)
# ---------------------------------------------------------------------------

MSG_TYPES = [
    "quote_followup",
    "booking_confirmation",
    "on_my_way",
    "running_late",
    "review_request",
    "reactivation_30",
    "reactivation_60",
    "reactivation_90",
]


def draft_message(lead: dict, msg_type: str) -> str:
    """
    Generate a short, copy-ready message for a lead based on msg_type.
    Uses string templates only — no AI, no API calls, always deterministic.
    Returns the message string (never None).
    """
    name = (lead.get("name") or "there").split()[0]  # first name only
    service = lead.get("service") or "the job"
    raw_date = lead.get("scheduled_date") or ""
    scheduled = str(raw_date)[:10] if raw_date else "your scheduled date"
    quote_amt = lead.get("quote_amount")
    quote = f"${quote_amt:,.0f}" if quote_amt else ""

    # Scheduled time window label for booking confirmation
    _tw_labels = {
        "morning": "morning (8am–12pm)",
        "afternoon": "afternoon (12pm–5pm)",
        "evening": "evening (5pm–8pm)",
        "flexible": None,  # don't include in message
    }
    raw_tw = lead.get("scheduled_time_window") or ""
    tw_label = _tw_labels.get(raw_tw)

    _booking_time = f" in the {tw_label}" if tw_label else ""

    templates = {
        "quote_followup": (
            f"Hey {name}, just wanted to follow up on the quote"
            + (f" for {service}" if service != "the job" else "")
            + (f" ({quote})" if quote else "")
            + ". Do you have any questions or want to get scheduled?"
        ),
        "booking_confirmation": (
            f"Hey {name}, confirming your {service} appointment"
            + (f" on {scheduled}{_booking_time}" if scheduled != "your scheduled date" else "")
            + ". Let me know if you need to reschedule. Looking forward to it!"
        ),
        "on_my_way": (f"Hey {name}, I'm on my way for {service} today." + " See you soon!"),
        "running_late": (
            f"Hey {name}, heads up — I'm running about 15 minutes behind for {service} today."
            + " I'll be there shortly, thanks for your patience!"
        ),
        "review_request": (
            f"Hey {name}, thanks for choosing us for {service}!"
            + " If you were happy with the work, a quick Google review would mean a lot."
            + " Here's the link: [YOUR REVIEW LINK]"
        ),
        "reactivation_30": (
            f"Hey {name}, just checking in — still interested in {service}?"
            + " Happy to get you scheduled if the timing works."
        ),
        "reactivation_60": (
            f"Hey {name}, it's been a little while — wanted to see if you're still thinking about {service}."
            + " No pressure, just here if you need us."
        ),
        "reactivation_90": (
            f"Hey {name}, hope all is well! Reaching out one more time about {service}."
            + " If you've found someone else, totally understand — just let me know either way."
        ),
    }

    if msg_type not in templates:
        return f"Unknown message type: {msg_type}. Valid types: {', '.join(MSG_TYPES)}"

    return templates[msg_type]


# ---------------------------------------------------------------------------
# AI pipeline analysis
# ---------------------------------------------------------------------------


def summarize_pipeline(leads: list, summary_rows: list) -> Optional[str]:
    status_counts = {row["status"]: row["count"] for row in summary_rows}
    open_statuses = {"new", "quoted", "followup_due"}
    total_value = sum(row["total_quoted"] for row in summary_rows if row["status"] in open_statuses)
    stale = [lead for lead in leads if lead["status"] == "followup_due"]
    high_value = sorted(
        [lead for lead in leads if lead.get("quote_amount")],
        key=lambda x: x["quote_amount"],
        reverse=True,
    )[:3]

    hv_lines = (
        "\n".join(
            f"  - {lead['name']}: ${lead['quote_amount']:.0f} ({lead['service']})"
            for lead in high_value
        )
        or "  none"
    )

    context = (
        "Pipeline snapshot:\n"
        f"- New: {status_counts.get('new', 0)}\n"
        f"- Quoted: {status_counts.get('quoted', 0)}\n"
        f"- Follow-up due: {status_counts.get('followup_due', 0)}\n"
        f"- Won: {status_counts.get('won', 0)}\n"
        f"- Lost: {status_counts.get('lost', 0)}\n"
        f"- Open pipeline value: ${total_value:,.0f}\n"
        f"- Stale leads: {len(stale)}\n\n"
        f"Top opportunities:\n{hv_lines}"
    )

    prompt = (
        "You are advising a local service business owner on their sales pipeline.\n\n"
        f"{context}\n\n"
        "Write 3-5 sentences: overall health, what to prioritize today, any patterns worth noting.\n"
        "Be direct and practical — no fluff."
    )
    return _call(prompt, max_tokens=300)
