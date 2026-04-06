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

import html as _html
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
    DISMISSAL_FIELDS,
    add_lead,
    delete_lead,
    dismiss_reminder_standalone,
    get_all_active_leads,
    get_all_leads,
    get_event_counts,
    get_invoice_reminders,
    get_job_today_leads,
    get_lead_by_id,
    get_pipeline_summary,
    get_reactivation_leads,
    get_review_reminders,
    get_service_reminders,
    get_stale_leads,
    get_today_leads,
    mark_booked,
    mark_completed,
    mark_invoice_sent,
    mark_lost,
    mark_paid,
    mark_won,
    set_next_service,
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
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("APP_URL", "").startswith("https")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

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
      color:var(--text);font-size:16px;font-family:inherit;outline:none;}
input:focus{border-color:var(--accent);}
.btn{display:block;width:100%;padding:10px;border-radius:6px;border:none;
     background:var(--accent);color:#fff;font-size:15px;font-weight:600;cursor:pointer;
     font-family:inherit;margin-top:8px;min-height:48px;}
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
    uid = create_user(email, pw_hash, token)
    # Auto-verify on signup (email delivery blocked on shared IPs; re-enable later)
    verify_user_email(uid)
    row = get_user_by_id(uid)
    login_user(User(row))
    return redirect(url_for("dashboard"))


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
    def _safe_col(key, default=None):
        try:
            return row[key]
        except (IndexError, KeyError):
            return default

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
        "lost_reason_notes": _safe_col("lost_reason_notes"),
        "scheduled_date": str(_safe_col("scheduled_date"))[:10]
        if _safe_col("scheduled_date")
        else None,
        "booked_at": str(_safe_col("booked_at"))[:10] if _safe_col("booked_at") else None,
        "completed_at": str(_safe_col("completed_at"))[:10] if _safe_col("completed_at") else None,
        "invoice_amount": _safe_col("invoice_amount"),
        "invoice_sent_at": str(_safe_col("invoice_sent_at"))[:10]
        if _safe_col("invoice_sent_at")
        else None,
        "paid_at": str(_safe_col("paid_at"))[:10] if _safe_col("paid_at") else None,
        "next_service_due_at": str(_safe_col("next_service_due_at"))[:10]
        if _safe_col("next_service_due_at")
        else None,
        "invoice_reminder_at": str(_safe_col("invoice_reminder_at"))[:10]
        if _safe_col("invoice_reminder_at")
        else None,
        "service_reminder_at": str(_safe_col("service_reminder_at"))[:10]
        if _safe_col("service_reminder_at")
        else None,
        "review_reminder_at": str(_safe_col("review_reminder_at"))[:10]
        if _safe_col("review_reminder_at")
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
        "invoice_reminders": [_lead_to_dict(r) for r in get_invoice_reminders(user_id=user_id)],
        "service_reminders": [_lead_to_dict(r) for r in get_service_reminders(user_id=user_id)],
        "job_today": [_lead_to_dict(r) for r in get_job_today_leads(user_id=user_id)],
        "review_reminders": [_lead_to_dict(r) for r in get_review_reminders(user_id=user_id)],
        "reactivation_30": [_lead_to_dict(r) for r in get_reactivation_leads(30, user_id=user_id)],
        "reactivation_60": [_lead_to_dict(r) for r in get_reactivation_leads(60, user_id=user_id)],
        "reactivation_90": [_lead_to_dict(r) for r in get_reactivation_leads(90, user_id=user_id)],
    }


def api_closed(user_id: int) -> dict:
    all_leads = get_all_leads(limit=10000, user_id=user_id)
    # Treat 'won' as 'paid' for display purposes
    closed = [_lead_to_dict(r) for r in all_leads if r["status"] in ("won", "lost", "paid")]
    return {"closed": closed}


def api_usage() -> dict:
    last30 = get_event_counts(days=30)
    alltime = get_event_counts()
    return {
        "last_30_days": [{"event_type": r["event_type"], "count": r["count"]} for r in last30],
        "all_time": [{"event_type": r["event_type"], "count": r["count"]} for r in alltime],
    }


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
    _page = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#6366f1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="LeadClaw">
