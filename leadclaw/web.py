"""
web.py - Multi-tenant Flask web dashboard with email/password auth

Entry point:
    leadclaw-web        # uses env vars PORT (default 7432), HOST (default 127.0.0.1)

Auth flow:
    POST /signup  → create account, send verification email (or print link in dev)
    GET  /verify/<token>  → mark email verified, log in, redirect to /
    POST /login   → check password + email_verified, create session
    GET  /logout  → clear session

All dashboard routes require @login_required AND email_verified.

Environment variables:
    LEADCLAW_SECRET_KEY  - Flask secret key (required for prod; fallback prints warning)
    LEADCLAW_DB          - DB path (default: data/leads.db)
    PORT                 - Bind port (default: 7432)
    HOST                 - Bind host (default: 127.0.0.1)
    SMTP_HOST            - SMTP server (omit to use stdout link in dev)
    SMTP_PORT            - SMTP port (default: 587)
    SMTP_USER            - SMTP username
    SMTP_PASS            - SMTP password
    APP_URL              - Public base URL (e.g. https://app.leadclaw.io)
"""

import json as _json
import os
import secrets
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText

import bcrypt
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)

import leadclaw.pilot as _pilot
from leadclaw.config import (
    DEFAULT_FOLLOWUP_DAYS,
    LOST_REASONS,
    MAX_FIELD_LENGTH,
    MAX_NAME_LENGTH,
)
from leadclaw.db import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_user_by_verify_token,
    init_db,
    verify_user_email,
)
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

# ---------------------------------------------------------------------------
# Flask app setup
# ---------------------------------------------------------------------------

_SECRET_KEY = os.environ.get("LEADCLAW_SECRET_KEY")
if not _SECRET_KEY:
    _SECRET_KEY = "dev-insecure-key-change-me"
    print(
        "WARNING: LEADCLAW_SECRET_KEY not set. Using insecure default. "
        "Set it in your environment before deploying.",
        file=sys.stderr,
    )

app = Flask(__name__)
app.secret_key = _SECRET_KEY

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Initialize DB at import time so gunicorn workers have schema ready
with app.app_context():
    init_db()

# ---------------------------------------------------------------------------
# User model for flask-login
# ---------------------------------------------------------------------------


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.email = row["email"]
        self.email_verified = bool(row["email_verified"])

    def get_id(self):
        return str(self.id)


@login_manager.user_loader
def load_user(user_id):
    row = get_user_by_id(int(user_id))
    if row is None:
        return None
    return User(row)


# ---------------------------------------------------------------------------
# Validation helpers
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
# Email verification helpers
# ---------------------------------------------------------------------------


