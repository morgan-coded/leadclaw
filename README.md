# LeadClaw

A lightweight lead tracking CLI for local service businesses.

## MVP Commands

```bash
python commands.py today               # leads due today
python commands.py stale               # overdue follow-ups
python commands.py lead "Mike Tran"    # look up a lead
python commands.py draft-followup "Priya"  # draft a follow-up text
```

## Setup

```bash
pip install anthropic
export ANTHROPIC_API_KEY=your_key_here

# Initialize DB and seed demo data
python seed.py
```

## Module Layout

| File | Purpose |
|---|---|
| `db.py` | DB connection, schema init |
| `seed.py` | Demo data seeder |
| `queries.py` | SQL query functions |
| `drafting.py` | Claude-powered follow-up drafts |
| `commands.py` | CLI entry point |
| `scheduler.py` | (Week 2+) Digest & cron jobs |

## Schema

Leads have structured `lost_reason` values:
`price` · `timing` · `went_competitor` · `no_response` · `not_qualified` · `service_area` · `other`

## Roadmap

- **Week 1** — schema, seed, core queries, CLI wired
- **Week 2** — stale logic, follow-up drafts, owner digest
- **Week 3** — summaries, quote tracking, better prompts
- **Week 4** — packaging, landing page, pilot user
