# LeadClaw

**Stop losing jobs to forgotten follow-ups.**

LeadClaw is a lightweight lead tracking CLI for local service businesses. It tells you who to call today, drafts your follow-up texts, and keeps your pipeline visible — without a CRM subscription.

---

## Quickstart

```bash
git clone https://github.com/morgan-coded/leadclaw.git
cd leadclaw
pip install .
export ANTHROPIC_API_KEY=your_key_here
leadclaw-seed          # initialize DB + seed demo data
leadclaw digest
```

---

## Commands

| Command | What it does |
|---|---|
| `today` | Leads due today |
| `stale` | Overdue follow-ups |
| `list [--all]` | List active leads (or all with `--all`) |
| `lead <name\|--id>` | Look up a lead |
| `add` | Add a new lead (interactive) |
| `edit <name\|--id>` | Edit a lead (interactive) |
| `delete <name\|--id>` | Delete a lead |
| `quote <name> <amount>` | Set/update a quote |
| `won <name\|--id>` | Mark a lead won |
| `lost <name\|--id> <reason>` | Mark a lead lost with structured reason |
| `draft-followup <name\|--id>` | AI-drafted follow-up text |
| `summarize <name\|--id>` | AI narrative on a lead's situation |
| `digest` | Pipeline snapshot + auto-promote stale leads |
| `pipeline` | Full AI pipeline analysis |
| `export [--output file]` | Export all leads to CSV |

### Global flags

| Flag | Effect |
|---|---|
| `--plain` | Plain-text output — no emoji (great for scripts and SMS) |

---

## Module Layout

| File | Purpose |
|---|---|
| `db.py` | DB connection, schema init |
| `seed.py` | Demo data seeder |
| `queries.py` | SQL query functions |
| `drafting.py` | Claude-powered follow-up drafts + summaries |
| `commands.py` | CLI entry point |
| `scheduler.py` | Daily digest job (cron-ready) |
| `landing/` | Landing page HTML |

---

## Schema

Leads live in SQLite. Statuses: `new` → `quoted` → `followup_due` → `won` / `lost`

`lost_reason` is structured (not free text):
`price` · `timing` · `went_competitor` · `no_response` · `not_qualified` · `service_area` · `other`

---

## Pilot

See [PILOT.md](PILOT.md) for the pilot user onboarding guide.

---

## Roadmap

- [x] Week 1 — schema, seed, core queries, 4 CLI commands
- [x] Week 2 — stale auto-promotion, pipeline digest, owner summary
- [x] Week 3 — quote tracking, won/lost, AI lead summaries, pipeline analysis
- [x] Week 4 — landing page, pilot user guide
- [x] Packaging — real `leadclaw` console entry point (pip installable)
- [ ] OpenClaw integration — `/digest`, `/lead`, `/draft-followup` as chat commands
- [ ] Week 5+ — CSV import, web UI, SMS integration