def _send_verification_email(to_email: str, token: str):
    """Send verification email via Resend API, SMTP, or print link in dev."""
    app_url = os.environ.get("APP_URL", "http://localhost:7432").rstrip("/")
    link = f"{app_url}/verify/{token}"

    resend_key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if resend_key:
        import urllib.error
        import urllib.request

        payload = _json.dumps(
            {
                "from": "LeadClaw <noreply@morganlabs.org>",
                "to": [to_email],
                "subject": "Verify your LeadClaw account",
                "text": (
                    f"Click the link below to verify your LeadClaw account:\n\n{link}\n\n"
                    "If you didn't create this account, you can ignore this email."
                ),
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            method="POST",
            headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            print(f"[RESEND] Verification email sent to {to_email}", file=sys.stderr)
            return
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            print(f"WARNING: Resend failed {exc.code}: {body}", file=sys.stderr)
            print(f"[FALLBACK] Verification link for {to_email}: {link}", file=sys.stderr)
            return
        except Exception as exc:
            print(f"WARNING: Resend failed: {exc}", file=sys.stderr)
            print(f"[FALLBACK] Verification link for {to_email}: {link}", file=sys.stderr)
            return

    smtp_host = os.environ.get("SMTP_HOST")
    if not smtp_host:
        # Dev mode: just print the link
        print(f"\n[DEV] Email verification link for {to_email}:\n  {link}\n", file=sys.stderr)
        return

    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    msg = MIMEText(
        f"Click the link below to verify your LeadClaw account:\n\n{link}\n\n"
        "If you didn't create this account, you can ignore this email.",
        "plain",
    )
    msg["Subject"] = "Verify your LeadClaw account"
    msg["From"] = smtp_user
    msg["To"] = to_email

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [to_email], msg.as_string())
    except Exception as exc:
        print(f"WARNING: Failed to send verification email: {exc}", file=sys.stderr)
        print(f"[FALLBACK] Verification link for {to_email}: {link}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Auth page HTML templates
# ---------------------------------------------------------------------------

_AUTH_CSS = """
<style>
:root{--bg:#0f1117;--surface:#1a1d27;--border:#2a2d3a;--text:#e8eaf0;--muted:#6b7280;--accent:#6366f1;--accent-h:#4f52d1;--red:#ef4444;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;font-size:14px;
     display:flex;align-items:center;justify-content:center;min-height:100vh;}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:32px 36px;
      width:100%;max-width:400px;}
h1{font-size:22px;font-weight:700;margin-bottom:6px;}
.sub{color:var(--muted);font-size:13px;margin-bottom:24px;}
.form-group{margin-bottom:16px;}
label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px;}
input{width:100%;padding:9px 12px;background:#22263a;border:1px solid var(--border);border-radius:6px;
      color:var(--text);font-size:13px;font-family:inherit;outline:none;}
input:focus{border-color:var(--accent);}
.btn{display:block;width:100%;padding:10px;border-radius:6px;border:none;
     background:var(--accent);color:#fff;font-size:14px;font-weight:600;cursor:pointer;
     font-family:inherit;margin-top:8px;}
.btn:hover{background:var(--accent-h);}
.err{background:#3b0d0d;border:1px solid var(--red);color:#fca5a5;border-radius:6px;
     padding:9px 12px;font-size:13px;margin-bottom:16px;}
.link{text-align:center;margin-top:18px;font-size:12px;color:var(--muted);}
.link a{color:var(--accent);text-decoration:none;}
.link a:hover{text-decoration:underline;}
.info{background:#1e3a5f;border:1px solid #1d4ed8;color:#93c5fd;border-radius:6px;
      padding:9px 12px;font-size:13px;margin-bottom:16px;}
</style>
"""

LOGIN_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>LeadClaw — Sign In</title>" + _AUTH_CSS + "</head><body><div class='card'>"
    "<h1>🦞 LeadClaw</h1>"
    "<div class='sub'>Sign in to your account</div>"
    "{% if error %}<div class='err'>{{ error }}</div>{% endif %}"
    "{% if info %}<div class='info'>{{ info }}</div>{% endif %}"
    "<form method='post'>"
    "<div class='form-group'><label>Email</label>"
    "<input type='email' name='email' required autofocus value='{{ email|default(\"\") }}'></div>"
    "<div class='form-group'><label>Password</label>"
    "<input type='password' name='password' required></div>"
    "<button class='btn' type='submit'>Sign In</button>"
    "</form>"
    "<div class='link'>No account? <a href='/signup'>Create one</a></div>"
    "</div></body></html>"
)

SIGNUP_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>LeadClaw — Create Account</title>" + _AUTH_CSS + "</head><body><div class='card'>"
    "<h1>🦞 LeadClaw</h1>"
    "<div class='sub'>Create your account</div>"
    "{% if error %}<div class='err'>{{ error }}</div>{% endif %}"
    "<form method='post'>"
    "<div class='form-group'><label>Email</label>"
    "<input type='email' name='email' required autofocus value='{{ email|default(\"\") }}'></div>"
    "<div class='form-group'><label>Password</label>"
    "<input type='password' name='password' required></div>"
    "<div class='form-group'><label>Confirm Password</label>"
    "<input type='password' name='confirm' required></div>"
    "<button class='btn' type='submit'>Create Account</button>"
    "</form>"
    "<div class='link'>Already have an account? <a href='/login'>Sign in</a></div>"
    "</div></body></html>"
)

