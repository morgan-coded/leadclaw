# LeadClaw Environment Variables

## Required for Production

| Variable | Description | Example |
|----------|-------------|---------|
| `APP_URL` | Public base URL. Used in verification emails, notification links, and HTTPS cookie enforcement. | `https://app.yourdomain.com` |
| `LEADCLAW_SECRET_KEY` | Flask session secret. Must be a long random string. | `openssl rand -hex 32` |

## Email / Notifications

| Variable | Description | Default |
|----------|-------------|---------|
| `RESEND_API_KEY` | Resend API key for transactional email (preferred). If set, takes priority over SMTP. | _(none — dev mode)_ |
| `NOTIFY_FROM_EMAIL` | Sender address for all outgoing emails. Swap this when your domain is ready. | `LeadClaw <noreply@morganlabs.org>` |
| `OWNER_NOTIFY_EMAIL` | Where new-request alerts are sent. Required for notifications. | _(none)_ |
| `SMTP_HOST` | SMTP server hostname. Only used if `RESEND_API_KEY` is not set. | _(none)_ |
| `SMTP_PORT` | SMTP port. | `587` |
| `SMTP_USER` | SMTP username / login. | _(none)_ |
| `SMTP_PASS` | SMTP password. | _(none)_ |

## App Behavior

| Variable | Description | Default |
|----------|-------------|---------|
| `LEADCLAW_DB` | Path to SQLite database file. | `data/leads.db` |
| `PORT` | HTTP port to bind. | `7432` |
| `HOST` | Interface to bind. Use `0.0.0.0` behind a reverse proxy. | `127.0.0.1` |
| `LEADCLAW_RECURRING_DAYS` | Default recurring service interval (days). | `90` |
| `LEADCLAW_INVOICE_REMINDER_DAYS` | Days after invoice before payment reminder fires. | `3` |

---

## Cutover Checklist (when domain + email are ready)

1. Point DNS and get your domain live
2. Set up Resend (or SMTP) for your domain
3. Update `NOTIFY_FROM_EMAIL` → `LeadClaw <hello@yourdomain.com>`
4. Update `OWNER_NOTIFY_EMAIL` → your real email
5. Update `APP_URL` → `https://yourdomain.com`
6. Run the smoke test (`docs/smoke_test.md`) end-to-end
7. Verify notification email arrives in your inbox (not spam)
