# LeadClaw — Pilot User Guide

Hey, thanks for trying LeadClaw early. This is real software built for real local businesses. Your feedback will directly shape what gets built next.

---

## Setup (5 minutes)

### 1. Requirements
- Python 3.9+
- An Anthropic API key (for AI follow-up drafts) — get one free at [console.anthropic.com](https://console.anthropic.com)

### 2. Install

```bash
git clone https://github.com/morgan-coded/leadclaw.git
cd leadclaw
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here
```

### 3. Initialize & seed demo data

```bash
python3 seed.py
```

This creates the database with 10 example leads so you can try everything immediately.

---

## Daily workflow

### Morning — see what needs attention
```bash
leadclaw digest
```
Shows your pipeline snapshot and promotes any overdue leads automatically.

### See today's leads
```bash
leadclaw today
```

### See what's gone stale
```bash
leadclaw stale
```

---

## Working a lead

### Look up a lead
```bash
leadclaw lead "Mike"
```

### Draft a follow-up text
```bash
leadclaw draft-followup "Mike"
```
Copy and paste it into your texts. Edit as needed — it's a starting point.

### Update a quote
```bash
leadclaw quote "Mike" 850
```

### Mark won
```bash
leadclaw won "Mike"
```

### Mark lost
```bash
leadclaw lost "Mike" price
# Reasons: price | timing | went_competitor | no_response | not_qualified | service_area | other
```

---

## Full pipeline view
```bash
leadclaw pipeline
```
Gives you a full breakdown + AI analysis of where your pipeline stands.

---

## Adding real leads

Right now, leads are added directly to the database. In a future version, this will be a simple form or CSV import.

To add a lead manually (temporary):

```python
# run python3, then:
from db import get_conn
conn = get_conn()
conn.execute("""
  INSERT INTO leads (name, phone, service, status, created_at, follow_up_after)
  VALUES ('John Smith', '555-999-0001', 'lawn care', 'new', datetime('now'), datetime('now', '+3 days'))
""")
conn.commit()
```

---

## Feedback

What would make this actually useful for your day-to-day?

- Email: [your contact]
- Text: [your number]

Things I want to know:
1. What's missing from the first day of using it?
2. Which command do you find yourself reaching for most?
3. What would make you pay for this?