CHECK_EMAIL_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>LeadClaw — Verify Email</title>" + _AUTH_CSS + "</head><body><div class='card'>"
    "<h1>🦞 LeadClaw</h1>"
    "<div class='info' style='margin-top:16px'>"
    "📧 We sent a verification link to <strong>{{ email }}</strong>.<br><br>"
    "Click the link in the email to activate your account."
    "</div>"
    "<div class='link'>Wrong email? <a href='/signup'>Start over</a> &nbsp;·&nbsp; "
    "<a href='/login'>Sign in</a></div>"
    "</div></body></html>"
)

UNVERIFIED_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>LeadClaw — Verify Email</title>" + _AUTH_CSS + "</head><body><div class='card'>"
    "<h1>🦞 LeadClaw</h1>"
    "<div class='info' style='margin-top:16px'>"
    "📧 Please verify your email before accessing the dashboard.<br><br>"
    "Check your inbox for a verification link."
    "</div>"
    "<div class='link'><a href='/logout'>Sign out</a></div>"
    "</div></body></html>"
)

# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "GET":
        return render_template_string(SIGNUP_HTML)

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm") or ""

    if not email or not _valid_email(email):
        return render_template_string(
            SIGNUP_HTML, error="Enter a valid email address.", email=email
        )
    if len(password) < 8:
        return render_template_string(
            SIGNUP_HTML, error="Password must be at least 8 characters.", email=email
        )
    if password != confirm:
        return render_template_string(SIGNUP_HTML, error="Passwords do not match.", email=email)
    if get_user_by_email(email):
        return render_template_string(
            SIGNUP_HTML, error="An account with that email already exists.", email=email
        )

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    token = secrets.token_urlsafe(32)
    create_user(email, pw_hash, token)
    _send_verification_email(email, token)

    return render_template_string(CHECK_EMAIL_HTML, email=email)


