"""
drafting.py - Follow-up message drafting via Claude API
"""
import os
import anthropic


def draft_followup(lead: dict) -> str:
    """
    Generate a follow-up message for a lead using Claude.
    lead should be a dict (or sqlite3.Row converted to dict).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY=your_key"
        )
    client = anthropic.Anthropic(api_key=api_key)

    name = lead["name"]
    service = lead["service"] or "your service request"
    status = lead["status"]
    quote = f"${lead['quote_amount']:.0f}" if lead["quote_amount"] else "TBD"
    notes = lead["notes"] or ""

    prompt = f"""
You are writing a short, professional follow-up text message on behalf of a local service business owner.

Lead info:
- Name: {name}
- Service requested: {service}
- Current status: {status}
- Quote amount: {quote}
- Notes: {notes}

Write a friendly, concise follow-up message (2-4 sentences). 
Do not be pushy. Be natural, like a real small business owner texting.
Do not include subject lines or signatures — just the message body.
""".strip()

    message = client.messages.create(
        model="claude-3-5-haiku-20241022",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()