<link rel="manifest" href="/manifest.json">
<title>LeadClaw</title>
<style>
  :root{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2a2d3a;--text:#e8eaf0;--muted:#6b7280;--accent:#6366f1;--accent-h:#4f52d1;--green:#22c55e;--yellow:#f59e0b;--red:#ef4444;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;font-size:14px;line-height:1.5;
       padding-bottom:max(72px,calc(60px + env(safe-area-inset-bottom)));}
  header{padding:10px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;
         position:sticky;top:0;background:var(--bg);z-index:100;}
  header h1{font-size:17px;font-weight:700;letter-spacing:-.3px;}
  .header-actions{margin-left:auto;display:flex;align-items:center;gap:8px;}
  .header-email{font-size:11px;color:var(--muted);max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:5px;padding:6px 12px;
       border-radius:8px;border:1px solid var(--border);background:none;color:var(--muted);
       cursor:pointer;font-size:12px;font-family:inherit;transition:all .15s;min-height:36px;}
  .btn:hover{border-color:var(--accent);color:var(--accent);}
  .btn-primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600;}
  .btn-primary:hover{background:var(--accent-h);border-color:var(--accent-h);color:#fff;}
  .btn-sm{padding:3px 8px;font-size:11px;min-height:28px;}
  .btn-danger{color:var(--red)!important;}
  .btn-danger:hover{border-color:var(--red)!important;}
  .btn-add{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600;
           padding:6px 12px;font-size:13px;border-radius:8px;white-space:nowrap;min-height:36px;
           display:inline-flex;align-items:center;cursor:pointer;font-family:inherit;border:none;}
  .stats-bar{display:flex;gap:8px;padding:12px 16px;overflow-x:auto;scrollbar-width:none;
             border-bottom:1px solid var(--border);}
  .stats-bar::-webkit-scrollbar{display:none;}
  .stat-pill{display:flex;flex-direction:column;align-items:center;gap:2px;
             background:var(--surface);border:1px solid var(--border);border-radius:20px;
             padding:6px 14px;white-space:nowrap;flex-shrink:0;}
  .stat-val{font-size:15px;font-weight:700;}
  .stat-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;}
  .green{color:var(--green);}.yellow{color:var(--yellow);}.red{color:var(--red);}.accent{color:var(--accent);}
  .bottom-nav{position:fixed;bottom:0;left:0;right:0;height:60px;background:var(--surface);
              border-top:1px solid var(--border);display:flex;z-index:1000;
              padding-bottom:env(safe-area-inset-bottom);}
  .nav-item{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
            gap:2px;cursor:pointer;color:var(--muted);font-size:10px;font-weight:500;
            transition:color .15s;border:none;background:none;font-family:inherit;}
  .nav-item.active{color:var(--accent);}
  .nav-icon{font-size:20px;line-height:1;}
  .main{padding:0 0 8px;}
  .tab-panel{display:none;padding:12px 16px;}
  .tab-panel.active{display:block;}
  section{margin-bottom:20px;}
  section h2{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;
             letter-spacing:.6px;margin-bottom:10px;}
  .lead-list{display:flex;flex-direction:column;gap:10px;}
  .lead{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px;display:flex;gap:12px;align-items:flex-start;}
  .lead-card-body{flex:1;min-width:0;}
  .lead-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:4px;}
  .lead-alert{font-size:11px;color:var(--yellow);font-weight:600;display:flex;align-items:center;gap:4px;margin-bottom:2px;}
  .lead-name{font-size:17px;font-weight:600;margin-bottom:2px;}
  .lead-sub{font-size:13px;color:var(--muted);margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .lead-meta-row{font-size:12px;color:var(--muted);margin-bottom:10px;}
  .lead-primary-btn{display:block;width:100%;height:48px;background:var(--accent);
                    border:none;border-radius:10px;color:#fff;font-size:15px;font-weight:600;
                    cursor:pointer;font-family:inherit;margin-bottom:8px;}
  .lead-primary-btn:active{background:var(--accent-h);}
  .lead-secondary-row{display:flex;gap:6px;flex-wrap:wrap;}
  .lead-secondary-row .btn{height:36px;font-size:12px;flex:1;min-width:0;}
  .badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:10px;
         font-weight:600;text-transform:uppercase;letter-spacing:.4px;flex-shrink:0;}
  .badge-new{background:#1e3a5f;color:#60a5fa;}
  .badge-quoted{background:#2a1e5f;color:#a78bfa;}
  .badge-followup_due{background:#3b1f0a;color:#f59e0b;}
  .badge-won{background:#0d3321;color:#22c55e;}
  .badge-lost{background:#3b0d0d;color:#ef4444;}
  .badge-booked{background:#1a3a1a;color:#4ade80;}
  .badge-completed{background:#1a2a3a;color:#60a5fa;}
  .badge-paid{background:#1a3a2a;color:#34d399;}
  .empty{color:var(--muted);font-style:italic;padding:16px 0;text-align:center;}
  .warn-banner{background:#2a1a0a;border:1px solid #7c4a00;border-radius:8px;padding:10px 14px;
               font-size:12px;color:#f59e0b;margin-bottom:16px;}
  .sheet-overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:500;display:none;}
  .sheet-overlay.open{display:flex;align-items:flex-end;}
  .sheet{background:var(--surface);border-radius:16px 16px 0 0;
         padding:20px 20px calc(20px + env(safe-area-inset-bottom));
         width:100%;max-height:85vh;overflow-y:auto;}
  .sheet-handle{width:36px;height:4px;background:var(--border);border-radius:2px;margin:0 auto 16px;}
  .sheet h3{font-size:18px;font-weight:700;margin-bottom:20px;}
  .form-group{margin-bottom:14px;}
  .form-group label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px;}
  .form-group input,.form-group select,.form-group textarea{
    width:100%;padding:12px;background:var(--surface2);border:1px solid var(--border);
    border-radius:10px;color:var(--text);font-size:16px;font-family:inherit;outline:none;height:48px;}
  .form-group textarea{height:auto;resize:vertical;min-height:80px;font-size:14px;}
  .form-group input:focus,.form-group select:focus,.form-group textarea:focus{border-color:var(--accent);}
  .form-group select option{background:var(--surface2);}
  .sheet-footer{display:flex;gap:8px;justify-content:flex-end;margin-top:20px;}
  .sheet-primary-btn{display:block;width:100%;height:52px;background:var(--accent);
                     border:none;border-radius:12px;color:#fff;font-size:16px;font-weight:600;
                     cursor:pointer;font-family:inherit;margin-top:12px;}
  .sheet-primary-btn:active{background:var(--accent-h);}
  .err{color:var(--red);font-size:12px;margin-top:8px;display:none;}
  .toast{position:fixed;bottom:80px;left:50%;transform:translateX(-50%);
         background:var(--surface);border:1px solid var(--border);border-radius:20px;
         padding:8px 20px;font-size:13px;z-index:900;opacity:0;transition:opacity .2s;
         pointer-events:none;white-space:nowrap;}
  .toast.show{opacity:1;}
  .lead-desktop-actions{display:none;}
  .pilot-table-wrap{display:none!important;}
  .pilot-card-list{display:flex;flex-direction:column;gap:10px;}
  .pilot-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px;}
  .pilot-card-name{font-size:15px;font-weight:600;margin-bottom:2px;}
  .pilot-card-sub{font-size:12px;color:var(--muted);margin-bottom:8px;}
  .pilot-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;}
  .pilot-actions .btn{flex:1;min-width:0;font-size:11px;}
  .reminder-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px;}
  .reminder-card-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:6px;}
  .reminder-card-name{font-size:17px;font-weight:600;}
  .reminder-card-sub{font-size:13px;color:var(--muted);margin-bottom:10px;}
  .reminder-btns{display:flex;gap:8px;flex-wrap:wrap;}
  .reminder-btns .btn{flex:1;min-width:0;min-height:44px;}
  .btn-dismiss{color:var(--muted)!important;font-size:12px;min-height:40px!important;padding:6px 12px!important;}
  .usage-table{width:100%;border-collapse:collapse;font-size:14px;margin-top:8px;}
  .usage-table th{text-align:left;padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);border-bottom:1px solid var(--border);}
  .usage-table td{padding:10px;border-bottom:1px solid var(--border);}
  .usage-table tr:last-child td{border-bottom:none;}
  @media(min-width:768px){
    body{padding-bottom:0;}
    header{padding:14px 24px;}
    header h1{font-size:18px;}
    .header-email{max-width:none;}
    .bottom-nav{position:static;height:auto;border-top:none;border-bottom:1px solid var(--border);
                flex-direction:row;background:var(--bg);}
    .nav-item{flex-direction:row;gap:6px;padding:10px 18px;font-size:13px;flex:none;
              border-radius:0;height:44px;}
    .nav-icon{font-size:16px;}
    .nav-item.active{border-bottom:2px solid var(--accent);color:var(--accent);}
    .stats-bar{padding:16px 24px;gap:12px;}
    .stat-pill{padding:8px 18px;}
    .main{max-width:1140px;margin:0 auto;}
    .tab-panel{padding:24px;}
    .lead-primary-btn{display:none;}
    .lead-secondary-row{flex-wrap:nowrap;}
    .lead-secondary-row .btn{flex:none;}
    .lead-desktop-actions{display:flex!important;gap:6px;flex-shrink:0;align-items:flex-start;flex-wrap:wrap;justify-content:flex-end;}
    .sheet-overlay.open{align-items:center;justify-content:center;}
    .sheet{border-radius:12px;max-width:440px;padding:24px;}
    .sheet-handle{display:none;}
    .pilot-card-list{display:none!important;}
    .pilot-table-wrap{display:block!important;}
  }
</style>
</head>
<body>
<header>
  <h1>&#x1F99E; LeadClaw</h1>
  <div class="header-actions">
    <span class="header-email">USER_EMAIL_PLACEHOLDER</span>
    <button class="btn-add" onclick="openAdd()">+ Add Lead</button>
    <a href="/logout" style="font-size:11px;color:var(--muted);text-decoration:none;padding:4px;">Out</a>
  </div>
</header>
<div class="stats-bar" id="stats-bar"></div>
<nav class="bottom-nav">
  <button class="nav-item active" id="nav-today" onclick="switchTab('today')">
    <span class="nav-icon">&#x1F4CB;</span>Today
  </button>
  <button class="nav-item" id="nav-pipeline" onclick="switchTab('pipeline')">
    <span class="nav-icon">&#x1F500;</span>Pipeline
  </button>
  <button class="nav-item" id="nav-reminders" onclick="switchTab('reminders')" style="position:relative">
    <span class="nav-icon">&#x1F514;</span>Reminders
    <span id="reminders-badge" style="display:none;position:absolute;top:6px;right:calc(50% - 18px);background:var(--red);color:#fff;font-size:9px;font-weight:700;border-radius:8px;padding:1px 5px;"></span>
  </button>
  <button class="nav-item" id="nav-more" onclick="switchTab('more')">
    <span class="nav-icon">&#x2630;</span>More
  </button>
</nav>
<div class="main">
  <div class="tab-panel active" id="tab-today">
    <section><h2>Due Today</h2><div class="lead-list" id="today"></div></section>
    <section><h2>Needs Action (Overdue)</h2><div class="lead-list" id="stale"></div></section>
  </div>
  <div class="tab-panel" id="tab-pipeline">
    <section><h2>Active Pipeline</h2><div class="lead-list" id="active"></div></section>
  </div>
  <div class="tab-panel" id="tab-reminders">
    <section><h2>Jobs Today</h2><div class="lead-list" id="jobs-today"></div></section>
    <section><h2>Quote Follow-ups Overdue</h2><div class="lead-list" id="remind-stale"></div></section>
    <section><h2>Invoice Reminders</h2><div class="lead-list" id="invoice-reminders"></div></section>
    <section><h2>Review Requests</h2><div class="lead-list" id="review-reminders"></div></section>
    <section><h2>Recurring Service Due</h2><div class="lead-list" id="service-reminders"></div></section>
    <section><h2>Reactivation &mdash; 30&ndash;59 Days</h2><div class="lead-list" id="reactivation-30"></div></section>
    <section><h2>Reactivation &mdash; 60&ndash;89 Days</h2><div class="lead-list" id="reactivation-60"></div></section>
    <section><h2>Reactivation &mdash; 90+ Days</h2><div class="lead-list" id="reactivation-90"></div></section>
  </div>
  <div class="tab-panel" id="tab-more">
    <section><h2>Closed Leads</h2><div class="lead-list" id="closed"></div></section>
    <section>
      <h2>Pilot Tracker</h2>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap">
        <div id="pilot-summary-bar" style="color:var(--muted);font-size:12px"></div>
        <div style="margin-left:auto">
          <select id="pilot-filter" onchange="loadPilot()"
            style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;
                   color:var(--text);padding:6px 10px;font-size:14px;font-family:inherit;height:40px">
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
      <div class="pilot-card-list" id="pilot-card-list"></div>
    </section>
    <section>
      <h2>&#x1F4CA; Usage</h2>
      <div id="usage-section"><div class="empty">Loading...</div></div>
      <div class="pilot-table-wrap" id="pilot-table-wrap">
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
        <div id="pilot-empty" class="empty" style="display:none">No candidates.</div>
      </div>
    </section>
  </div>
</div>

<!-- Book sheet -->
<div class="sheet-overlay" id="sheet-book">
  <div class="sheet"><div class="sheet-handle"></div>
    <h3>Book Lead</h3>
    <input type="hidden" id="book-id">
    <div class="form-group"><label>Scheduled Date</label><input id="book-date" type="date"></div>
    <div class="err" id="book-err"></div>
    <div class="sheet-footer">
      <button class="btn" onclick="closeSheet('sheet-book')">Cancel</button>
      <button class="btn btn-primary" onclick="submitBook()">Book</button>
    </div>
  </div>
</div>

<!-- Invoice sheet -->
<div class="sheet-overlay" id="sheet-invoice">
  <div class="sheet"><div class="sheet-handle"></div>
    <h3>Send Invoice</h3>
    <input type="hidden" id="invoice-id">
    <div class="form-group"><label>Invoice Amount ($, blank = use quote)</label>
      <input id="invoice-amount" type="number" min="0.01" step="0.01" placeholder="e.g. 950"></div>
    <div class="err" id="invoice-err"></div>
    <button class="sheet-primary-btn" onclick="submitInvoice()">Record Invoice</button>
    <button class="btn" style="width:100%;margin-top:8px" onclick="closeSheet('sheet-invoice')">Cancel</button>
  </div>
</div>

<!-- Next Service sheet -->
<div class="sheet-overlay" id="sheet-nextsvc">
  <div class="sheet"><div class="sheet-handle"></div>
    <h3>Schedule Next Service</h3>
    <input type="hidden" id="nextsvc-id">
    <div class="form-group"><label>Next Service Date</label><input id="nextsvc-date" type="date"></div>
    <div class="err" id="nextsvc-err"></div>
    <button class="sheet-primary-btn" onclick="submitNextService()">Set Date</button>
    <button class="btn" style="width:100%;margin-top:8px" onclick="closeSheet('sheet-nextsvc')">Cancel</button>
  </div>
</div>

<!-- Pilot draft sheet -->
<div class="sheet-overlay" id="sheet-pilot-draft">
  <div class="sheet" style="max-width:520px"><div class="sheet-handle"></div>
    <h3 id="pdraft-title">Outreach Draft</h3>
    <input type="hidden" id="pdraft-id">
    <div class="form-group"><label>Draft text</label>
      <textarea id="pdraft-text" rows="6"></textarea></div>
    <div class="err" id="pdraft-err"></div>
    <div class="sheet-footer">
      <button class="btn" onclick="closeSheet('sheet-pilot-draft')">Cancel</button>
      <button class="btn" onclick="savePilotDraft(false)">Save only</button>
      <button class="btn btn-primary" onclick="savePilotDraft(true)">Save &amp; Approve</button>
    </div>
  </div>
</div>

<!-- Pilot reply sheet -->
<div class="sheet-overlay" id="sheet-pilot-reply">
  <div class="sheet" style="max-width:520px"><div class="sheet-handle"></div>
    <h3>Log Reply</h3>
    <input type="hidden" id="preply-id">
    <div class="form-group"><label>Paste their reply</label>
      <textarea id="preply-text" rows="5" placeholder="Their exact response..."></textarea></div>
    <div class="err" id="preply-err"></div>
    <button class="sheet-primary-btn" onclick="submitPilotReply()">Log &amp; Summarize</button>
    <button class="btn" style="width:100%;margin-top:8px" onclick="closeSheet('sheet-pilot-reply')">Cancel</button>
  </div>
</div>

<!-- Add/Edit sheet -->
<div class="sheet-overlay" id="sheet-edit">
  <div class="sheet"><div class="sheet-handle"></div>
    <h3 id="sheet-edit-title">Add Lead</h3>
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
    <button class="sheet-primary-btn" onclick="submitEdit()">Save</button>
    <button class="btn" style="width:100%;margin-top:8px" onclick="closeSheet('sheet-edit')">Cancel</button>
  </div>
</div>

<!-- Quote sheet -->
<div class="sheet-overlay" id="sheet-quote">
  <div class="sheet"><div class="sheet-handle"></div>
    <h3>Send Quote</h3>
    <input type="hidden" id="quote-id">
    <div class="form-group"><label>Quote Amount ($)</label><input id="quote-amount" type="number" min="1" placeholder="850"></div>
    <div class="err" id="quote-err"></div>
    <button class="sheet-primary-btn" onclick="submitQuote()">Set Quote</button>
    <button class="btn" style="width:100%;margin-top:8px" onclick="closeSheet('sheet-quote')">Cancel</button>
  </div>
</div>

<!-- Lost sheet -->
<div class="sheet-overlay" id="sheet-lost">
  <div class="sheet"><div class="sheet-handle"></div>
    <h3>Mark Lost</h3>
    <input type="hidden" id="lost-id">
    <div class="form-group"><label>Reason</label><select id="lost-reason"></select></div>
    <div class="form-group" id="lost-notes-group" style="display:none">
      <label>Notes (required for "other")</label>
      <textarea id="lost-notes" rows="2"></textarea>
    </div>
    <div class="err" id="lost-err"></div>
    <button class="sheet-primary-btn" style="background:var(--red)" onclick="submitLost()">Mark Lost</button>
    <button class="btn" style="width:100%;margin-top:8px" onclick="closeSheet('sheet-lost')">Cancel</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const LOST_REASONS=__LOST_REASONS_JS__;
const MAX_NAME=__MAX_NAME_JS__;
const MAX_FIELD=__MAX_FIELD_JS__;

(function(){
  const sel=document.getElementById('lost-reason');
  LOST_REASONS.forEach(r=>{const o=document.createElement('option');o.value=r;o.textContent=r.replace(/_/g,' ');sel.appendChild(o);});
  sel.addEventListener('change',()=>{document.getElementById('lost-notes-group').style.display=sel.value==='other'?'':'none';});
})();

function fmt(n){return n==null?'--':'$'+Number(n).toLocaleString(undefined,{maximumFractionDigits:0});}
function badge(s){return '<span class="badge badge-'+s+'">'+s.replace(/_/g,' ')+'</span>';}
function esc(s){return s?String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'):''}
function toast(msg,err){
  const t=document.getElementById('toast');
  t.textContent=msg;t.style.borderColor=err?'var(--red)':'var(--border)';
  t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2500);
}
function validEmail(v){return v.includes('@')&&v.split('@').pop().includes('.');}
function validDate(v){return /^\\d{4}-\\d{2}-\\d{2}$/.test(v)&&!isNaN(Date.parse(v));}

// Sheet open/close
function openSheet(id){document.getElementById(id).classList.add('open');document.body.style.overflow='hidden';}
function closeSheet(id){document.getElementById(id).classList.remove('open');document.body.style.overflow='';}
document.querySelectorAll('.sheet-overlay').forEach(el=>{
  el.addEventListener('click',e=>{if(e.target===el)closeSheet(el.id);});
});

// Tab switching
const _lastLoaded={};
const TAB_NAMES=['today','pipeline','reminders','more'];
function switchTab(name){
  TAB_NAMES.forEach(t=>{
    const nb=document.getElementById('nav-'+t);
    const tp=document.getElementById('tab-'+t);
    if(nb)nb.classList.toggle('active',t===name);
    if(tp)tp.classList.toggle('active',t===name);
  });
  const now=Date.now();
  const stale=!_lastLoaded[name]||now-_lastLoaded[name]>30000;
  if(stale){
    if(name==='today'||name==='pipeline'||name==='reminders')load();
    if(name==='more'){loadClosed();loadPilot();loadUsage();}
    _lastLoaded[name]=now;
  }
}

// Lead card rendering
function renderLead(l,opts){
  opts=opts||{};
  const showActions=opts.showActions!==false;
  const contact=[l.phone,l.email].filter(Boolean).join(' \u00b7 ');

  let alertHtml='';
  if(l.status==='followup_due')alertHtml='<div class="lead-alert">🔔 Follow-up Due</div>';
  else if(l.status==='new')alertHtml='<div class="lead-alert" style="color:var(--accent)">🆕 New Lead</div>';

  const metaParts=[];
  if(l.quote_amount)metaParts.push('Quote: '+fmt(l.quote_amount));
  if(l.invoice_amount)metaParts.push('Invoice: '+fmt(l.invoice_amount));
  if(l.scheduled_date)metaParts.push('Scheduled: '+l.scheduled_date);
  if(l.paid_at)metaParts.push('Paid: '+l.paid_at);
  if(l.next_service_due_at)metaParts.push('Next svc: '+l.next_service_due_at);
  if(l.follow_up_after)metaParts.push('Follow-up: '+l.follow_up_after);
  if(l.lost_reason)metaParts.push('Lost: '+l.lost_reason.replace(/_/g,' ')+(l.lost_reason_notes?' \u2014 '+l.lost_reason_notes:''));
  const metaRow=metaParts.length?'<div class="lead-meta-row">'+metaParts.map(m=>esc(m)).join(' \u00b7 ')+'</div>':'<div class="lead-meta-row"></div>';

  const notesHtml=l.notes?'<div style="font-size:12px;color:var(--muted);margin-bottom:8px">'+esc(l.notes)+'</div>':'';

  let primaryBtn='';
  let secondaryBtns=[];
  const lj=esc(JSON.stringify(l));

  if(showActions){
    if(l.status==='booked'){
      primaryBtn='<button class="lead-primary-btn" onclick="doComplete('+l.id+')">Mark Complete</button>';
      secondaryBtns.push('<button class="btn btn-danger" onclick="openLost('+l.id+')">Lost</button>');
    } else if(l.status==='completed'){
      if(!l.invoice_sent_at){
        primaryBtn='<button class="lead-primary-btn" onclick="openInvoice('+l.id+','+(l.quote_amount||0)+')">Send Invoice</button>';
      } else {
        primaryBtn='<button class="lead-primary-btn" onclick="doPaid('+l.id+')">Mark Paid</button>';
      }
      secondaryBtns.push('<button class="btn btn-danger" onclick="openLost('+l.id+')">Lost</button>');
    } else if(l.status==='paid'){
      primaryBtn='<button class="lead-primary-btn" onclick="openNextService('+l.id+')">Schedule Next Service</button>';
      secondaryBtns.push('<button class="btn btn-danger" onclick="doDelete('+l.id+',' + "'"+esc(l.name)+"'"+')">Delete</button>');
    } else if(l.status==='won'||l.status==='lost'){
      secondaryBtns.push('<button class="btn btn-danger" onclick="doDelete('+l.id+',' + "'"+esc(l.name)+"'"+')">Delete</button>');
    } else {
      primaryBtn='<button class="lead-primary-btn" onclick="openQuote('+l.id+')">Send Quote</button>';
      secondaryBtns.push('<button class="btn" onclick="doFollowUpTomorrow('+l.id+')">Follow Up Tomorrow</button>');
      secondaryBtns.push('<button class="btn" onclick="openBook('+l.id+')">Book</button>');
      secondaryBtns.push('<button class="btn" onclick="openEdit(JSON.parse(this.dataset.l))" data-l="'+lj+'">Edit</button>');
      secondaryBtns.push('<button class="btn btn-danger" onclick="openLost('+l.id+')">Lost</button>');
    }
  }

  // Desktop inline actions
  let desktopActions='';
  if(showActions){
    const da=[];
    if(['new','quoted','followup_due'].includes(l.status)){
      da.push('<button class="btn btn-sm btn-primary" onclick="openQuote('+l.id+')">Quote</button>');
      da.push('<button class="btn btn-sm" onclick="doFollowUpTomorrow('+l.id+')">Tmrw</button>');
      da.push('<button class="btn btn-sm" onclick="openBook('+l.id+')">Book</button>');
      da.push('<button class="btn btn-sm" onclick="openEdit(JSON.parse(this.dataset.l))" data-l="'+lj+'">Edit</button>');
      da.push('<button class="btn btn-sm btn-danger" onclick="openLost('+l.id+')">Lost</button>');
      da.push('<button class="btn btn-sm btn-danger" onclick="doDelete('+l.id+',' + "'"+esc(l.name)+"'"+')">Del</button>');
    } else if(l.status==='booked'){
      da.push('<button class="btn btn-sm btn-primary" onclick="doComplete('+l.id+')">Complete</button>');
      da.push('<button class="btn btn-sm btn-danger" onclick="openLost('+l.id+')">Lost</button>');
    } else if(l.status==='completed'){
      if(!l.invoice_sent_at)da.push('<button class="btn btn-sm btn-primary" onclick="openInvoice('+l.id+','+(l.quote_amount||0)+')">Invoice</button>');
      else da.push('<button class="btn btn-sm btn-primary" onclick="doPaid('+l.id+')">Mark Paid</button>');
      da.push('<button class="btn btn-sm btn-danger" onclick="openLost('+l.id+')">Lost</button>');
    } else if(l.status==='paid'){
      da.push('<button class="btn btn-sm" onclick="openNextService('+l.id+')">Next Svc</button>');
      da.push('<button class="btn btn-sm btn-danger" onclick="doDelete('+l.id+',' + "'"+esc(l.name)+"'"+')">Del</button>');
    } else if(l.status==='won'||l.status==='lost'){
      da.push('<button class="btn btn-sm btn-danger" onclick="doDelete('+l.id+',' + "'"+esc(l.name)+"'"+')">Del</button>');
    }
    desktopActions='<div class="lead-desktop-actions">'+da.join('')+'</div>';
  }

  const secondaryRow=secondaryBtns.length?'<div class="lead-secondary-row">'+secondaryBtns.join('')+'</div>':'';

  return '<div class="lead" data-id="'+l.id+'" data-status="'+l.status+'">'
    +'<div class="lead-card-body">'
    +'<div class="lead-header"><div>'+alertHtml+'<div class="lead-name">'+esc(l.name)+'</div></div>'+badge(l.status)+'</div>'
    +'<div class="lead-sub">'+esc(l.service||'')+(contact?' \u00b7 '+esc(contact):'')+'</div>'
    +notesHtml
    +metaRow
    +primaryBtn
    +secondaryRow
    +'</div>'
    +desktopActions
    +'</div>';
}

function renderList(id,leads,opts){
  opts=opts||{};
  const el=document.getElementById(id);
  if(!el)return;
  const emptyMsg=opts.emptyMsg||'None';
  el.innerHTML=leads.length?leads.map(function(l){return renderLead(l,opts);}).join(''):'<div class="empty">'+emptyMsg+'</div>';
}

// Data loading
async function load(){
  try{
    const d=await fetch('/api/summary').then(r=>r.json());
    const p=d.pipeline,b=p.by_status||{};
    const pills=[
      {val:fmt(p.open_value),lbl:'Pipeline',cls:'accent'},
      {val:fmt(p.won_value),lbl:'Paid',cls:'green'},
      {val:fmt(p.lost_value),lbl:'Lost',cls:'red'},
      {val:(b.followup_due||{count:0}).count,lbl:'Follow-up Due',cls:'yellow'},
      {val:(b.new||{count:0}).count,lbl:'New',cls:''},
      {val:(b.booked||{count:0}).count,lbl:'Booked',cls:''},
      {val:(b.completed||{count:0}).count,lbl:'Completed',cls:''},
    ];
    document.getElementById('stats-bar').innerHTML=pills.map(p=>
      '<div class="stat-pill"><span class="stat-val '+p.cls+'">'+p.val+'</span><span class="stat-lbl">'+p.lbl+'</span></div>'
    ).join('');
    renderList('today',d.today,{emptyMsg:"You\u2019re all caught up 🎉"});
    renderList('stale',d.stale,{emptyMsg:"You\u2019re all caught up 🎉"});
    renderList('active',d.active,{emptyMsg:'No active leads.'});
    const ir=d.invoice_reminders||[];
    const sr=d.service_reminders||[];

    // Jobs today with dismiss
    renderRemSection('jobs-today',d.job_today||[],[
      {label:'On My Way',type:'on_my_way',cls:'btn-primary'},
      {label:'Running Late',type:'running_late',cls:''}
    ],'No jobs today.','job_today');

    // Invoice reminders
    renderList('invoice-reminders',ir,{emptyMsg:'No overdue invoices.'});

    // Review requests with dismiss
    renderRemSection('review-reminders',d.review_reminders||[],[
      {label:'Copy Review Request',type:'review_request',cls:'btn-primary'}
    ],'No review requests due.','review_request');

    // Recurring service
    renderList('service-reminders',sr,{emptyMsg:'No recurring service due.'});

    // Reactivation buckets with dismiss
    renderRemSection('reactivation-30',d.reactivation_30||[],[
      {label:'Copy Message',type:'reactivation_30',cls:''}
    ],'None.','reactivation');
    renderRemSection('reactivation-60',d.reactivation_60||[],[
      {label:'Copy Message',type:'reactivation_60',cls:''}
    ],'None.','reactivation');
    renderRemSection('reactivation-90',d.reactivation_90||[],[
      {label:'Copy Message',type:'reactivation_90',cls:''}
    ],'None.','reactivation');

    // Reminders badge
    const reminderCount=(d.job_today||[]).length+(ir||[]).length+(d.review_reminders||[]).length
      +(sr||[]).length+(d.reactivation_30||[]).length+(d.reactivation_60||[]).length+(d.reactivation_90||[]).length;
    const rem_badge=document.getElementById('reminders-badge');
    if(rem_badge){rem_badge.textContent=reminderCount>0?String(reminderCount):'';rem_badge.style.display=reminderCount>0?'':'none';}

    _lastLoaded['today']=_lastLoaded['pipeline']=_lastLoaded['reminders']=Date.now();
  }catch(e){console.error(e);}
}

// Message templates (mirrors Python draft_message — deterministic, no API call)
function draftMessage(lead,msgType){
  const name=(lead.name||'there').split(' ')[0];
  const service=lead.service||'the job';
  const scheduled=lead.scheduled_date||'your scheduled date';
  const quoteAmt=lead.quote_amount?'$'+Number(lead.quote_amount).toLocaleString(undefined,{maximumFractionDigits:0}):'';
  const templates={
    quote_followup:'Hey '+name+', just wanted to follow up on the quote'+(service!=='the job'?' for '+service:'')+(quoteAmt?' ('+quoteAmt+')':'')+'. Do you have any questions or want to get scheduled?',
    booking_confirmation:'Hey '+name+', confirming your '+service+' appointment'+(scheduled!=='your scheduled date'?' on '+scheduled:'')+'. Let me know if you need to reschedule. Looking forward to it!',
    on_my_way:'Hey '+name+", I'm on my way for "+service+' today. See you soon!',
    running_late:'Hey '+name+", heads up \u2014 I'm running about 15 minutes behind for "+service+" today. I'll be there shortly, thanks for your patience!",
    review_request:'Hey '+name+', thanks for choosing us for '+service+'! If you were happy with the work, a quick Google review would mean a lot. Here\'s the link: [YOUR REVIEW LINK]',
    reactivation_30:'Hey '+name+', just checking in \u2014 still interested in '+service+'? Happy to get you scheduled if the timing works.',
    reactivation_60:'Hey '+name+", it's been a little while \u2014 wanted to see if you're still thinking about "+service+'. No pressure, just here if you need us.',
    reactivation_90:'Hey '+name+', hope all is well! Reaching out one more time about '+service+". If you've found someone else, totally understand \u2014 just let me know either way.",
  };
  return templates[msgType]||'';
}

async function copyMessage(lead,msgType){
  const msg=draftMessage(lead,msgType);
  if(!msg){toast('Unknown message type',true);return;}
  try{
    await navigator.clipboard.writeText(msg);
    toast('Message copied!');
  }catch(e){
    prompt('Copy this message:',msg);
  }
}

// Dismiss a reminder via API
async function dismissReminder(leadId,reminderType,el){
  try{
    const r=await fetch('/api/reminders/dismiss',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({lead_id:leadId,reminder_type:reminderType})
    });
    if(r.ok){
      const card=el.closest('[data-id]');
      if(card){card.style.transition='opacity .3s';card.style.opacity='0.3';card.style.pointerEvents='none';}
      toast('Reminder dismissed.');
      setTimeout(()=>load(),700);
    }else{toast('Error dismissing',true);}
  }catch(e){toast('Error',true);}
}

// Render a reminders section with copy-message + optional dismiss buttons
function renderRemSection(containerId,leads,buttons,emptyMsg,dismissType){
  const el=document.getElementById(containerId);
  if(!el)return;
  if(!leads||!leads.length){el.innerHTML='<div class="empty">'+(emptyMsg||'None.')+'</div>';return;}
  el.innerHTML=leads.map(function(l){
    const lj=esc(JSON.stringify(l));
    const btns=buttons.map(function(b){
      const cls=b.cls?(' '+b.cls):'';
      return '<button class="btn btn-sm'+cls+'" onclick="copyMessage(JSON.parse(this.dataset.l),\''+b.type+'\')" data-l="'+lj+'">'+esc(b.label)+'</button>';
    }).join('');
    const dismissBtn=dismissType
      ?'<button class="btn btn-sm btn-dismiss" onclick="dismissReminder('+l.id+',\''+dismissType+'\',this)">Dismiss</button>'
      :'';
    const metaParts=[];
    if(l.scheduled_date)metaParts.push('Scheduled: '+l.scheduled_date);
    if(l.next_service_due_at)metaParts.push('Next svc: '+l.next_service_due_at);
    const metaHtml=metaParts.length?'<div style="font-size:13px;color:var(--muted);margin-bottom:8px">'+metaParts.map(function(m){return esc(m);}).join(' \u00b7 ')+'</div>':'';
    return '<div class="reminder-card" data-id="'+l.id+'" data-status="'+l.status+'">'
      +'<div class="reminder-card-header"><div>'
      +'<div class="reminder-card-name">'+esc(l.name)+'</div>'
      +'<div class="reminder-card-sub">'+esc(l.service||'')+(l.phone?' \u00b7 '+esc(l.phone):'')+'</div>'
      +'</div>'+badge(l.status)+'</div>'
      +metaHtml
      +'<div class="reminder-btns">'+btns+dismissBtn+'</div>'
      +'</div>';
  }).join('');
}

async function loadClosed(){
  try{
    const d=await fetch('/api/closed').then(r=>r.json());
    renderList('closed',d.closed,{emptyMsg:'No closed leads yet.'});
  }catch(e){document.getElementById('closed').innerHTML='<div class="empty">Error loading.</div>';}
}

async function loadUsage(){
  const el=document.getElementById('usage-section');
  if(!el)return;
  try{
    const d=await fetch('/api/usage').then(r=>r.json());
    const last30=d.last_30_days||[];
    const alltime=d.all_time||[];
    const atMap={};alltime.forEach(function(r){atMap[r.event_type]=r.count;});
    const l30Map={};last30.forEach(function(r){l30Map[r.event_type]=r.count;});
    const typeArr=[...new Set([...alltime.map(function(r){return r.event_type;}),...last30.map(function(r){return r.event_type;})])].sort();
    if(!typeArr.length){el.innerHTML='<div class="empty">No events yet.</div>';return;}
    let html='<table class="usage-table"><thead><tr><th>Event</th><th>Last 30 Days</th><th>All Time</th></tr></thead><tbody>';
    typeArr.forEach(function(t){
      html+='<tr><td>'+esc(t.replace(/_/g,' '))+'</td><td>'+(l30Map[t]||0)+'</td><td>'+(atMap[t]||0)+'</td></tr>';
    });
    html+='</tbody></table>';
    el.innerHTML=html;
  }catch(e){el.innerHTML='<div class="empty">Error loading usage.</div>';}
}

// Follow Up Tomorrow
async function doFollowUpTomorrow(id){
  const tomorrow=new Date();
  tomorrow.setDate(tomorrow.getDate()+1);
  const dateStr=tomorrow.toISOString().slice(0,10);
  const r=await fetch('/api/leads/'+id+'/edit',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({follow_up_after:dateStr})
  });
  if(r.ok){toast('Follow-up set for tomorrow.');load();}
  else{toast('Error',true);}
}

