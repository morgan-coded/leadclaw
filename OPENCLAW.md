# LeadClaw — OpenClaw Integration

LeadClaw ships with an OpenClaw skill that lets you run pipeline commands from
any connected chat surface (iMessage, Signal, Telegram, etc.) without opening
a terminal.

---

## Setup

1. The skill file lives at `<openclaw-workspace>/skills/leadclaw/SKILL.md`.
2. It is picked up automatically on the next session — no restart needed.
3. Confirm it's active: `openclaw skills list` should show `leadclaw` as ✓ ready.

The skill calls `leadclaw --plain <cmd>` when available, falling back to
`python3 -m leadclaw.commands --plain <cmd>` if the binary isn't on PATH.
The `--plain` flag strips emoji for clean text output over messaging.

---

## Chat commands

Say any of these naturally — the skill matches on intent, not exact phrasing.

### `digest` — pipeline snapshot

> "digest" / "show my pipeline" / "what's due"

```
=== Pipeline Digest ===
  [followup_due]: 8  ($5,545)
  [new]: 2

  Open pipeline:  $5,545
  Won (closed):   $0
  Lost (closed):  $0

=== Needs Action (8) ===
  [27] Carlos Mendez — tree trimming (due 2026-03-30)
  [28] Beth Walters — concrete work (due 2026-03-31)
  [26] Rachel Kim — painting (due 2026-04-01)
  [30] Lisa Chen — window cleaning (due 2026-04-01)
  [25] Tom Nguyen — lawn care (due 2026-04-02)
  ... and 3 more
```

---

### `today` — leads due today

> "today's leads" / "who do I call today"

```
=== Today's Leads (1) ===

[followup_due] [23] James Okafor — gutter cleaning
   Status: followup_due
   Phone:  555-101-0003
   Follow up: 2026-04-04
   Notes:  Two-story home, needs annual cleaning
```

---

### `stale` — overdue follow-ups

> "stale leads" / "what's overdue" / "who have I been ignoring"

```
=== Stale Leads (8) ===

[followup_due] [27] Carlos Mendez — tree trimming
   Status: followup_due
   Quote:  $600
   Phone:  555-101-0007
   Follow up: 2026-03-30
   Notes:  Three oaks, one close to power line
...
```

---

### `lead <name>` — look up a lead

> "show lead Carlos" / "look up Beth" / "lead --id 27"

```
[followup_due] [27] Carlos Mendez — tree trimming
   Status: followup_due
   Quote:  $600
   Phone:  555-101-0007
   Follow up: 2026-03-30
   Notes:  Three oaks, one close to power line
```

---

### `draft-followup <name>` — AI-drafted follow-up text

> "draft followup for Carlos" / "write a follow-up for Beth" / "draft text for [name]"

```
Drafting follow-up for Carlos Mendez...

--- Draft ---
Hey Carlos, just wanted to follow up on the tree trimming quote I sent over.
I know the one near the power line is a priority — I can work around your
schedule and have a crew out within the week. Let me know if $600 still works
for you or if you have any questions.
```

> Requires `ANTHROPIC_API_KEY` in `.env`. Without it, the command will prompt
> you to add the key.

---

## Other commands available from chat

| Say | Runs |
|---|---|
| "list leads" | `leadclaw --plain list` |
| "list all leads" | `leadclaw --plain list --all` |
| "draft followup for [name]" | `leadclaw --plain draft-followup "[name]"` |
| "summarize [name]" | `leadclaw --plain summarize "[name]"` |
| "quote [name] [amount]" | `leadclaw --plain quote "[name]" [amount]` |
| "mark [name] won" | `leadclaw --plain won "[name]"` |
| "mark [name] lost [reason]" | `leadclaw --plain lost "[name]" [reason]` |
| "export leads" | `leadclaw --plain export` |
| "full pipeline analysis" | `leadclaw --plain pipeline` |

AI commands (`draft-followup`, `summarize`, `pipeline`) require `ANTHROPIC_API_KEY`
set in `.env`.

---

## Interactive commands (terminal only)

`add`, `edit`, and `delete` are interactive prompts — they can't run over chat.
Use them directly in your terminal:

```bash
leadclaw add
leadclaw edit "Mike"
leadclaw delete "Mike"
```
