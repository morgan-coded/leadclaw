"""
web.py - Full CRUD web dashboard (no extra dependencies)

Usage:
    leadclaw-web            # serves on http://localhost:7432
    leadclaw-web --port 8080
    leadclaw-web --host 0.0.0.0 --port 8080

API endpoints (JSON):
    GET  /api/summary
    GET  /api/leads/<id>
    POST /api/leads              { name, service, phone?, email?, notes?, followup_days? }
    POST /api/leads/<id>/edit    { name?, service?, phone?, email?, notes?, follow_up_after? }
    POST /api/leads/<id>/quote   { amount }
    POST /api/leads/<id>/won
    POST /api/leads/<id>/lost    { reason, notes? }
    POST /api/leads/<id>/delete
"""

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from leadclaw.config import DEFAULT_FOLLOWUP_DAYS, LOST_REASONS, MAX_FIELD_LENGTH, MAX_NAME_LENGTH
from leadclaw.db import init_db
from leadclaw.queries import (
    add_lead,
    delete_lead,
    get_all_active_leads,
    get_lead_by_id,
    get_pipeline_summary,
    get_stale_leads,
    get_today_leads,
    mark_lost,
    mark_won,
    update_lead,
    update_quote,
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
        "lost_reason": row["lost_reason"],
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
# HTML dashboard
# ---------------------------------------------------------------------------

_LOST_REASONS_JS = json.dumps(LOST_REASONS)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LeadClaw</title>
<style>
  :root {
    --bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2a2d3a;
    --text:#e8eaf0;--muted:#6b7280;--accent:#6366f1;--accent-h:#4f52d1;
    --green:#22c55e;--yellow:#f59e0b;--red:#ef4444;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;font-size:14px;line-height:1.5;}
  header{padding:14px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;}
  header h1{font-size:18px;font-weight:700;letter-spacing:-.3px;}
  header span{color:var(--muted);font-size:12px;}
  .btn{display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:none;color:var(--muted);cursor:pointer;font-size:12px;font-family:inherit;transition:all .15s;}
  .btn:hover{border-color:var(--accent);color:var(--accent);}
  .btn-primary{background:var(--accent);border-color:var(--accent);color:#fff;}
  .btn-primary:hover{background:var(--accent-h);border-color:var(--accent-h);color:#fff;}
  .btn-sm{padding:3px 8px;font-size:11px;}
  .btn-danger{color:var(--red)!important;}
  .btn-danger:hover{border-color:var(--red)!important;}
  .ml-auto{margin-left:auto;}
  .main{padding:24px;max-width:1140px;margin:0 auto;}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:28px;}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;}
  .card .label{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);margin-bottom:4px;}
  .card .value{font-size:24px;font-weight:700;}
  .card .sub{font-size:11px;color:var(--muted);margin-top:1px;}
  .green{color:var(--green);}.yellow{color:var(--yellow);}.red{color:var(--red);}.accent{color:var(--accent);}
  section{margin-bottom:28px;}
  section h2{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:10px;}
  .lead-list{display:flex;flex-direction:column;gap:7px;}
  .lead{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:11px 14px;display:flex;align-items:flex-start;gap:12px;}
  .lead-body{flex:1;min-width:0;}
  .lead-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
  .lead-name{font-weight:600;}
  .lead-service{color:var(--muted);font-size:12px;margin-top:1px;}
  .lead-contact{color:var(--muted);font-size:11px;}
  .lead-notes{color:#9ca3af;font-size:11px;margin-top:2px;}
  .lead-actions{display:flex;gap:5px;flex-shrink:0;align-items:flex-start;flex-wrap:wrap;justify-content:flex-end;}
  .lead-meta{font-size:11px;color:var(--muted);text-align:right;}
  .badge{display:inline-block;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;}
  .badge-new{background:#1e3a5f;color:#60a5fa;}
  .badge-quoted{background:#2a1e5f;color:#a78bfa;}
  .badge-followup_due{background:#3b1f0a;color:#f59e0b;}
  .badge-won{background:#0d3321;color:#22c55e;}
  .badge-lost{background:#3b0d0d;color:#ef4444;}
  .empty{color:var(--muted);font-style:italic;padding:10px 0;}
  /* Modal */
  .overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;align-items:center;justify-content:center;}
  .overlay.open{display:flex;}
  .modal{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;width:100%;max-width:440px;max-height:90vh;overflow-y:auto;}
  .modal h3{font-size:16px;font-weight:700;margin-bottom:18px;}
  .form-group{margin-bottom:14px;}
  .form-group label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px;}
  .form-group input,.form-group select,.form-group textarea{width:100%;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;font-family:inherit;outline:none;}
  .form-group input:focus,.form-group select:focus,.form-group textarea:focus{border-color:var(--accent);}
  .form-group textarea{resize:vertical;min-height:60px;}
  .form-group select option{background:var(--surface2);}
  .modal-footer{display:flex;gap:8px;justify-content:flex-end;margin-top:20px;}
  .err{color:var(--red);font-size:12px;margin-top:10px;display:none;}
  .toast{position:fixed;bottom:24px;right:24px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 16px;font-size:13px;z-index:200;opacity:0;transition:opacity .2s;pointer-events:none;}
  .toast.show{opacity:1;}
</style>
</head>
<body>
<header>
  <h1>🦞 LeadClaw</h1>
  <span id="updated">Loading…</span>
  <button class="btn ml-auto" onclick="load()">Refresh</button>
  <button class="btn btn-primary" onclick="openAdd()">+ Add Lead</button>
</header>
<div class="main">
  <div class="cards" id="cards"></div>
  <section><h2>Due Today</h2><div class="lead-list" id="today"></div></section>
  <section><h2>Needs Action (Overdue)</h2><div class="lead-list" id="stale"></div></section>
  <section><h2>Full Pipeline</h2><div class="lead-list" id="active"></div></section>
</div>

<!-- Add/Edit modal -->
<div class="overlay" id="modal-edit" onclick="closeModal(event)">
  <div class="modal">
    <h3 id="modal-title">Add Lead</h3>
    <input type="hidden" id="edit-id">
    <div class="form-group"><label>Name *</label><input id="edit-name" placeholder="Full name"></div>
    <div class="form-group"><label>Service *</label><input id="edit-service" placeholder="What they need"></div>
    <div class="form-group"><label>Phone</label><input id="edit-phone" placeholder="555-000-0000" type="tel"></div>
    <div class="form-group"><label>Email</label><input id="edit-email" placeholder="email@example.com" type="email"></div>
    <div class="form-group" id="fg-followup"><label>Follow-up in (days)</label><input id="edit-followup" type="number" min="0" value="3"></div>
    <div class="form-group" id="fg-followup-date" style="display:none"><label>Follow-up date</label><input id="edit-followup-date" type="date"></div>
    <div class="form-group"><label>Notes</label><textarea id="edit-notes" rows="2"></textarea></div>
    <div class="err" id="edit-err"></div>
    <div class="modal-footer">
      <button class="btn" onclick="closeOverlay('modal-edit')">Cancel</button>
      <button class="btn btn-primary" onclick="submitEdit()">Save</button>
    </div>
  </div>
</div>

<!-- Quote modal -->
<div class="overlay" id="modal-quote" onclick="closeModal(event)">
  <div class="modal">
    <h3>Set Quote</h3>
    <input type="hidden" id="quote-id">
    <div class="form-group"><label>Quote Amount ($)</label><input id="quote-amount" type="number" min="1" placeholder="850"></div>
    <div class="err" id="quote-err"></div>
    <div class="modal-footer">
      <button class="btn" onclick="closeOverlay('modal-quote')">Cancel</button>
      <button class="btn btn-primary" onclick="submitQuote()">Set Quote</button>
    </div>
  </div>
</div>

<!-- Lost modal -->
<div class="overlay" id="modal-lost" onclick="closeModal(event)">
  <div class="modal">
    <h3>Mark Lost</h3>
    <input type="hidden" id="lost-id">
    <div class="form-group">
      <label>Reason</label>
      <select id="lost-reason"></select>
    </div>
    <div class="form-group" id="lost-notes-group" style="display:none">
      <label>Notes (required for "other")</label>
      <textarea id="lost-notes" rows="2"></textarea>
    </div>
    <div class="err" id="lost-err"></div>
    <div class="modal-footer">
      <button class="btn" onclick="closeOverlay('modal-lost')">Cancel</button>
      <button class="btn btn-primary btn-danger" onclick="submitLost()">Mark Lost</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const LOST_REASONS = """ + _LOST_REASONS_JS + """;

// Populate lost reason select
const sel = document.getElementById('lost-reason');
LOST_REASONS.forEach(r => { const o = document.createElement('option'); o.value = r; o.textContent = r.replace('_',' '); sel.appendChild(o); });
sel.addEventListener('change', () => {
  document.getElementById('lost-notes-group').style.display = sel.value === 'other' ? '' : 'none';
});

function fmt(n){return n==null?'—':'$'+Number(n).toLocaleString(undefined,{maximumFractionDigits:0});}
function badge(s){return `<span class="badge badge-${s}">${s.replace(/_/g,' ')}</span>`;}
function esc(s){return s?s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'):''}

function toast(msg, err=false){
  const t=document.getElementById('toast');
  t.textContent=msg; t.style.borderColor=err?'var(--red)':'var(--border)';
  t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),2500);
}

function renderLead(l){
  const due=l.follow_up_after?`<div class="lead-meta ${l.status==='followup_due'?'yellow':''}">${l.follow_up_after}</div>`:'';
  const quote=l.quote_amount?`<div class="lead-meta">${fmt(l.quote_amount)}</div>`:'';
  const contact=[l.phone,l.email].filter(Boolean).join(' · ');
  const active=!['won','lost'].includes(l.status);
  const actions=active?`
    <button class="btn btn-sm" onclick='openQuote(${l.id})'>Quote</button>
    <button class="btn btn-sm" onclick='openEdit(${JSON.stringify(l)})'>Edit</button>
    <button class="btn btn-sm" onclick='doWon(${l.id},"${esc(l.name)}")'>Won</button>
    <button class="btn btn-sm btn-danger" onclick='openLost(${l.id})'>Lost</button>
    <button class="btn btn-sm btn-danger" onclick='doDelete(${l.id},"${esc(l.name)}")'>Del</button>
  `:`<button class="btn btn-sm btn-danger" onclick='doDelete(${l.id},"${esc(l.name)}")'>Del</button>`;
  return `<div class="lead">
    <div class="lead-body">
      <div class="lead-top">
        <span class="lead-name">${esc(l.name)}</span>${badge(l.status)}
      </div>
      <div class="lead-service">${esc(l.service||'')}${contact?' · '+esc(contact):''}</div>
      ${l.notes?`<div class="lead-notes">${esc(l.notes)}</div>`:''}
    </div>
    <div class="lead-actions">
      <div>${quote}${due}<div class="lead-meta">#${l.id}</div></div>
      <div style="display:flex;flex-direction:column;gap:4px;align-items:flex-end">${actions}</div>
    </div>
  </div>`;
}

function renderList(id,leads){
  document.getElementById(id).innerHTML=leads.length?leads.map(renderLead).join(''):'<div class="empty">None</div>';
}

async function load(){
  try{
    const d=await fetch('/api/summary').then(r=>r.json());
    const p=d.pipeline; const b=p.by_status||{};
    const cards=[
      {label:'Open Pipeline',value:fmt(p.open_value),cls:'accent'},
      {label:'Won',value:fmt(p.won_value),cls:'green'},
      {label:'Follow-up Due',value:(b.followup_due||{count:0}).count,sub:'leads overdue',cls:'yellow'},
      {label:'New',value:(b.new||{count:0}).count,sub:'not yet quoted',cls:''},
      {label:'Lost',value:fmt(p.lost_value),cls:'red'},
    ];
    document.getElementById('cards').innerHTML=cards.map(c=>
      `<div class="card"><div class="label">${c.label}</div><div class="value ${c.cls}">${c.value}</div>${c.sub?`<div class="sub">${c.sub}</div>`:''}</div>`
    ).join('');
    renderList('today',d.today);
    renderList('stale',d.stale);
    renderList('active',d.active);
    document.getElementById('updated').textContent='Updated '+new Date().toLocaleTimeString();
  }catch(e){document.getElementById('updated').textContent='Error loading';}
}

// ---- Modal helpers ----
function closeModal(e){if(e.target===e.currentTarget)e.target.classList.remove('open');}
function closeOverlay(id){document.getElementById(id).classList.remove('open');}

// ---- Add Lead ----
function openAdd(){
  document.getElementById('modal-title').textContent='Add Lead';
  document.getElementById('edit-id').value='';
  ['edit-name','edit-service','edit-phone','edit-email','edit-notes'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('edit-followup').value='3';
  document.getElementById('fg-followup').style.display='';
  document.getElementById('fg-followup-date').style.display='none';
  document.getElementById('edit-err').style.display='none';
  document.getElementById('modal-edit').classList.add('open');
}

// ---- Edit Lead ----
function openEdit(l){
  document.getElementById('modal-title').textContent='Edit Lead';
  document.getElementById('edit-id').value=l.id;
  document.getElementById('edit-name').value=l.name||'';
  document.getElementById('edit-service').value=l.service||'';
  document.getElementById('edit-phone').value=l.phone||'';
  document.getElementById('edit-email').value=l.email||'';
  document.getElementById('edit-notes').value=l.notes||'';
  document.getElementById('edit-followup-date').value=l.follow_up_after||'';
  document.getElementById('fg-followup').style.display='none';
  document.getElementById('fg-followup-date').style.display='';
  document.getElementById('edit-err').style.display='none';
  document.getElementById('modal-edit').classList.add('open');
}

async function submitEdit(){
  const id=document.getElementById('edit-id').value;
  const name=document.getElementById('edit-name').value.trim();
  const service=document.getElementById('edit-service').value.trim();
  const errEl=document.getElementById('edit-err');
  if(!name||!service){errEl.textContent='Name and service are required.';errEl.style.display='';return;}
  const body={name,service,
    phone:document.getElementById('edit-phone').value.trim()||null,
    email:document.getElementById('edit-email').value.trim()||null,
    notes:document.getElementById('edit-notes').value.trim()||null,
  };
  if(!id){body.followup_days=parseInt(document.getElementById('edit-followup').value)||3;}
  else{body.follow_up_after=document.getElementById('edit-followup-date').value||null;}
  const url=id?`/api/leads/${id}/edit`:'/api/leads';
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeOverlay('modal-edit');
  toast(id?'Lead updated.':'Lead added.');
  load();
}

// ---- Quote ----
function openQuote(id){
  document.getElementById('quote-id').value=id;
  document.getElementById('quote-amount').value='';
  document.getElementById('quote-err').style.display='none';
  document.getElementById('modal-quote').classList.add('open');
}
async function submitQuote(){
  const id=document.getElementById('quote-id').value;
  const amount=parseFloat(document.getElementById('quote-amount').value);
  const errEl=document.getElementById('quote-err');
  if(!amount||amount<=0){errEl.textContent='Enter a valid amount > 0.';errEl.style.display='';return;}
  const r=await fetch(`/api/leads/${id}/quote`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({amount})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeOverlay('modal-quote'); toast('Quote set.'); load();
}

// ---- Won ----
async function doWon(id,name){
  if(!confirm(`Mark "${name}" as WON?`))return;
  const r=await fetch(`/api/leads/${id}/won`,{method:'POST'});
  if(r.ok){toast('Marked won! 🎉');load();}else{toast('Error',true);}
}

// ---- Lost ----
function openLost(id){
  document.getElementById('lost-id').value=id;
  document.getElementById('lost-reason').value=LOST_REASONS[0];
  document.getElementById('lost-notes').value='';
  document.getElementById('lost-notes-group').style.display='none';
  document.getElementById('lost-err').style.display='none';
  document.getElementById('modal-lost').classList.add('open');
}
async function submitLost(){
  const id=document.getElementById('lost-id').value;
  const reason=document.getElementById('lost-reason').value;
  const notes=document.getElementById('lost-notes').value.trim();
  const errEl=document.getElementById('lost-err');
  if(reason==='other'&&!notes){errEl.textContent='Notes required for "other".';errEl.style.display='';return;}
  const r=await fetch(`/api/leads/${id}/lost`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason,notes:notes||null})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeOverlay('modal-lost'); toast('Marked lost.'); load();
}

// ---- Delete ----
async function doDelete(id,name){
  if(!confirm(`Delete "${name}"? This cannot be undone.`))return;
  const r=await fetch(`/api/leads/${id}/delete`,{method:'POST'});
  if(r.ok){toast('Deleted.');load();}else{toast('Error',true);}
}

load();
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

_ID_PATTERN = re.compile(r"^/api/leads/(\d+)/(\w+)$")


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

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/dashboard"):
            self.send_html(DASHBOARD_HTML)
        elif path == "/api/summary":
            try:
                self.send_json(api_summary())
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        elif re.match(r"^/api/leads/(\d+)$", path):
            lead_id = int(path.split("/")[-1])
            lead = get_lead_by_id(lead_id)
            if lead:
                self.send_json(_lead_to_dict(lead))
            else:
                self.send_json({"error": "Not found"}, 404)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.read_json_body()
        except (json.JSONDecodeError, ValueError):
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        # POST /api/leads — add new lead
        if path == "/api/leads":
            name = (body.get("name") or "").strip()
            service = (body.get("service") or "").strip()
            if not name or not service:
                self.send_json({"error": "name and service are required"}, 400)
                return
            if len(name) > MAX_NAME_LENGTH:
                self.send_json({"error": f"name max {MAX_NAME_LENGTH} chars"}, 400)
                return
            phone = (body.get("phone") or "").strip() or None
            email = (body.get("email") or "").strip() or None
            notes = (body.get("notes") or "").strip() or None
            if notes and len(notes) > MAX_FIELD_LENGTH:
                self.send_json({"error": f"notes max {MAX_FIELD_LENGTH} chars"}, 400)
                return
            try:
                followup_days = int(body.get("followup_days") or DEFAULT_FOLLOWUP_DAYS)
            except (ValueError, TypeError):
                followup_days = DEFAULT_FOLLOWUP_DAYS
            lead_id, _ = add_lead(name, service, phone=phone, email=email,
                                   notes=notes, followup_days=followup_days)
            self.send_json({"id": lead_id}, 201)
            return

        # POST /api/leads/<id>/<action>
        m = _ID_PATTERN.match(path)
        if not m:
            self.send_json({"error": "Not found"}, 404)
            return

        lead_id, action = int(m.group(1)), m.group(2)
        lead = get_lead_by_id(lead_id)
        if not lead:
            self.send_json({"error": f"Lead {lead_id} not found"}, 404)
            return

        if action == "edit":
            fields = {}
            for field in ("name", "service", "phone", "email", "notes", "follow_up_after"):
                val = body.get(field)
                if val is not None:
                    val = str(val).strip() or None
                    if val and len(val) > MAX_FIELD_LENGTH:
                        self.send_json({"error": f"{field} max {MAX_FIELD_LENGTH} chars"}, 400)
                        return
                    if val is not None:
                        fields[field] = val
            update_lead(lead_id, **fields)
            self.send_json({"ok": True})

        elif action == "quote":
            amount = body.get("amount")
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                self.send_json({"error": "amount must be a number"}, 400)
                return
            if amount <= 0:
                self.send_json({"error": "amount must be > 0"}, 400)
                return
            update_quote(lead_id, amount)
            self.send_json({"ok": True})

        elif action == "won":
            mark_won(lead_id)
            self.send_json({"ok": True})

        elif action == "lost":
            reason = (body.get("reason") or "").strip()
            if reason not in LOST_REASONS:
                self.send_json({"error": f"reason must be one of: {', '.join(LOST_REASONS)}"}, 400)
                return
            notes = (body.get("notes") or "").strip() or None
            if reason == "other" and not notes:
                self.send_json({"error": "notes required when reason is 'other'"}, 400)
                return
            mark_lost(lead_id, reason, notes=notes)
            self.send_json({"ok": True})

        elif action == "delete":
            delete_lead(lead_id)
            self.send_json({"ok": True})

        else:
            self.send_json({"error": f"Unknown action: {action}"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    init_db()
    parser = argparse.ArgumentParser(
        prog="leadclaw-web",
        description="LeadClaw web dashboard (read + write)",
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