// Add/Edit
function openAdd(){
  document.getElementById('sheet-edit-title').textContent='Add Lead';
  document.getElementById('edit-id').value='';
  document.getElementById('dup-warn').style.display='none';
  ['edit-name','edit-service','edit-phone','edit-email','edit-notes'].forEach(function(id){document.getElementById(id).value='';});
  document.getElementById('edit-followup').value='3';
  document.getElementById('fg-followup').style.display='';
  document.getElementById('fg-followup-date').style.display='none';
  document.getElementById('edit-err').style.display='none';
  openSheet('sheet-edit');
}
function openEdit(l){
  document.getElementById('sheet-edit-title').textContent='Edit Lead';
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
  openSheet('sheet-edit');
}
async function submitEdit(){
  const id=document.getElementById('edit-id').value;
  const name=document.getElementById('edit-name').value.trim();
  const service=document.getElementById('edit-service').value.trim();
  const email=document.getElementById('edit-email').value.trim();
  const followupDate=document.getElementById('edit-followup-date').value;
  const errEl=document.getElementById('edit-err');
  if(!name||!service){errEl.textContent='Name and service are required.';errEl.style.display='';return;}
  if(name.length>MAX_NAME){errEl.textContent='Name max '+MAX_NAME+' chars.';errEl.style.display='';return;}
  if(email&&!validEmail(email)){errEl.textContent='Invalid email format.';errEl.style.display='';return;}
  if(id&&followupDate&&!validDate(followupDate)){errEl.textContent='Follow-up date must be YYYY-MM-DD.';errEl.style.display='';return;}
  const body={name,service,
    phone:document.getElementById('edit-phone').value.trim()||null,
    email:email||null,
    notes:document.getElementById('edit-notes').value.trim()||null,
  };
  if(!id){body.followup_days=parseInt(document.getElementById('edit-followup').value)||3;}
  else{body.follow_up_after=followupDate||null;}
  const url=id?'/api/leads/'+id+'/edit':'/api/leads';
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  if(!id&&j.duplicates&&j.duplicates.length){
    const w=document.getElementById('dup-warn');
    w.textContent='\u26a0 '+j.duplicates.length+' existing lead(s) with the same name: '+j.duplicates.map(d=>d.name).join(', ');
    w.style.display='';
  } else {
    closeSheet('sheet-edit');
    toast(id?'Lead updated.':'Lead added.');
    load();
  }
}

