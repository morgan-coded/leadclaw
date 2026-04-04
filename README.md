# LeadClaw

**Stop losing jobs to forgotten follow-ups.**

LeadClaw is a lightweight lead tracking CLI for local service businesses. It tells you who to call today, drafts your follow-up texts, and keeps your pipeline visible — without a CRM subscription.

---

## Quickstart

```bash
git clone https://github.com/morgan-coded/leadclaw.git
cd leadclaw
pip install .
cp .env.example .env   # add your ANTHROPIC_API_KEY
leadclaw-seed          # init DB + load demo data
leadclaw digest        # first look at your pipeline
```

> AI features (`draft-followup`, `summarize`, `pipeline`) require an Anthropic API key.
> Get one free at [console.anthropic.com](https://console.anthropic.com).

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

## Package Layout

| Path | Purpose |
|---|---|
| `leadclaw/commands.py` | CLI entry point (`leadclaw` command) |
| `leadclaw/db.py` | SQLite connection + schema init |
| `leadclaw/queries.py` | All SQL query functions |
| `leadclaw/drafting.py` | Claude-powered follow-up drafts + summaries |
| `leadclaw/seed.py` | Demo data seeder (`leadclaw-seed` command) |
| `leadclaw/scheduler.py` | Daily digest runner (`leadclaw-scheduler` command) |
| `leadclaw/config.py` | Shared constants (status labels, lost reasons, limits) |
| `pyproject.toml` | Package definition + entry points |
| `tests/` | pytest suite |

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
- [x] OpenClaw integration — `digest`, `lead`, `draft-followup`, and more as chat commands
- [ ] Week 5+ — CSV import, web UI, SMS integration
