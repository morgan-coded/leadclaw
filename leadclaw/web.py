"""
web.py - Read-only dashboard server (no extra dependencies)

Usage:
    leadclaw-web            # serves on http://localhost:7432
    leadclaw-web --port 8080
    leadclaw-web --host 0.0.0.0 --port 8080
"""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from leadclaw.db import init_db
from leadclaw.queries import (
    get_all_active_leads,
    get_pipeline_summary,
    get_stale_leads,
    get_today_leads,
)

DEFAULT_PORT = 7432


# ---------------------------------------------------------------------------
# JSON API helpers
# ---------------------------------------------------------------------------


def _lead_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "service": row["service"],
        "status": row["status"],
        "phone": row["phone"],
        "email": row["email"],
        "quote_amount": row["quote_amount"],
        "follow_up_after": str(row["follow_up_after"])[:10] if row["follow_up_after"] else None,
        "notes": row["notes"],
    }


def api_summary() -> dict:
    summary_rows, totals = get_pipeline_summary()
    today = [_lead_to_dict(r) for r in get_today_leads()]
    stale = [_lead_to_dict(r) for r in get_stale_leads()]
    active = [_lead_to_dict(r) for r in get_all_active_leads()]

    by_status = {row["status"]: {"count": row["count"], "total": row["total_quoted"]}
                 for row in summary_rows}

    return {
        "pipeline": {
            "open_value": totals["open_value"],
            "won_value": totals["won_value"],
            "lost_value": totals["lost_value"],
            "by_status": by_status,
        },
        "today": today,
        "stale": stale,
        "active": active,
    }


# ---------------------------------------------------------------------------
# HTML dashboard (single-page, inline CSS + JS, no build step)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LeadClaw</title>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --text: #e8eaf0; --muted: #6b7280; --accent: #6366f1;
    --green: #22c55e; --yellow: #f59e0b; --red: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; font-size: 14px; line-height: 1.5; }
  header { padding: 16px 24px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 18px; font-weight: 700; letter-spacing: -0.3px; }
  header span { color: var(--muted); font-size: 12px; }
  .main { padding: 24px; max-width: 1100px; margin: 0 auto; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 32px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 20px; }
  .card .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); margin-bottom: 6px; }
  .card .value { font-size: 26px; font-weight: 700; }
  .card .sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .green { color: var(--green); } .yellow { color: var(--yellow); } .red { color: var(--red); } .accent { color: var(--accent); }
  section { margin-bottom: 32px; }
  section h2 { font-size: 14px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 12px; }
  .lead-list { display: flex; flex-direction: column; gap: 8px; }
  .lead { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; }
  .lead-name { font-weight: 600; }
  .lead-service { color: var(--muted); font-size: 12px; }
  .lead-meta { font-size: 12px; color: var(--muted); text-align: right; }
  .lead-due { font-size: 12px; font-weight: 500; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; }
  .badge-new { background: #1e3a5f; color: #60a5fa; }
  .badge-quoted { background: #1e3a5f; color: #a78bfa; }
  .badge-followup_due { background: #3b1f0a; color: #f59e0b; }
  .badge-won { background: #0d3321; color: #22c55e; }
  .badge-lost { background: #3b0d0d; color: #ef4444; }
  .empty { color: var(--muted); font-style: italic; padding: 12px 0; }
  .refresh { margin-left: auto; background: none; border: 1px solid var(--border); color: var(--muted); padding: 5px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
  .refresh:hover { border-color: var(--accent); color: var(--accent); }
</style>
</head>
<body>
<header>
  <h1>🦞 LeadClaw</h1>
  <span id="updated">Loading…</span>
  <button class="refresh" onclick="load()">Refresh</button>
</header>
<div class="main">
  <div class="cards" id="cards"></div>
  <section>
    <h2>Due Today</h2>
    <div class="lead-list" id="today"></div>
  </section>
  <section>
    <h2>Needs Action (Overdue)</h2>
    <div class="lead-list" id="stale"></div>
  </section>
  <section>
    <h2>Full Pipeline</h2>
    <div class="lead-list" id="active"></div>
  </section>
</div>
<script>
function fmt(n) { return n == null ? '—' : '$' + Number(n).toLocaleString(undefined, {maximumFractionDigits: 0}); }
function badge(s) { return `<span class="badge badge-${s}">${s.replace('_', ' ')}</span>`; }
function renderLead(l) {
  const due = l.follow_up_after ? `<div class="lead-due ${l.status === 'followup_due' ? 'yellow' : ''}">${l.follow_up_after}</div>` : '';
  const quote = l.quote_amount ? `<div class="lead-meta">${fmt(l.quote_amount)}</div>` : '<div></div>';
  const contact = [l.phone, l.email].filter(Boolean).join(' · ');
  return `<div class="lead">
    <div>
      <div class="lead-name">${l.name} ${badge(l.status)}</div>
      <div class="lead-service">${l.service || ''}${contact ? ' · ' + contact : ''}</div>
      ${l.notes ? `<div class="lead-service" style="margin-top:2px;color:#9ca3af">${l.notes}</div>` : ''}
    </div>
    <div style="text-align:right">${quote}${due}<div class="lead-meta">#${l.id}</div></div>
  </div>`;
}
function renderList(id, leads) {
  const el = document.getElementById(id);
  el.innerHTML = leads.length ? leads.map(renderLead).join('') : '<div class="empty">None</div>';
}
async function load() {
  try {
    const d = await fetch('/api/summary').then(r => r.json());
    const p = d.pipeline;
    const byStatus = p.by_status || {};
    const cards = [
      {label: 'Open Pipeline', value: fmt(p.open_value), cls: 'accent'},
      {label: 'Won', value: fmt(p.won_value), cls: 'green'},
      {label: 'Follow-up Due', value: (byStatus.followup_due || {count:0}).count, sub: 'leads overdue', cls: 'yellow'},
      {label: 'New', value: (byStatus.new || {count:0}).count, sub: 'not yet quoted', cls: ''},
      {label: 'Lost', value: fmt(p.lost_value), cls: 'red'},
    ];
    document.getElementById('cards').innerHTML = cards.map(c =>
      `<div class="card"><div class="label">${c.label}</div><div class="value ${c.cls}">${c.value}</div>${c.sub ? `<div class="sub">${c.sub}</div>` : ''}</div>`
    ).join('');
    renderList('today', d.today);
    renderList('stale', d.stale);
    renderList('active', d.active);
    document.getElementById('updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('updated').textContent = 'Error loading data';
  }
}
load();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress request logs

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/dashboard"):
            self.send_html(DASHBOARD_HTML)
        elif path == "/api/summary":
            try:
                self.send_json(api_summary())
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)
        else:
            self.send_response(404)
            self.end_headers()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    init_db()
    parser = argparse.ArgumentParser(
        prog="leadclaw-web",
        description="LeadClaw read-only web dashboard",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"LeadClaw dashboard → {url}")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
