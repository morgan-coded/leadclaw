# LeadClaw — Deployment & Ops Guide

## Running the web dashboard

```bash
# Install (one time)
cd leadclaw
pip install .

# Start (localhost only — default)
leadclaw-web

# Or with Python if binary not on PATH
python3 -m leadclaw.web
```

Opens at **http://localhost:7432**

---

## Where your data lives

LeadClaw uses a single SQLite file. Default location:

| Platform | Path |
|---|---|
| macOS / Linux | `~/.local/share/leadclaw/leads.db` |
| Fallback | `./leads.db` (current working directory) |

Find it:

```bash
python3 -c "from leadclaw.db import DB_PATH; print(DB_PATH)"
```

---

## Backup

SQLite is a single file — back it up like any other file.

**Manual:**
```bash
cp ~/.local/share/leadclaw/leads.db ~/backups/leads-$(date +%Y%m%d).db
```

**Cron (daily at 2am):**
```bash
0 2 * * * cp ~/.local/share/leadclaw/leads.db ~/backups/leads-$(date +\%Y\%m\%d).db
```

**Export to CSV (human-readable):**
```bash
leadclaw export --output leads-backup.csv
```

---

## Restore

```bash
# From SQLite backup
cp ~/backups/leads-20260404.db ~/.local/share/leadclaw/leads.db

# From CSV backup (re-imports all rows)
leadclaw import leads-backup.csv --yes
```

---

## Run on startup (macOS launchd)

Create `~/Library/LaunchAgents/com.leadclaw.web.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.leadclaw.web</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/leadclaw-web</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/leadclaw-web.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/leadclaw-web.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.leadclaw.web.plist
```

---

## Security model

**Default (localhost only):** Safe for personal use. No auth required because the
server only accepts connections from `127.0.0.1`. A DNS rebinding guard rejects
requests whose `Host` header doesn't match localhost.

**LAN / VPN exposure (`--host 0.0.0.0`):** All write endpoints are unauthenticated.
Only use this on a trusted network (home LAN, Tailscale, WireGuard). Never expose
directly to the internet.

**Internet exposure:** Put a reverse proxy in front with HTTP Basic Auth:

*Nginx example:*
```nginx
server {
    listen 443 ssl;
    server_name leads.morganlabs.org;

    auth_basic "LeadClaw";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:7432;
    }
}
```

*Caddy example:*
```
leads.morganlabs.org {
    basicauth {
        youruser JDJhJDE0...  # bcrypt hash from `caddy hash-password`
    }
    reverse_proxy localhost:7432
}
```

---

## Upgrading

```bash
cd leadclaw
git pull
pip install .
# Restart leadclaw-web
```

Schema changes are backward-compatible within the 0.x line. If a migration is
ever needed, it will be documented in the release notes.
