"""
web.py - Full CRUD web dashboard (no extra dependencies)

Usage:
    leadclaw-web                    # http://localhost:7432 (localhost only)
    leadclaw-web --port 8080
    leadclaw-web --host 0.0.0.0     # bind all interfaces (trusted LAN only)

Security model:
    Default bind is 127.0.0.1 — localhost only, no auth required.
    Binding to 0.0.0.0 exposes unauthenticated write endpoints to your network.
    For any exposure beyond localhost, put Nginx/Caddy in front with HTTP Basic Auth.
    See OPENCLAW.md for deployment and backup guidance.

API endpoints (JSON):
    GET  /api/summary
    GET  /api/leads/<id>
    GET  /api/closed
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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import leadclaw.pilot as _pilot
from leadclaw.config import (
    DEFAULT_FOLLOWUP_DAYS,
    LOST_REASONS,
    MAX_FIELD_LENGTH,
    MAX_NAME_LENGTH,
)
from leadclaw.db import init_db
from leadclaw.queries import (
    add_lead,
    delete_lead,
    get_all_active_leads,
    get_all_leads,
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
# Validation helpers (mirrors CLI)
# ---------------------------------------------------------------------------


def _valid_email(val: str) -> bool:
    return "@" in val and "." in val.split("@")[-1]


def _valid_date(val: str) -> bool:
    try:
        datetime.strptime(val, "%Y-%m-%d")
        return True
    except ValueError:
        return False


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
        "lost_reason_notes": row["lost_reason_notes"] if "lost_reason_notes" in row.keys() else None,
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


def api_closed() -> dict:
    """All won/lost leads for the closed-leads browser view."""
    all_leads = get_all_leads(limit=10000)
    closed = [_lead_to_dict(r) for r in all_leads
              if r["status"] in ("won", "lost")]
    return {"closed": closed}


def _candidate_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "business_name": row["business_name"],
        "phone": row["phone"],
        "email": row["email"],
        "service_type": row["service_type"],
        "location": row["location"],
        "source": row["source"],
        "score": row["score"],
        "status": row["status"],
        "notes": row["notes"],
        "outreach_draft": row["outreach_draft"],
        "reply_text": row["reply_text"],
        "reply_summary": row["reply_summary"],
        "contacted_at": str(row["contacted_at"])[:10] if row["contacted_at"] else None,
        "follow_up_after": str(row["follow_up_after"])[:10] if row["follow_up_after"] else None,
        "created_at": str(row["created_at"])[:10] if row["created_at"] else None,
    }


def api_pilot_candidates(status: str = None) -> dict:
    rows = _pilot.get_all_candidates(status=status or None, limit=500)
    summary = _pilot.get_pilot_summary()
    followups = _pilot.get_followup_due()
    return {
        "candidates": [_candidate_to_dict(r) for r in rows],
        "summary": summary,
        "followup_count": len(followups),
    }


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

_LOST_REASONS_JS = json.dumps(LOST_REASONS)
_MAX_NAME_JS = MAX_NAME_LENGTH
_MAX_FIELD_JS = MAX_FIELD_LENGTH

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LeadClaw</title>
<style>
  :root{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2a2d3a;--text:#e8eaf0;--muted:#6b7280;--accent:#6366f1;--accent-h:#4f52d1;--green:#22c55e;--yellow:#f59e0b;--red:#ef4444;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;font-size:14px;line-height:1.5;}
  header{padding:14px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
  header h1{font-size:18px;font-weight:700;letter-spacing:-.3px;}
  header span{color:var(--muted);font-size:12px;}
  .btn{display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:none;color:var(--muted);cursor:pointer;font-size:12px;font-family:inherit;transition:all .15s;}
  .btn:hover{border-color:var(--accent);color:var(--accent);}
  .btn-primary{background:var(--accent);border-color:var(--accent);color:#fff;}
  .btn-primary:hover{background:var(--accent-h);border-color:var(--accent-h);color:#fff;}
  .btn-sm{padding:3px 8px;font-size:11px;}
  .btn-danger{color:var(--red)!important;}
  .btn-danger:hover{border-color:var(--red)!important;}
  .btn-active{border-color:var(--accent);color:var(--accent);}
  .ml-auto{margin-left:auto;}
  .main{padding:24px;max-width:1140px;margin:0 auto;}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:28px;}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;}
  .card .label{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);margin-bottom:4px;}
  .card .value{font-size:24px;font-weight:700;}
  .card .sub{font-size:11px;color:var(--muted);margin-top:1px;}
  .green{color:var(--green);}.yellow{color:var(--yellow);}.red{color:var(--red);}.accent{color:var(--accent);}
  .tabs{display:flex;gap:8px;margin-bottom:20px;border-bottom:1px solid var(--border);padding-bottom:0;}
  .tab{padding:6px 14px;cursor:pointer;border-bottom:2px solid transparent;font-size:13px;color:var(--muted);transition:all .15s;margin-bottom:-1px;}
  .tab.active{color:var(--text);border-color:var(--accent);}
  .tab-panel{display:none;}.tab-panel.active{display:block;}
  section{margin-bottom:28px;}
  section h2{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:10px;}
  .lead-list{display:flex;flex-direction:column;gap:7px;}
  .lead{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:11px 14px;display:flex;align-items:flex-start;gap:12px;}
  .lead-body{flex:1;min-width:0;}
  .lead-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
  .lead-name{font-weight:600;}
  .lead-service{color:var(--muted);font-size:12px;margin-top:1px;}
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
  .warn-banner{background:#2a1a0a;border:1px solid #7c4a00;border-radius:7px;padding:10px 14px;font-size:12px;color:#f59e0b;margin-bottom:16px;}
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

  <div class="tabs">
    <div class="tab active" onclick="switchTab('pipeline')">Pipeline</div>
    <div class="tab" onclick="switchTab('closed')">Closed</div>
    <div class="tab" id="tab-btn-pilot" onclick="switchTab('pilot')">Pilot</div>
  </div>

  <div class="tab-panel active" id="tab-pipeline">
    <section><h2>Due Today</h2><div class="lead-list" id="today"></div></section>
    <section><h2>Needs Action (Overdue)</h2><div class="lead-list" id="stale"></div></section>
    <section><h2>Full Pipeline</h2><div class="lead-list" id="active"></div></section>
  </div>

  <div class="tab-panel" id="tab-closed">
    <section><h2>Won &amp; Lost</h2><div class="lead-list" id="closed"></div></section>
  </div>

  <div class="tab-panel" id="tab-pilot">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap">
      <div id="pilot-summary-bar" style="color:var(--muted);font-size:12px"></div>
      <div style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap">
        <select id="pilot-filter" onchange="loadPilot()" style="background:var(--surface2);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:5px 10px;font-size:12px;font-family:inherit">
          <option value="">All statuses</option>
          <option value="new">new</option>
          <option value="drafted">drafted</option>
          <option value="approved">approved</option>
          <option value="sent">sent</option>
          <option value="replied">replied</option>
          <option value="converted">converted</option>
          <option value="passed">passed</option>
        </select>
      </div>
    </div>
    <div id="pilot-followup-banner" class="warn-banner" style="display:none;margin-bottom:14px"></div>
    <div id="pilot-table-wrap">
      <table id="pilot-table" style="width:100%;border-collapse:collapse;font-size:13px">
        <thead>
          <tr style="border-bottom:1px solid var(--border);color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px">
            <th style="padding:8px 10px;text-align:left">Name / Business</th>
            <th style="padding:8px 6px;text-align:left">Service</th>
            <th style="padding:8px 6px;text-align:left">Location</th>
            <th style="padding:8px 6px;text-align:center">Score</th>
            <th style="padding:8px 6px;text-align:center">Status</th>
            <th style="padding:8px 6px;text-align:left">Source</th>
            <th style="padding:8px 6px;text-align:left">Follow-up</th>
            <th style="padding:8px 6px;text-align:left">Reply</th>
            <th style="padding:8px 6px;text-align:right">Actions</th>
          </tr>
        </thead>
        <tbody id="pilot-tbody"></tbody>
      </table>
      <div id="pilot-empty" class="empty" style="display:none">No candidates. Import a CSV or add manually via CLI.</div>
    </div>
  </div>
</div>

<!-- Pilot draft modal -->
<div class="overlay" id="modal-pilot-draft" onclick="closeModal(event)">
  <div class="modal" style="max-width:520px">
    <h3 id="pdraft-title">Outreach Draft</h3>
    <input type="hidden" id="pdraft-id">
    <div class="form-group">
      <label>Draft text (edit before approving)</label>
      <textarea id="pdraft-text" rows="6" style="font-size:13px"></textarea>
    </div>
    <div class="err" id="pdraft-err"></div>
    <div class="modal-footer">
      <button class="btn" onclick="closeOverlay('modal-pilot-draft')">Cancel</button>
      <button class="btn" onclick="savePilotDraft(false)">Save only</button>
      <button class="btn btn-primary" onclick="savePilotDraft(true)">Save &amp; Approve</button>
    </div>
  </div>
</div>

<!-- Pilot reply modal -->
<div class="overlay" id="modal-pilot-reply" onclick="closeModal(event)">
  <div class="modal" style="max-width:520px">
    <h3>Log Reply</h3>
    <input type="hidden" id="preply-id">
    <div class="form-group">
      <label>Paste their reply</label>
      <textarea id="preply-text" rows="5" placeholder="Their exact response..."></textarea>
    </div>
    <div class="err" id="preply-err"></div>
    <div class="modal-footer">
      <button class="btn" onclick="closeOverlay('modal-pilot-reply')">Cancel</button>
      <button class="btn btn-primary" onclick="submitPilotReply()">Log &amp; Summarize</button>
    </div>
  </div>
</div>

<!-- Add/Edit modal -->
<div class="overlay" id="modal-edit" onclick="closeModal(event)">
  <div class="modal">
    <h3 id="modal-title">Add Lead</h3>
    <div id="dup-warn" class="warn-banner" style="display:none"></div>
    <input type="hidden" id="edit-id">
    <div class="form-group"><label>Name *</label><input id="edit-name" placeholder="Full name" maxlength="100"></div>
    <div class="form-group"><label>Service *</label><input id="edit-service" placeholder="What they need" maxlength="500"></div>
    <div class="form-group"><label>Phone</label><input id="edit-phone" placeholder="555-000-0000" type="tel" maxlength="500"></div>
    <div class="form-group"><label>Email</label><input id="edit-email" placeholder="email@example.com" type="email" maxlength="500"></div>
    <div class="form-group" id="fg-followup"><label>Follow-up in (days)</label><input id="edit-followup" type="number" min="0" value="3"></div>
    <div class="form-group" id="fg-followup-date" style="display:none"><label>Follow-up date</label><input id="edit-followup-date" type="date"></div>
    <div class="form-group"><label>Notes</label><textarea id="edit-notes" rows="2" maxlength="500"></textarea></div>
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
    <div class="form-group"><label>Reason</label><select id="lost-reason"></select></div>
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
const LOST_REASONS=""" + _LOST_REASONS_JS + """;
const MAX_NAME=""" + str(_MAX_NAME_JS) + """;
const MAX_FIELD=""" + str(_MAX_FIELD_JS) + r""";

// Populate lost reason select
(function(){
  const sel=document.getElementById('lost-reason');
  LOST_REASONS.forEach(r=>{const o=document.createElement('option');o.value=r;o.textContent=r.replace(/_/g,' ');sel.appendChild(o);});
  sel.addEventListener('change',()=>{document.getElementById('lost-notes-group').style.display=sel.value==='other'?'':'none';});
})();

function fmt(n){return n==null?'—':'$'+Number(n).toLocaleString(undefined,{maximumFractionDigits:0});}
function badge(s){return `<span class="badge badge-${s}">${s.replace(/_/g,' ')}</span>`;}
function esc(s){return s?String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'):''}

function toast(msg,err=false){
  const t=document.getElementById('toast');
  t.textContent=msg;t.style.borderColor=err?'var(--red)':'var(--border)';
  t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2500);
}

// ---- Client-side validation (mirrors server) ----
function validEmail(v){return v.includes('@')&&v.split('@').pop().includes('.');}
function validDate(v){return /^\d{4}-\d{2}-\d{2}$/.test(v)&&!isNaN(Date.parse(v));}

// ---- Tabs ----
function switchTab(name){
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',['pipeline','closed','pilot'][i]===name));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.id==='tab-'+name));
  if(name==='closed')loadClosed();
  if(name==='pilot')loadPilot();
}

// ---- Render ----
function renderLead(l,showActions=true){
  const due=l.follow_up_after?`<div class="lead-meta ${l.status==='followup_due'?'yellow':''}">${l.follow_up_after}</div>`:'';
  const quote=l.quote_amount?`<div class="lead-meta">${fmt(l.quote_amount)}</div>`:'';
  const contact=[l.phone,l.email].filter(Boolean).join(' · ');
  const isActive=!['won','lost'].includes(l.status);
  const lj=esc(JSON.stringify(l));
  const actions=showActions?(isActive?`
    <button class="btn btn-sm" onclick='openQuote(${l.id})'>Quote</button>
    <button class="btn btn-sm" onclick='openEdit(JSON.parse(this.dataset.l))' data-l="${lj}">Edit</button>
    <button class="btn btn-sm" onclick='doWon(${l.id},"${esc(l.name)}")'>Won</button>
    <button class="btn btn-sm btn-danger" onclick='openLost(${l.id})'>Lost</button>
    <button class="btn btn-sm btn-danger" onclick='doDelete(${l.id},"${esc(l.name)}")'>Del</button>
  `:`<button class="btn btn-sm btn-danger" onclick='doDelete(${l.id},"${esc(l.name)}")'>Del</button>`):'';
  const lostNote=l.lost_reason?`<div class="lead-notes">Lost: ${esc(l.lost_reason)}${l.lost_reason_notes?' — '+esc(l.lost_reason_notes):''}</div>`:'';
  return `<div class="lead" data-id="${l.id}" data-status="${l.status}">
    <div class="lead-body">
      <div class="lead-top"><span class="lead-name">${esc(l.name)}</span>${badge(l.status)}</div>
      <div class="lead-service">${esc(l.service||'')}${contact?' · '+esc(contact):''}</div>
      ${l.notes?`<div class="lead-notes">${esc(l.notes)}</div>`:''}
      ${lostNote}
    </div>
    <div class="lead-actions">
      <div>${quote}${due}<div class="lead-meta">#${l.id}</div></div>
      <div style="display:flex;flex-direction:column;gap:4px;align-items:flex-end">${actions}</div>
    </div>
  </div>`;
}

function renderList(id,leads,showActions=true){
  document.getElementById(id).innerHTML=leads.length?leads.map(l=>renderLead(l,showActions)).join(''):'<div class="empty">None</div>';
}

async function load(){
  try{
    const d=await fetch('/api/summary').then(r=>r.json());
    const p=d.pipeline,b=p.by_status||{};
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

async function loadClosed(){
  try{
    const d=await fetch('/api/closed').then(r=>r.json());
    renderList('closed',d.closed,true);
  }catch(e){document.getElementById('closed').innerHTML='<div class="empty">Error loading closed leads.</div>';}
}

// ---- Modal helpers ----
function closeModal(e){if(e.target===e.currentTarget)e.target.classList.remove('open');}
function closeOverlay(id){document.getElementById(id).classList.remove('open');}

// ---- Add Lead ----
function openAdd(){
  document.getElementById('modal-title').textContent='Add Lead';
  document.getElementById('edit-id').value='';
  document.getElementById('dup-warn').style.display='none';
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
  document.getElementById('dup-warn').style.display='none';
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
  const email=document.getElementById('edit-email').value.trim();
  const followupDate=document.getElementById('edit-followup-date').value;
  const errEl=document.getElementById('edit-err');

  if(!name||!service){errEl.textContent='Name and service are required.';errEl.style.display='';return;}
  if(name.length>MAX_NAME){errEl.textContent=`Name max ${MAX_NAME} chars.`;errEl.style.display='';return;}
  if(email&&!validEmail(email)){errEl.textContent='Invalid email format.';errEl.style.display='';return;}
  if(id&&followupDate&&!validDate(followupDate)){errEl.textContent='Follow-up date must be YYYY-MM-DD.';errEl.style.display='';return;}

  const body={name,service,
    phone:document.getElementById('edit-phone').value.trim()||null,
    email:email||null,
    notes:document.getElementById('edit-notes').value.trim()||null,
  };
  if(!id){body.followup_days=parseInt(document.getElementById('edit-followup').value)||3;}
  else{body.follow_up_after=followupDate||null;}

  const url=id?`/api/leads/${id}/edit`:'/api/leads';
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}

  // Show duplicate warning if server flagged matches
  if(!id&&j.duplicates&&j.duplicates.length){
    const w=document.getElementById('dup-warn');
    w.textContent=`⚠ ${j.duplicates.length} existing lead(s) with the same name: `+j.duplicates.map(d=>d.name).join(', ');
    w.style.display='';
  } else {
    closeOverlay('modal-edit');
    toast(id?'Lead updated.':'Lead added.');
    load();
  }
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
  closeOverlay('modal-quote');toast('Quote set.');load();
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
  closeOverlay('modal-lost');toast('Marked lost.');load();
}

// ---- Delete ----
async function doDelete(id,name){
  if(!confirm(`Delete "${name}"? This cannot be undone.`))return;
  const r=await fetch(`/api/leads/${id}/delete`,{method:'POST'});
  if(r.ok){toast('Deleted.');load();}else{toast('Error',true);}
}

// ===========================================================================
// Pilot tracker
// ===========================================================================

const PILOT_STATUSES=['new','drafted','approved','sent','replied','converted','passed'];
const PILOT_STATUS_COLORS={
  new:'#60a5fa',drafted:'#a78bfa',approved:'#34d399',
  sent:'#f59e0b',replied:'#fb923c',converted:'#22c55e',passed:'#6b7280'
};

function pilotBadge(s){
  const c=PILOT_STATUS_COLORS[s]||'#9ca3af';
  return `<span style="display:inline-block;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:600;text-transform:uppercase;background:${c}22;color:${c}">${s}</span>`;
}

function scoreBar(n){
  const c=n>=80?'var(--green)':n>=60?'var(--yellow)':'var(--red)';
  return `<div style="display:flex;align-items:center;gap:5px">
    <div style="width:40px;height:5px;background:var(--border);border-radius:3px;overflow:hidden">
      <div style="width:${n}%;height:100%;background:${c}"></div>
    </div>
    <span style="font-size:11px;color:${c}">${n}</span>
  </div>`;
}

async function loadPilot(){
  const status=document.getElementById('pilot-filter').value;
  const url='/api/pilot'+(status?'?status='+encodeURIComponent(status):'');
  try{
    const d=await fetch(url).then(r=>r.json());
    // Summary bar
    const bs=d.summary.by_status||{};
    const parts=PILOT_STATUSES.filter(s=>bs[s]).map(s=>`${s}: ${bs[s]}`);
    document.getElementById('pilot-summary-bar').textContent=`${d.summary.total} total — `+parts.join(' · ');
    // Follow-up banner
    const fb=document.getElementById('pilot-followup-banner');
    if(d.followup_count>0){
      fb.textContent=`⚠ ${d.followup_count} candidate(s) overdue for follow-up`;
      fb.style.display='';
    }else{fb.style.display='none';}
    // Table
    const tbody=document.getElementById('pilot-tbody');
    const empty=document.getElementById('pilot-empty');
    if(!d.candidates.length){tbody.innerHTML='';empty.style.display='';return;}
    empty.style.display='none';
    tbody.innerHTML=d.candidates.map(c=>{
      const biz=c.business_name&&c.business_name!==c.name?`<div style="font-size:11px;color:var(--muted)">${esc(c.business_name)}</div>`:''
      const contact=[c.phone,c.email].filter(Boolean).join(' · ');
      const contactEl=contact?`<div style="font-size:11px;color:var(--muted)">${esc(contact)}</div>`:""
      const draftSnip=c.outreach_draft?`<div style="font-size:11px;color:var(--muted);max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(c.outreach_draft)}">${esc(c.outreach_draft.slice(0,60))}…</div>`:""
      const replyEl=c.reply_summary?`<div style="font-size:11px;color:var(--muted);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(c.reply_summary)}">${esc(c.reply_summary.slice(0,60))}…</div>`:(c.reply_text?'<span style="font-size:11px;color:var(--muted)">logged</span>':'')
      const overdue=c.follow_up_after&&c.follow_up_after<new Date().toISOString().slice(0,10);
      const dueEl=c.follow_up_after?`<span style="font-size:11px;color:${overdue?'var(--yellow)':'var(--muted)'}">${c.follow_up_after}</span>`:"";
      const cj=esc(JSON.stringify(c));
      // Action buttons
      const canDraft=['new','drafted'].includes(c.status);
      const canApprove=c.status==='drafted'&&c.outreach_draft;
      const canSent=['approved','drafted'].includes(c.status);
      const canReply=c.status==='sent';
      const canConvert=['replied','sent'].includes(c.status);
      const canPass=!['converted','passed'].includes(c.status);
      const actions=[
        canDraft?`<button class="btn btn-sm" onclick='openPilotDraft(JSON.parse(this.dataset.c))' data-c="${cj}">Draft</button>`:"",
        canApprove?`<button class="btn btn-sm" onclick='pilotAction(${c.id},"approve")'>Approve</button>`:"",
        canSent?`<button class="btn btn-sm" onclick='pilotAction(${c.id},"mark-sent")'>Sent</button>`:"",
        canReply?`<button class="btn btn-sm" onclick='openPilotReply(${c.id})'>Log Reply</button>`:"",
        canConvert?`<button class="btn btn-sm" onclick='pilotAction(${c.id},"convert")'>Convert</button>`:"",
        canPass?`<button class="btn btn-sm btn-danger" onclick='pilotAction(${c.id},"pass")'>Pass</button>`:"",
      ].filter(Boolean).join('');
      return `<tr style="border-bottom:1px solid var(--border)">
        <td style="padding:10px 10px"><span style="font-weight:600">${esc(c.name)}</span>${biz}${contactEl}</td>
        <td style="padding:10px 6px">${esc(c.service_type||'')}</td>
        <td style="padding:10px 6px;font-size:12px;color:var(--muted)">${esc(c.location||'')}</td>
        <td style="padding:10px 6px;text-align:center">${scoreBar(c.score)}</td>
        <td style="padding:10px 6px;text-align:center">${pilotBadge(c.status)}${draftSnip}</td>
        <td style="padding:10px 6px;font-size:11px;color:var(--muted)">${esc(c.source.replace('_',' '))}</td>
        <td style="padding:10px 6px">${dueEl}</td>
        <td style="padding:10px 6px">${replyEl}</td>
        <td style="padding:10px 6px;text-align:right;white-space:nowrap">${actions}</td>
      </tr>`;
    }).join('');
  }catch(e){document.getElementById('pilot-summary-bar').textContent='Error loading pilot data';}
}

function openPilotDraft(c){
  document.getElementById('pdraft-id').value=c.id;
  document.getElementById('pdraft-title').textContent='Draft — '+c.name;
  document.getElementById('pdraft-text').value=c.outreach_draft||'';
  document.getElementById('pdraft-err').style.display='none';
  document.getElementById('modal-pilot-draft').classList.add('open');
}

async function savePilotDraft(andApprove){
  const id=document.getElementById('pdraft-id').value;
  const text=document.getElementById('pdraft-text').value.trim();
  const errEl=document.getElementById('pdraft-err');
  if(!text){errEl.textContent='Draft cannot be empty.';errEl.style.display='';return;}
  const action=andApprove?'save-and-approve':'save-draft';
  const r=await fetch(`/api/pilot/${id}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({draft:text})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeOverlay('modal-pilot-draft');
  toast(andApprove?'Draft saved and approved.':'Draft saved.');
  loadPilot();
}

function openPilotReply(id){
  document.getElementById('preply-id').value=id;
  document.getElementById('preply-text').value='';
  document.getElementById('preply-err').style.display='none';
  document.getElementById('modal-pilot-reply').classList.add('open');
}

async function submitPilotReply(){
  const id=document.getElementById('preply-id').value;
  const text=document.getElementById('preply-text').value.trim();
  const errEl=document.getElementById('preply-err');
  if(!text){errEl.textContent='Reply text is required.';errEl.style.display='';return;}
  const r=await fetch(`/api/pilot/${id}/log-reply`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reply:text})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeOverlay('modal-pilot-reply');
  toast(j.summary?'Reply logged and summarized.':'Reply logged.');
  loadPilot();
}

async function pilotAction(id,action){
  const labels={approve:'Approve this draft for sending?','mark-sent':'Mark as sent?',convert:'Mark as converted pilot user?',pass:'Mark as passed?'};
  if(!confirm(labels[action]||action+'?'))return;
  const r=await fetch(`/api/pilot/${id}/${action}`,{method:'POST'});
  const j=await r.json();
  if(r.ok){toast(action==='convert'?'Converted! 🎉':action+' done.');loadPilot();}else{toast(j.error||'Error',true);}
}

load();
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

_ID_PATTERN = re.compile(r"^/api/leads/(\d+)/(\w+)$")
_PILOT_ID_PATTERN = re.compile(r"^/api/pilot/(\d+)/([\w-]+)$")
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _check_host(self) -> bool:
        """Reject requests with a Host header pointing at a non-local host (DNS rebinding guard)."""
        host_header = self.headers.get("Host", "")
        hostname = host_header.split(":")[0]
        if hostname and hostname not in _ALLOWED_HOSTS:
            self.send_json({"error": "Forbidden"}, 403)
            return False
        return True

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # No wildcard CORS — omit the header for localhost-only operation
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
        if not self._check_host():
            return
        path = urlparse(self.path).path
        if path in ("/", "/dashboard"):
            self.send_html(DASHBOARD_HTML)
        elif path == "/api/summary":
            try:
                self.send_json(api_summary())
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        elif path == "/api/closed":
            try:
                self.send_json(api_closed())
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        elif path == "/api/pilot" or path.startswith("/api/pilot?"):
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            status = (qs.get("status") or [None])[0]
            try:
                self.send_json(api_pilot_candidates(status))
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
        if not self._check_host():
            return
        path = urlparse(self.path).path
        try:
            body = self.read_json_body()
        except (json.JSONDecodeError, ValueError):
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        # Pilot routes
        if _PILOT_ID_PATTERN.match(path):
            self._handle_pilot_post(path, body)
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
            if email and not _valid_email(email):
                self.send_json({"error": "invalid email format"}, 400)
                return
            notes = (body.get("notes") or "").strip() or None
            if notes and len(notes) > MAX_FIELD_LENGTH:
                self.send_json({"error": f"notes max {MAX_FIELD_LENGTH} chars"}, 400)
                return
            try:
                followup_days = int(body.get("followup_days") or DEFAULT_FOLLOWUP_DAYS)
                if followup_days < 0:
                    followup_days = DEFAULT_FOLLOWUP_DAYS
            except (ValueError, TypeError):
                followup_days = DEFAULT_FOLLOWUP_DAYS
            lead_id, dupes = add_lead(name, service, phone=phone, email=email,
                                      notes=notes, followup_days=followup_days)
            resp = {"id": lead_id}
            if dupes:
                resp["duplicates"] = [{"id": d["id"], "name": d["name"]} for d in dupes]
            self.send_json(resp, 201)
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
                    if val is None:
                        continue
                    if field == "name" and len(val) > MAX_NAME_LENGTH:
                        self.send_json({"error": f"name max {MAX_NAME_LENGTH} chars"}, 400)
                        return
                    if field not in ("name",) and len(val) > MAX_FIELD_LENGTH:
                        self.send_json({"error": f"{field} max {MAX_FIELD_LENGTH} chars"}, 400)
                        return
                    if field == "email" and not _valid_email(val):
                        self.send_json({"error": "invalid email format"}, 400)
                        return
                    if field == "follow_up_after" and not _valid_date(val):
                        self.send_json({"error": "follow_up_after must be YYYY-MM-DD"}, 400)
                        return
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

    def _handle_pilot_post(self, path, body):
        """Pilot POST actions — called from do_POST after leads routing."""
        pm = _PILOT_ID_PATTERN.match(path)
        if not pm:
            return False
        cid, action = int(pm.group(1)), pm.group(2)
        candidate = _pilot.get_candidate_by_id(cid)
        if not candidate:
            self.send_json({"error": f"Candidate {cid} not found"}, 404)
            return True

        if action == "save-draft":
            draft = (body.get("draft") or "").strip()
            if not draft:
                self.send_json({"error": "draft is required"}, 400)
                return True
            _pilot.set_draft(cid, draft)
            self.send_json({"ok": True})

        elif action == "save-and-approve":
            draft = (body.get("draft") or "").strip()
            if not draft:
                self.send_json({"error": "draft is required"}, 400)
                return True
            _pilot.set_draft(cid, draft)
            _pilot.set_status(cid, "approved")
            self.send_json({"ok": True})

        elif action == "approve":
            if not candidate["outreach_draft"]:
                self.send_json({"error": "No draft to approve. Save a draft first."}, 400)
                return True
            _pilot.set_status(cid, "approved")
            self.send_json({"ok": True})

        elif action == "mark-sent":
            _pilot.set_status(cid, "sent", contacted=True)
            self.send_json({"ok": True})

        elif action == "log-reply":
            reply = (body.get("reply") or "").strip()
            if not reply:
                self.send_json({"error": "reply text is required"}, 400)
                return True
            _pilot.log_reply(cid, reply)
            # Try AI summary (non-blocking — skip if no key)
            summary = None
            try:
                from leadclaw.drafting import check_api_key, summarize_pilot_reply
                if check_api_key():
                    summary = summarize_pilot_reply(dict(candidate), reply)
                    if summary:
                        _pilot.set_reply_summary(cid, summary)
            except Exception:
                pass
            self.send_json({"ok": True, "summary": summary})

        elif action == "convert":
            _pilot.set_status(cid, "converted")
            self.send_json({"ok": True})

        elif action == "pass":
            _pilot.set_status(cid, "passed")
            self.send_json({"ok": True})

        else:
            self.send_json({"error": f"Unknown pilot action: {action}"}, 404)
        return True

    def do_OPTIONS(self):
        # Only allow same-origin preflight
        self.send_response(204)
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
        description="LeadClaw web dashboard",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind host (default: 127.0.0.1 — localhost only)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    if args.host == "0.0.0.0":
        print("WARNING: binding to 0.0.0.0 exposes unauthenticated write endpoints.")
        print("         Only do this on a trusted local network.")
        print("         For internet exposure, use a reverse proxy with auth.")

    server = HTTPServer((args.host, args.port), Handler)
    url = f"http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}"
    print(f"LeadClaw dashboard → {url}")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
