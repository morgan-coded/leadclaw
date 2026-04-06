# LeadClaw Pilot Success Metrics

Internal note. One page. Keep it honest.

---

## What We're Measuring (Week 1)

| Metric | Keep Going | Stop / Revisit |
|--------|-----------|----------------|
| Real requests submitted | ≥3 from real customers | 0 real requests after 1 week |
| Requests booked | ≥1 booking made | Owner ignores all requests |
| Time to first response | Owner responds within 24h | Owner misses requests because of app |
| Jobs moved to paid | ≥1 invoice + paid cycle | No jobs completed |
| Reminder usage | Owner clicks reminders or messages | Reminders ignored entirely |
| Return use (day 7) | Owner opens dashboard unprompted | Not opened after day 2 |
| Replaces manual process | Owner says "I used to text/spreadsheet this" | Owner still using old method in parallel |

---

## Keep Going Signal

All three of these are true:
1. At least one real customer submitted a request through `/request`
2. Owner booked or followed up on at least one request through the app
3. Owner returns to the dashboard on their own in week 2

---

## Stop / Revisit Signal

Any one of these is true:
- Zero real requests submitted after 1 week (not even a test from the owner)
- Owner says the form is confusing or customers won't use it
- Owner can't figure out the booking or invoicing flow without hand-holding
- Owner says it added work instead of reducing it

---

## After Week 1

- Run a 10-minute call or async survey using the feedback questions in `pilot_package.md`
- Tally signals above
- If Keep Going: recruit 2–3 more pilot users, start tracking aggregate metrics
- If Stop: do a postmortem, fix the core blocker before recruiting more users

---

## Notes

- Don't optimize for "liked it" — optimize for "used it"
- One real paying job run through the app is worth more than 10 signups
- If the owner replaced their notebook with LeadClaw: that's the win