@app.route("/verify/<token>")
def verify_email(token):
    row = get_user_by_verify_token(token)
    if not row:
        return render_template_string(
            LOGIN_HTML,
            error="Invalid or expired verification link.",
        )
    verify_user_email(row["id"])
    # Re-fetch so email_verified is set
    updated = get_user_by_id(row["id"])
    user = User(updated)
    login_user(user)
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    info = request.args.get("info")

    if request.method == "GET":
        return render_template_string(LOGIN_HTML, info=info)

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    row = get_user_by_email(email)
    if not row or not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return render_template_string(LOGIN_HTML, error="Invalid email or password.", email=email)

    user = User(row)
    login_user(user)
    return redirect(url_for("dashboard"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Verified-only decorator helper
# ---------------------------------------------------------------------------


def verified_required(f):
    """Wrap a view so it also requires email_verified."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.email_verified:
            return render_template_string(UNVERIFIED_HTML)
        return f(*args, **kwargs)

    return decorated


# ---------------------------------------------------------------------------
# JSON / data helpers
# ---------------------------------------------------------------------------

_LOST_REASONS_JS = _json.dumps(LOST_REASONS)
_MAX_NAME_JS = MAX_NAME_LENGTH
_MAX_FIELD_JS = MAX_FIELD_LENGTH


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
        "lost_reason_notes": row["lost_reason_notes"]
        if "lost_reason_notes" in row.keys()
        else None,
    }


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


def api_summary(user_id: int) -> dict:
    summary_rows, totals = get_pipeline_summary(user_id=user_id)
    today = [_lead_to_dict(r) for r in get_today_leads(user_id=user_id)]
    stale = [_lead_to_dict(r) for r in get_stale_leads(user_id=user_id)]
    active = [_lead_to_dict(r) for r in get_all_active_leads(user_id=user_id)]
    by_status = {
        row["status"]: {"count": row["count"], "total": row["total_quoted"]} for row in summary_rows
    }
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


def api_closed(user_id: int) -> dict:
    all_leads = get_all_leads(limit=10000, user_id=user_id)
    closed = [_lead_to_dict(r) for r in all_leads if r["status"] in ("won", "lost")]
    return {"closed": closed}


def api_pilot_candidates(user_id: int, status: str = None) -> dict:
    rows = _pilot.get_all_candidates(status=status or None, limit=500, user_id=user_id)
    summary = _pilot.get_pilot_summary(user_id=user_id)
    followups = _pilot.get_followup_due(user_id=user_id)
    return {
        "candidates": [_candidate_to_dict(r) for r in rows],
        "summary": summary,
        "followup_count": len(followups),
    }


# ---------------------------------------------------------------------------
# Dashboard HTML (injected with user email + sign-out link)
# ---------------------------------------------------------------------------


def _build_dashboard_html(user_email: str) -> str:
    """Return the full dashboard HTML with user email and signout link injected."""
    _html = (
        """<!DOCTYPE html>
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
  .header-user{margin-left:auto;display:flex;align-items:center;gap:12px;font-size:12px;color:var(--muted);}
  .header-user a{color:var(--muted);text-decoration:none;}
  .header-user a:hover{color:var(--text);}
  .btn{display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:none;color:var(--muted);cursor:pointer;font-size:12px;font-family:inherit;transition:all .15s;}
  .btn:hover{border-color:var(--accent);color:var(--accent);}
  .btn-primary{background:var(--accent);border-color:var(--accent);color:#fff;}
  .btn-primary:hover{background:var(--accent-h);border-color:var(--accent-h);color:#fff;}
  .btn-sm{padding:3px 8px;font-size:11px;}
  .btn-danger{color:var(--red)!important;}
  .btn-danger:hover{border-color:var(--red)!important;}
  .btn-active{border-color:var(--accent);color:var(--accent);}
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
  <div class="header-user">
    <span>"""
        + user_email
        + """</span>
    <a href="/logout">Sign out</a>
  </div>
  <button class="btn" onclick="load()">Refresh</button>
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
const LOST_REASONS=__LOST_REASONS_JS__;
const MAX_NAME=__MAX_NAME_JS__;
const MAX_FIELD=__MAX_FIELD_JS__"""
        + r""";

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

function validEmail(v){return v.includes('@')&&v.split('@').pop().includes('.');}
function validDate(v){return /^\d{4}-\d{2}-\d{2}$/.test(v)&&!isNaN(Date.parse(v));}

function switchTab(name){
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',['pipeline','closed','pilot'][i]===name));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.id==='tab-'+name));
  if(name==='closed')loadClosed();
  if(name==='pilot')loadPilot();
}

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

function closeModal(e){if(e.target===e.currentTarget)e.target.classList.remove('open');}
function closeOverlay(id){document.getElementById(id).classList.remove('open');}

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

async function doWon(id,name){
  if(!confirm(`Mark "${name}" as WON?`))return;
  const r=await fetch(`/api/leads/${id}/won`,{method:'POST'});
  if(r.ok){toast('Marked won! 🎉');load();}else{toast('Error',true);}
}

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
    const bs=d.summary.by_status||{};
    const parts=PILOT_STATUSES.filter(s=>bs[s]).map(s=>`${s}: ${bs[s]}`);
    document.getElementById('pilot-summary-bar').textContent=`${d.summary.total} total — `+parts.join(' · ');
    const fb=document.getElementById('pilot-followup-banner');
    if(d.followup_count>0){
      fb.textContent=`⚠ ${d.followup_count} candidate(s) overdue for follow-up`;
      fb.style.display='';
    }else{fb.style.display='none';}
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
    )
    return (
        _html.replace("__LOST_REASONS_JS__", _LOST_REASONS_JS)
        .replace("__MAX_NAME_JS__", str(_MAX_NAME_JS))
        .replace("__MAX_FIELD_JS__", str(_MAX_FIELD_JS))
    )


DASHBOARD_HTML = _build_dashboard_html("user@example.com")

# ---------------------------------------------------------------------------
# Dashboard routes
# ---------------------------------------------------------------------------


@app.route("/")
@login_required
@verified_required
def dashboard():
    return _build_dashboard_html(current_user.email)


@app.route("/api/summary")
@login_required
@verified_required
def route_api_summary():
    try:
        return jsonify(api_summary(current_user.id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/closed")
@login_required
@verified_required
def route_api_closed():
    try:
        return jsonify(api_closed(current_user.id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pilot")
@login_required
@verified_required
def route_api_pilot():
    status = request.args.get("status") or None
    try:
        return jsonify(api_pilot_candidates(current_user.id, status=status))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/leads/<int:lead_id>")
@login_required
@verified_required
def route_get_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if lead:
        return jsonify(_lead_to_dict(lead))
    return jsonify({"error": "Not found"}), 404


# ---------------------------------------------------------------------------
# Lead write routes
# ---------------------------------------------------------------------------


@app.route("/api/leads", methods=["POST"])
@login_required
@verified_required
def route_add_lead():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    service = (body.get("service") or "").strip()
    if not name or not service:
        return jsonify({"error": "name and service are required"}), 400
    if len(name) > MAX_NAME_LENGTH:
        return jsonify({"error": f"name max {MAX_NAME_LENGTH} chars"}), 400
    phone = (body.get("phone") or "").strip() or None
    email = (body.get("email") or "").strip() or None
    if email and not _valid_email(email):
        return jsonify({"error": "invalid email format"}), 400
    notes = (body.get("notes") or "").strip() or None
    if notes and len(notes) > MAX_FIELD_LENGTH:
        return jsonify({"error": f"notes max {MAX_FIELD_LENGTH} chars"}), 400
    try:
        followup_days = int(body.get("followup_days") or DEFAULT_FOLLOWUP_DAYS)
        if followup_days < 0:
            followup_days = DEFAULT_FOLLOWUP_DAYS
    except (ValueError, TypeError):
        followup_days = DEFAULT_FOLLOWUP_DAYS

    lead_id, dupes = add_lead(
        name,
        service,
        phone=phone,
        email=email,
        notes=notes,
        followup_days=followup_days,
        user_id=current_user.id,
    )
    resp = {"id": lead_id}
    if dupes:
        resp["duplicates"] = [{"id": d["id"], "name": d["name"]} for d in dupes]
    return jsonify(resp), 201


@app.route("/api/leads/<int:lead_id>/edit", methods=["POST"])
@login_required
@verified_required
def route_edit_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": f"Lead {lead_id} not found"}), 404

    body = request.get_json(silent=True) or {}
    fields = {}
    for field in ("name", "service", "phone", "email", "notes", "follow_up_after"):
        val = body.get(field)
        if val is not None:
            val = str(val).strip() or None
            if val is None:
                continue
            if field == "name" and len(val) > MAX_NAME_LENGTH:
                return jsonify({"error": f"name max {MAX_NAME_LENGTH} chars"}), 400
            if field not in ("name",) and len(val) > MAX_FIELD_LENGTH:
                return jsonify({"error": f"{field} max {MAX_FIELD_LENGTH} chars"}), 400
            if field == "email" and not _valid_email(val):
                return jsonify({"error": "invalid email format"}), 400
            if field == "follow_up_after" and not _valid_date(val):
                return jsonify({"error": "follow_up_after must be YYYY-MM-DD"}), 400
            fields[field] = val
    update_lead(lead_id, **fields)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:lead_id>/quote", methods=["POST"])
@login_required
@verified_required
def route_quote_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": f"Lead {lead_id} not found"}), 404
    body = request.get_json(silent=True) or {}
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be > 0"}), 400
    update_quote(lead_id, amount)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:lead_id>/won", methods=["POST"])
@login_required
@verified_required
def route_won_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": f"Lead {lead_id} not found"}), 404
    mark_won(lead_id)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:lead_id>/lost", methods=["POST"])
@login_required
@verified_required
def route_lost_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": f"Lead {lead_id} not found"}), 404
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    if reason not in LOST_REASONS:
        return jsonify({"error": f"reason must be one of: {', '.join(LOST_REASONS)}"}), 400
    notes = (body.get("notes") or "").strip() or None
    if reason == "other" and not notes:
        return jsonify({"error": "notes required when reason is 'other'"}), 400
    mark_lost(lead_id, reason, notes=notes)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:lead_id>/delete", methods=["POST"])
@login_required
@verified_required
def route_delete_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": f"Lead {lead_id} not found"}), 404
    delete_lead(lead_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Pilot write routes
# ---------------------------------------------------------------------------


def _get_pilot_candidate(cid: int):
    """Fetch candidate and verify ownership."""
    return _pilot.get_candidate_by_id(cid, user_id=current_user.id)


@app.route("/api/pilot/<int:cid>/save-draft", methods=["POST"])
@login_required
@verified_required
def route_pilot_save_draft(cid):
    candidate = _get_pilot_candidate(cid)
    if not candidate:
        return jsonify({"error": f"Candidate {cid} not found"}), 404
    body = request.get_json(silent=True) or {}
    draft = (body.get("draft") or "").strip()
    if not draft:
        return jsonify({"error": "draft is required"}), 400
    _pilot.set_draft(cid, draft)
    return jsonify({"ok": True})


@app.route("/api/pilot/<int:cid>/save-and-approve", methods=["POST"])
@login_required
@verified_required
def route_pilot_save_and_approve(cid):
    candidate = _get_pilot_candidate(cid)
    if not candidate:
        return jsonify({"error": f"Candidate {cid} not found"}), 404
    body = request.get_json(silent=True) or {}
    draft = (body.get("draft") or "").strip()
    if not draft:
        return jsonify({"error": "draft is required"}), 400
    _pilot.set_draft(cid, draft)
    _pilot.set_status(cid, "approved")
    return jsonify({"ok": True})


@app.route("/api/pilot/<int:cid>/approve", methods=["POST"])
@login_required
@verified_required
def route_pilot_approve(cid):
    candidate = _get_pilot_candidate(cid)
    if not candidate:
        return jsonify({"error": f"Candidate {cid} not found"}), 404
    if not candidate["outreach_draft"]:
        return jsonify({"error": "No draft to approve. Save a draft first."}), 400
    _pilot.set_status(cid, "approved")
    return jsonify({"ok": True})


@app.route("/api/pilot/<int:cid>/mark-sent", methods=["POST"])
@login_required
@verified_required
def route_pilot_mark_sent(cid):
    candidate = _get_pilot_candidate(cid)
    if not candidate:
        return jsonify({"error": f"Candidate {cid} not found"}), 404
    _pilot.set_status(cid, "sent", contacted=True)
    return jsonify({"ok": True})


@app.route("/api/pilot/<int:cid>/log-reply", methods=["POST"])
@login_required
@verified_required
def route_pilot_log_reply(cid):
    candidate = _get_pilot_candidate(cid)
    if not candidate:
        return jsonify({"error": f"Candidate {cid} not found"}), 404
    body = request.get_json(silent=True) or {}
    reply = (body.get("reply") or "").strip()
    if not reply:
        return jsonify({"error": "reply text is required"}), 400
    _pilot.log_reply(cid, reply)
    summary = None
    try:
        from leadclaw.drafting import check_api_key, summarize_pilot_reply

        if check_api_key():
            summary = summarize_pilot_reply(dict(candidate), reply)
            if summary:
                _pilot.set_reply_summary(cid, summary)
    except Exception:
        pass
    return jsonify({"ok": True, "summary": summary})


@app.route("/api/pilot/<int:cid>/convert", methods=["POST"])
@login_required
@verified_required
def route_pilot_convert(cid):
    candidate = _get_pilot_candidate(cid)
    if not candidate:
        return jsonify({"error": f"Candidate {cid} not found"}), 404
    _pilot.set_status(cid, "converted")
    return jsonify({"ok": True})


@app.route("/api/pilot/<int:cid>/pass", methods=["POST"])
@login_required
@verified_required
def route_pilot_pass(cid):
    candidate = _get_pilot_candidate(cid)
    if not candidate:
        return jsonify({"error": f"Candidate {cid} not found"}), 404
    _pilot.set_status(cid, "passed")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    import argparse

    init_db()
    parser = argparse.ArgumentParser(prog="leadclaw-web", description="LeadClaw web dashboard")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7432)))
    args = parser.parse_args()

    if args.host == "0.0.0.0":
        print("WARNING: binding to 0.0.0.0 — ensure this is behind a reverse proxy in production.")

    url = f"http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}"
    print(f"LeadClaw dashboard → {url}")
    print("Ctrl+C to stop.")
    try:
        app.run(host=args.host, port=args.port)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
