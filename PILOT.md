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
python3 commands.py digest
```
Shows your pipeline snapshot and promotes any overdue leads automatically.

### See today's leads
```bash
python3 commands.py today
```

### See what's gone stale
```bash
python3 commands.py stale
```

---

## Working a lead

### Add a real lead
```bash
python3 commands.py add
```
Walks you through name, service, phone, notes, and follow-up timing.

### Look up a lead
```bash
python3 commands.py lead "Mike"
```

### Draft a follow-up text
```bash
python3 commands.py draft-followup "Mike"
```
Copy and paste it into your texts. Edit as needed — it's a starting point.

### Update a quote
```bash
python3 commands.py quote "Mike" 850
```
Automatically sets last contact time and schedules a follow-up in 3 days.

### Mark won
```bash
python3 commands.py won "Mike"
```

### Mark lost
```bash
python3 commands.py lost "Mike" price
# Reasons: price | timing | went_competitor | no_response | not_qualified | service_area | other
```

---

## Full pipeline view
```bash
python3 commands.py pipeline
```
Gives you a full breakdown + AI analysis of where your pipeline stands.

---

---

## Feedback

What would make this actually useful for your day-to-day?

- Email: [your contact]
- Text: [your number]

Things I want to know:
1. What's missing from the first day of using it?
2. Which command do you find yourself reaching for most?
3. What would make you pay for this?