// Quote
function openQuote(id){
  document.getElementById('quote-id').value=id;
  document.getElementById('quote-amount').value='';
  document.getElementById('quote-err').style.display='none';
  openSheet('sheet-quote');
}
async function submitQuote(){
  const id=document.getElementById('quote-id').value;
  const amount=parseFloat(document.getElementById('quote-amount').value);
  const errEl=document.getElementById('quote-err');
  if(!amount||amount<=0){errEl.textContent='Enter a valid amount > 0.';errEl.style.display='';return;}
  const r=await fetch('/api/leads/'+id+'/quote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({amount})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeSheet('sheet-quote');toast('Quote set.');load();
}

// Book
function openBook(id){
  document.getElementById('book-id').value=id;
  document.getElementById('book-date').value='';
  document.getElementById('book-err').style.display='none';
  openSheet('sheet-book');
}
async function submitBook(){
  const id=document.getElementById('book-id').value;
  const date=document.getElementById('book-date').value;
  const errEl=document.getElementById('book-err');
  if(!date||!validDate(date)){errEl.textContent='Enter a valid date (YYYY-MM-DD).';errEl.style.display='';return;}
  const r=await fetch('/api/leads/'+id+'/book',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scheduled_date:date})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeSheet('sheet-book');toast('Lead booked!');load();
}

