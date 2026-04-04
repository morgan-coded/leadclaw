# LeadClaw — Pilot User Guide

Thanks for trying LeadClaw early. Your feedback will directly shape what gets built next.

---

## Setup (5 minutes)

### 1. Requirements
- Python 3.9+
- An Anthropic API key for AI features — get one free at [console.anthropic.com](https://console.anthropic.com)

### 2. Install

```bash
git clone https://github.com/morgan-coded/leadclaw.git
cd leadclaw
pip install .
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### 4. Initialize with demo data

```bash
leadclaw-seed
```

---

## Daily workflow

### Morning — see what needs attention
```bash
leadclaw digest
```

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

### Add a real lead
```bash
leadclaw add
```

### Look up a lead
```bash
leadclaw lead "Mike"
leadclaw lead --id 7
```

### Draft a follow-up text
```bash
leadclaw draft-followup "Mike"
```

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

## Other commands

```bash
leadclaw list              # all active leads
leadclaw list --all        # everything including won/lost
leadclaw edit "Mike"       # update phone, email, notes, follow-up date
leadclaw delete "Mike"     # remove a lead
leadclaw summarize "Mike"  # AI summary of a lead
leadclaw pipeline          # full AI pipeline analysis
leadclaw export            # export to CSV
```

Plain text mode (no emoji):
```bash
leadclaw --plain digest
```

---

## Feedback

What would make this actually useful for your day-to-day?

- Which command do you use most?
- What's missing from day 1?
- What would make you pay for this?