// Complete
async function doComplete(id){
  const r=await fetch('/api/leads/'+id+'/complete',{method:'POST'});
  if(r.ok){toast('Marked completed.');load();}else{toast('Error',true);}
}

// Invoice
function openInvoice(id,defaultAmount){
  document.getElementById('invoice-id').value=id;
  document.getElementById('invoice-amount').value=defaultAmount||'';
  document.getElementById('invoice-err').style.display='none';
  openSheet('sheet-invoice');
}
async function submitInvoice(){
  const id=document.getElementById('invoice-id').value;
  const amount=document.getElementById('invoice-amount').value;
  const errEl=document.getElementById('invoice-err');
  const body={};
  if(amount){
    const a=parseFloat(amount);
    if(isNaN(a)||a<=0){errEl.textContent='Amount must be > 0.';errEl.style.display='';return;}
    body.invoice_amount=a;
  }
  const r=await fetch('/api/leads/'+id+'/invoice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeSheet('sheet-invoice');toast('Invoice recorded.');load();
}

// Paid
async function doPaid(id){
  const r=await fetch('/api/leads/'+id+'/paid',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
  if(r.ok){toast('Marked paid! 🎉');load();}else{toast('Error',true);}
}

// Lost
function openLost(id){
  document.getElementById('lost-id').value=id;
  document.getElementById('lost-reason').value=LOST_REASONS[0];
  document.getElementById('lost-notes').value='';
  document.getElementById('lost-notes-group').style.display='none';
  document.getElementById('lost-err').style.display='none';
  openSheet('sheet-lost');
}
async function submitLost(){
  const id=document.getElementById('lost-id').value;
  const reason=document.getElementById('lost-reason').value;
  const notes=document.getElementById('lost-notes').value.trim();
  const errEl=document.getElementById('lost-err');
  if(reason==='other'&&!notes){errEl.textContent='Notes required for "other".';errEl.style.display='';return;}
  const r=await fetch('/api/leads/'+id+'/lost',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason,notes:notes||null})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeSheet('sheet-lost');toast('Marked lost.');load();
}

// Delete
async function doDelete(id,name){
  if(!confirm('Delete "'+name+'"? This cannot be undone.'))return;
  const r=await fetch('/api/leads/'+id+'/delete',{method:'POST'});
  if(r.ok){toast('Deleted.');load();}else{toast('Error',true);}
}

// Next Service
function openNextService(id){
  document.getElementById('nextsvc-id').value=id;
  document.getElementById('nextsvc-date').value='';
  document.getElementById('nextsvc-err').style.display='none';
  openSheet('sheet-nextsvc');
}
async function submitNextService(){
  const id=document.getElementById('nextsvc-id').value;
  const date=document.getElementById('nextsvc-date').value;
  const errEl=document.getElementById('nextsvc-err');
  if(!date||!validDate(date)){errEl.textContent='Enter a valid date (YYYY-MM-DD).';errEl.style.display='';return;}
  const r=await fetch('/api/leads/'+id+'/next-service',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({next_service_due_at:date})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeSheet('sheet-nextsvc');toast('Next service date set.');load();
}

// Pilot tracker
const PILOT_STATUSES=['new','drafted','approved','sent','replied','converted','passed'];
const PILOT_STATUS_COLORS={new:'#60a5fa',drafted:'#a78bfa',approved:'#34d399',sent:'#f59e0b',replied:'#fb923c',converted:'#22c55e',passed:'#6b7280'};

function pilotBadge(s){
  const c=PILOT_STATUS_COLORS[s]||'#9ca3af';
  return '<span style="display:inline-block;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:600;text-transform:uppercase;background:'+c+'22;color:'+c+'">'+s+'</span>';
}
function scoreBar(n){
  const c=n>=80?'var(--green)':n>=60?'var(--yellow)':'var(--red)';
  return '<div style="display:flex;align-items:center;gap:5px"><div style="width:40px;height:5px;background:var(--border);border-radius:3px;overflow:hidden"><div style="width:'+n+'%;height:100%;background:'+c+'"></div></div><span style="font-size:11px;color:'+c+'">'+n+'</span></div>';
}

async function loadPilot(){
  const status=document.getElementById('pilot-filter').value;
  const url='/api/pilot'+(status?'?status='+encodeURIComponent(status):'');
  try{
    const d=await fetch(url).then(r=>r.json());
    const bs=d.summary.by_status||{};
    const parts=PILOT_STATUSES.filter(s=>bs[s]).map(s=>s+': '+bs[s]);
    document.getElementById('pilot-summary-bar').textContent=d.summary.total+' total \u2014 '+parts.join(' \u00b7 ');
    const fb=document.getElementById('pilot-followup-banner');
    if(d.followup_count>0){fb.textContent='\u26a0 '+d.followup_count+' candidate(s) overdue for follow-up';fb.style.display='';}
    else{fb.style.display='none';}
    // Mobile cards
    const cardList=document.getElementById('pilot-card-list');
    if(!d.candidates.length){
      cardList.innerHTML='<div class="empty">No candidates.</div>';
    } else {
      cardList.innerHTML=d.candidates.map(function(c){
        const biz=c.business_name&&c.business_name!==c.name?' \u00b7 '+esc(c.business_name):'';
        const contact=[c.phone,c.email].filter(Boolean).join(' \u00b7 ');
        const cj=esc(JSON.stringify(c));
        const canDraft=['new','drafted'].includes(c.status);
        const canApprove=c.status==='drafted'&&c.outreach_draft;
        const canSent=['approved','drafted'].includes(c.status);
        const canReply=c.status==='sent';
        const canConvert=['replied','sent'].includes(c.status);
        const canPass=!['converted','passed'].includes(c.status);
        const actions=[
          canDraft?'<button class="btn" onclick="openPilotDraft(JSON.parse(this.dataset.c))" data-c="'+cj+'">Draft</button>':'',
          canApprove?'<button class="btn" onclick="pilotAction('+c.id+',' + '"approve"' + ')">Approve</button>':'',
          canSent?'<button class="btn" onclick="pilotAction('+c.id+',' + '"mark-sent"' + ')">Sent</button>':'',
          canReply?'<button class="btn" onclick="openPilotReply('+c.id+')">Log Reply</button>':'',
          canConvert?'<button class="btn" onclick="pilotAction('+c.id+',' + '"convert"' + ')">Convert</button>':'',
          canPass?'<button class="btn btn-danger" onclick="pilotAction('+c.id+',' + '"pass"' + ')">Pass</button>':'',
        ].filter(Boolean).join('');
        const overdue=c.follow_up_after&&c.follow_up_after<new Date().toISOString().slice(0,10);
        return '<div class="pilot-card">'
          +'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px">'
          +'<div><div class="pilot-card-name">'+esc(c.name)+biz+'</div>'
          +'<div class="pilot-card-sub">'+esc(c.service_type||'')+(c.location?' \u00b7 '+esc(c.location):'')+'</div></div>'
          +pilotBadge(c.status)+'</div>'
          +(contact?'<div style="font-size:12px;color:var(--muted);margin-bottom:4px">'+esc(contact)+'</div>':'')
          +scoreBar(c.score)
          +(c.follow_up_after?'<div style="font-size:11px;color:'+(overdue?'var(--yellow)':'var(--muted)')+';margin-top:4px">Follow-up: '+c.follow_up_after+'</div>':'')
          +(c.reply_summary?'<div style="font-size:11px;color:var(--muted);margin-top:4px">'+esc(c.reply_summary.slice(0,80))+'</div>':'')
          +'<div class="pilot-actions">'+actions+'</div>'
          +'</div>';
      }).join('');
    }
    // Desktop table
    const tbody=document.getElementById('pilot-tbody');
    const empty=document.getElementById('pilot-empty');
    if(!d.candidates.length){tbody.innerHTML='';empty.style.display='';}
    else{
      empty.style.display='none';
      tbody.innerHTML=d.candidates.map(function(c){
        const biz=c.business_name&&c.business_name!==c.name?'<div style="font-size:11px;color:var(--muted)">'+esc(c.business_name)+'</div>':'';
        const contact=[c.phone,c.email].filter(Boolean).join(' \u00b7 ');
        const contactEl=contact?'<div style="font-size:11px;color:var(--muted)">'+esc(contact)+'</div>':'';
        const draftSnip=c.outreach_draft?'<div style="font-size:11px;color:var(--muted);max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+esc(c.outreach_draft)+'">'+esc(c.outreach_draft.slice(0,60))+'&#x2026;</div>':'';
        const replyEl=c.reply_summary?'<div style="font-size:11px;color:var(--muted);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(c.reply_summary.slice(0,60))+'&#x2026;</div>':(c.reply_text?'<span style="font-size:11px;color:var(--muted)">logged</span>':'');
        const overdue=c.follow_up_after&&c.follow_up_after<new Date().toISOString().slice(0,10);
        const dueEl=c.follow_up_after?'<span style="font-size:11px;color:'+(overdue?'var(--yellow)':'var(--muted)')+'">'+c.follow_up_after+'</span>':'';
        const cj=esc(JSON.stringify(c));
        const canDraft=['new','drafted'].includes(c.status);
        const canApprove=c.status==='drafted'&&c.outreach_draft;
        const canSent=['approved','drafted'].includes(c.status);
        const canReply=c.status==='sent';
        const canConvert=['replied','sent'].includes(c.status);
        const canPass=!['converted','passed'].includes(c.status);
        const actions=[
          canDraft?'<button class="btn btn-sm" onclick="openPilotDraft(JSON.parse(this.dataset.c))" data-c="'+cj+'">Draft</button>':'',
          canApprove?'<button class="btn btn-sm" onclick="pilotAction('+c.id+',' + '"approve"' + ')">Approve</button>':'',
          canSent?'<button class="btn btn-sm" onclick="pilotAction('+c.id+',' + '"mark-sent"' + ')">Sent</button>':'',
          canReply?'<button class="btn btn-sm" onclick="openPilotReply('+c.id+')">Log Reply</button>':'',
          canConvert?'<button class="btn btn-sm" onclick="pilotAction('+c.id+',' + '"convert"' + ')">Convert</button>':'',
          canPass?'<button class="btn btn-sm btn-danger" onclick="pilotAction('+c.id+',' + '"pass"' + ')">Pass</button>':'',
        ].filter(Boolean).join('');
        return '<tr style="border-bottom:1px solid var(--border)">'
          +'<td style="padding:10px 10px"><span style="font-weight:600">'+esc(c.name)+'</span>'+biz+contactEl+'</td>'
          +'<td style="padding:10px 6px">'+esc(c.service_type||'')+'</td>'
          +'<td style="padding:10px 6px;font-size:12px;color:var(--muted)">'+esc(c.location||'')+'</td>'
          +'<td style="padding:10px 6px;text-align:center">'+scoreBar(c.score)+'</td>'
          +'<td style="padding:10px 6px;text-align:center">'+pilotBadge(c.status)+draftSnip+'</td>'
          +'<td style="padding:10px 6px;font-size:11px;color:var(--muted)">'+esc(c.source.replace('_',' '))+'</td>'
          +'<td style="padding:10px 6px">'+dueEl+'</td>'
          +'<td style="padding:10px 6px">'+replyEl+'</td>'
          +'<td style="padding:10px 6px;text-align:right;white-space:nowrap">'+actions+'</td>'
          +'</tr>';
      }).join('');
    }
  }catch(e){document.getElementById('pilot-summary-bar').textContent='Error loading pilot data';}
}

function openPilotDraft(c){
  document.getElementById('pdraft-id').value=c.id;
  document.getElementById('pdraft-title').textContent='Draft \u2014 '+c.name;
  document.getElementById('pdraft-text').value=c.outreach_draft||'';
  document.getElementById('pdraft-err').style.display='none';
  openSheet('sheet-pilot-draft');
}
async function savePilotDraft(andApprove){
  const id=document.getElementById('pdraft-id').value;
  const text=document.getElementById('pdraft-text').value.trim();
  const errEl=document.getElementById('pdraft-err');
  if(!text){errEl.textContent='Draft cannot be empty.';errEl.style.display='';return;}
  const action=andApprove?'save-and-approve':'save-draft';
  const r=await fetch('/api/pilot/'+id+'/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({draft:text})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeSheet('sheet-pilot-draft');
  toast(andApprove?'Draft saved and approved.':'Draft saved.');
  loadPilot();
}
function openPilotReply(id){
  document.getElementById('preply-id').value=id;
  document.getElementById('preply-text').value='';
  document.getElementById('preply-err').style.display='none';
  openSheet('sheet-pilot-reply');
}
async function submitPilotReply(){
  const id=document.getElementById('preply-id').value;
  const text=document.getElementById('preply-text').value.trim();
  const errEl=document.getElementById('preply-err');
  if(!text){errEl.textContent='Reply text is required.';errEl.style.display='';return;}
  const r=await fetch('/api/pilot/'+id+'/log-reply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reply:text})});
  const j=await r.json();
  if(!r.ok){errEl.textContent=j.error||'Error';errEl.style.display='';return;}
  closeSheet('sheet-pilot-reply');
  toast(j.summary?'Reply logged and summarized.':'Reply logged.');
  loadPilot();
}
async function pilotAction(id,action){
  const labels={approve:'Approve this draft for sending?','mark-sent':'Mark as sent?',convert:'Mark as converted pilot user?',pass:'Mark as passed?'};
  if(!confirm(labels[action]||action+'?'))return;
  const r=await fetch('/api/pilot/'+id+'/'+action,{method:'POST'});
  const j=await r.json();
  if(r.ok){toast(action==='convert'?'Converted! 🎉':action+' done.');loadPilot();}else{toast(j.error||'Error',true);}
}

// Init
switchTab('today');
load();
</script>
</body>
</html>"""
    return (
        _page.replace("__LOST_REASONS_JS__", _LOST_REASONS_JS)
        .replace("__MAX_NAME_JS__", str(_MAX_NAME_JS))
        .replace("__MAX_FIELD_JS__", str(_MAX_FIELD_JS))
        .replace("USER_EMAIL_PLACEHOLDER", _html.escape(user_email))
    )


DASHBOARD_HTML = _build_dashboard_html("user@example.com")

# ---------------------------------------------------------------------------
# Dashboard routes
# ---------------------------------------------------------------------------


@app.route("/manifest.json")
def manifest():
    return jsonify(
        {
            "name": "LeadClaw",
            "short_name": "LeadClaw",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#0f1117",
            "theme_color": "#6366f1",
            "icons": [
                {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            ],
        }
    )


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
# Message template route
# ---------------------------------------------------------------------------


@app.route("/api/leads/<int:lead_id>/draft-message", methods=["POST"])
@login_required
@verified_required
def route_draft_message(lead_id):
    """Return a copy-ready message for a lead. Pure template, no AI."""
    from leadclaw.drafting import MSG_TYPES, draft_message

    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    msg_type = data.get("type", "")
    if msg_type not in MSG_TYPES:
        return jsonify({"error": f"Invalid type. Valid: {', '.join(MSG_TYPES)}"}), 400
    msg = draft_message(dict(lead), msg_type)
    return jsonify({"message": msg, "type": msg_type})


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
    update_lead(lead_id, user_id=current_user.id, **fields)
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
    update_quote(lead_id, amount, user_id=current_user.id)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:lead_id>/won", methods=["POST"])
@login_required
@verified_required
def route_won_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": f"Lead {lead_id} not found"}), 404
    mark_won(lead_id, user_id=current_user.id)
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
    mark_lost(lead_id, reason, notes=notes, user_id=current_user.id)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:lead_id>/delete", methods=["POST"])
@login_required
@verified_required
def route_delete_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": f"Lead {lead_id} not found"}), 404
    delete_lead(lead_id, user_id=current_user.id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# New lifecycle endpoints: book, complete, invoice, paid, next-service
# ---------------------------------------------------------------------------


@app.route("/api/leads/<int:lead_id>/book", methods=["POST"])
@login_required
@verified_required
def api_book_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    scheduled_date = data.get("scheduled_date", "")
    if not scheduled_date or not _valid_date(scheduled_date):
        return jsonify({"error": "scheduled_date required (YYYY-MM-DD)"}), 400
    mark_booked(lead_id, scheduled_date, user_id=current_user.id)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:lead_id>/complete", methods=["POST"])
@login_required
@verified_required
def api_complete_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    mark_completed(lead_id, user_id=current_user.id)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:lead_id>/invoice", methods=["POST"])
@login_required
@verified_required
def api_invoice_lead(lead_id):
    from leadclaw.config import DEFAULT_INVOICE_REMINDER_DAYS

    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    amount = data.get("invoice_amount")
    if amount is not None:
        try:
            amount = float(amount)
            if amount <= 0:
                return jsonify({"error": "amount must be > 0"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "invalid amount"}), 400
    mark_invoice_sent(
        lead_id,
        invoice_amount=amount,
        reminder_days=DEFAULT_INVOICE_REMINDER_DAYS,
        user_id=current_user.id,
    )
    return jsonify({"ok": True})


@app.route("/api/leads/<int:lead_id>/paid", methods=["POST"])
@login_required
@verified_required
def api_paid_lead(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    recurring = data.get("recurring_days")
    if recurring is not None:
        try:
            recurring = int(recurring)
        except (ValueError, TypeError):
            return jsonify({"error": "invalid recurring_days"}), 400
    mark_paid(lead_id, recurring_days=recurring, user_id=current_user.id)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:lead_id>/next-service", methods=["POST"])
@login_required
@verified_required
def api_next_service(lead_id):
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    date_val = data.get("next_service_due_at", "")
    if not date_val or not _valid_date(date_val):
        return jsonify({"error": "next_service_due_at required (YYYY-MM-DD)"}), 400
    set_next_service(lead_id, date_val, user_id=current_user.id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Reminder dismissal + usage endpoints
# ---------------------------------------------------------------------------


@app.route("/api/reminders/dismiss", methods=["POST"])
@login_required
@verified_required
def api_dismiss_reminder():
    data = request.get_json(silent=True) or {}
    lead_id = data.get("lead_id")
    reminder_type = (data.get("reminder_type") or "").strip()
    if not lead_id:
        return jsonify({"error": "lead_id is required"}), 400
    if reminder_type not in DISMISSAL_FIELDS:
        return jsonify(
            {"error": f"reminder_type must be one of: {', '.join(DISMISSAL_FIELDS)}"}
        ), 400
    lead = get_lead_by_id(int(lead_id), user_id=current_user.id)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    ok = dismiss_reminder_standalone(lead["id"], reminder_type, user_id=current_user.id)
    if not ok:
        return jsonify({"error": "Could not dismiss reminder"}), 400
    return jsonify({"ok": True})


@app.route("/api/usage")
@login_required
@verified_required
def route_api_usage():
    try:
        return jsonify(api_usage())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    _pilot.set_draft(cid, draft, user_id=current_user.id)
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
    _pilot.set_draft(cid, draft, user_id=current_user.id)
    _pilot.set_status(cid, "approved", user_id=current_user.id)
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
    _pilot.set_status(cid, "approved", user_id=current_user.id)
    return jsonify({"ok": True})


@app.route("/api/pilot/<int:cid>/mark-sent", methods=["POST"])
@login_required
@verified_required
def route_pilot_mark_sent(cid):
    candidate = _get_pilot_candidate(cid)
    if not candidate:
        return jsonify({"error": f"Candidate {cid} not found"}), 404
    _pilot.set_status(cid, "sent", contacted=True, user_id=current_user.id)
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
    _pilot.log_reply(cid, reply, user_id=current_user.id)
    summary = None
    try:
        from leadclaw.drafting import check_api_key, summarize_pilot_reply

        if check_api_key():
            summary = summarize_pilot_reply(dict(candidate), reply)
            if summary:
                _pilot.set_reply_summary(cid, summary, user_id=current_user.id)
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
    _pilot.set_status(cid, "converted", user_id=current_user.id)
    return jsonify({"ok": True})


@app.route("/api/pilot/<int:cid>/pass", methods=["POST"])
@login_required
@verified_required
def route_pilot_pass(cid):
    candidate = _get_pilot_candidate(cid)
    if not candidate:
        return jsonify({"error": f"Candidate {cid} not found"}), 404
    _pilot.set_status(cid, "passed", user_id=current_user.id)
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
