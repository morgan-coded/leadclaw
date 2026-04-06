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

import leadclaw.availability as _avail
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
    get_closed_leads,
    get_event_counts,
    get_invoice_reminders,
    get_job_today_leads,
    get_lead_by_id,
    get_pipeline_summary,
    get_public_requests,
    get_reactivation_leads,
    get_review_reminders,
    get_service_reminders,
    get_stale_leads,
    get_today_leads,
    get_unseen_requests,
    mark_all_requests_seen,
    mark_booked,
    mark_completed,
    mark_invoice_sent,
    mark_lost,
    mark_paid,
    mark_request_seen,
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
# Public service request form
# ---------------------------------------------------------------------------

_REQUEST_SERVICES = [
    "Lawn Mowing",
    "Landscaping",
    "Cleanup",
    "Mulching",
    "Other",
]

_REQUEST_TIME_WINDOWS = [
    ("morning", "Morning (8am–12pm)"),
    ("afternoon", "Afternoon (12pm–5pm)"),
    ("evening", "Evening (5pm–8pm)"),
    ("flexible", "Flexible"),
]

_REQUEST_CSS = (
    _AUTH_CSS
    + """
<style>
select,textarea{width:100%;padding:9px 12px;background:#22263a;border:1px solid var(--border);
  border-radius:6px;color:var(--text);font-size:16px;font-family:inherit;outline:none;
  -webkit-appearance:none;appearance:none;}
select:focus,textarea:focus{border-color:var(--accent);}
textarea{resize:vertical;min-height:80px;}
.card{max-width:480px;}
.success-icon{font-size:48px;margin-bottom:12px;}
.success-msg{font-size:18px;font-weight:600;margin-bottom:8px;}
.success-sub{color:var(--muted);font-size:14px;line-height:1.5;}
</style>
"""
)

_REQUEST_FORM_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>Request Service</title>" + _REQUEST_CSS + "</head><body><div class='card'>"
    "<h1>🦞 Request Service</h1>"
    "<div class='sub'>Fill out the form and we'll get back to you shortly.</div>"
    "{% if error %}<div class='err'>{{ error }}</div>{% endif %}"
    "<form method='post'>"
    "<div class='form-group'><label>Your Name *</label>"
    "<input type='text' name='name' required autocomplete='name' value='{{ name|default(\"\") }}'></div>"
    "<div class='form-group'><label>Phone Number *</label>"
    "<input type='tel' name='phone' required autocomplete='tel' value='{{ phone|default(\"\") }}'></div>"
    "<div class='form-group'><label>Email (optional)</label>"
    "<input type='email' name='email' autocomplete='email' value='{{ email|default(\"\") }}'></div>"
    "<div class='form-group'><label>Service Needed *</label>"
    "<select name='service' required>"
    "{% for svc in services %}"
    "<option value='{{ svc }}' {% if svc == service %}selected{% endif %}>{{ svc }}</option>"
    "{% endfor %}"
    "</select></div>"
    "<div class='form-group'><label>Service Address *</label>"
    "<input type='text' name='service_address' required placeholder='Street, City, ZIP'"
    " value='{{ service_address|default(\"\") }}'></div>"
    "<div class='form-group'><label>Preferred Date (optional)</label>"
    "<input type='date' name='requested_date' value='{{ requested_date|default(\"\") }}'></div>"
    "<div class='form-group'><label>Preferred Time Window</label>"
    "<select name='requested_time_window'>"
    "{% for val, label in time_windows %}"
    "<option value='{{ val }}' {% if val == requested_time_window %}selected{% endif %}>{{ label }}</option>"
    "{% endfor %}"
    "</select></div>"
    "<div class='form-group'><label>Notes (optional)</label>"
    "<textarea name='notes' placeholder='Any extra details...'>{{ notes|default(\"\") }}</textarea></div>"
    "<button class='btn' type='submit'>Submit Request</button>"
    "</form>"
    "</div></body></html>"
)

_REQUEST_SUCCESS_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>Request Received</title>" + _REQUEST_CSS + "</head><body><div class='card'>"
    "<div style='text-align:center;padding:16px 0'>"
    "<div class='success-icon'>✅</div>"
    "<div class='success-msg'>Request Received!</div>"
    "<div class='success-sub'>Thanks, {{ name }}! We'll review your request and reach out soon.</div>"
    "{% if avail_warning %}"
    "<div class='info' style='margin-top:16px;text-align:left'>\U0001f4c5 {{ avail_warning }}</div>"
    "{% endif %}"
    "</div>"
    "</div></body></html>"
)


def _send_new_request_notification(lead: dict):
    """Fire-and-forget owner alert when a new public request comes in.

    Tries Resend first, then SMTP, then logs to stderr in dev mode.
    Never raises — notification failures must not break form submission.
    """
    owner_email = os.environ.get("OWNER_NOTIFY_EMAIL") or os.environ.get("SMTP_USER", "").strip()
    if not owner_email:
        # No owner email configured — skip silently
        return

    app_url = os.environ.get("APP_URL", "http://localhost:7432").rstrip("/")
    name = lead.get("name") or "Unknown"
    service = lead.get("service") or "N/A"
    phone = lead.get("phone") or "—"
    address = lead.get("service_address") or "—"
    pref_date = lead.get("requested_date") or "—"
    pref_tw = lead.get("requested_time_window") or ""
    notes = lead.get("notes") or ""

    subject = f"New LeadClaw request: {service} from {name}"
    body_lines = [
        "New service request submitted via LeadClaw:",
        "",
        f"  Name:     {name}",
        f"  Phone:    {phone}",
        f"  Service:  {service}",
        f"  Address:  {address}",
        f"  Pref. date: {pref_date}" + (f" ({pref_tw})" if pref_tw else ""),
    ]
    if notes:
        body_lines.append(f"  Notes:    {notes}")
    body_lines += ["", f"View in dashboard: {app_url}/"]
    body = "\n".join(body_lines)

    try:
        resend_key = (os.environ.get("RESEND_API_KEY") or "").strip()
        if resend_key:
            import urllib.request as _ureq

            payload = _json.dumps(
                {
                    "from": "LeadClaw <noreply@morganlabs.org>",
                    "to": [owner_email],
                    "subject": subject,
                    "text": body,
                }
            ).encode()
            req = _ureq.Request(
                "https://api.resend.com/emails",
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                _ureq.urlopen(req, timeout=8)
                print(f"[NOTIFY] New request alert sent to {owner_email}", file=sys.stderr)
                return
            except Exception as exc:
                print(f"[NOTIFY] Resend failed: {exc}", file=sys.stderr)
                # Fall through to SMTP

        smtp_host = os.environ.get("SMTP_HOST")
        if smtp_host:
            smtp_port = int(os.environ.get("SMTP_PORT", 587))
            smtp_user = os.environ.get("SMTP_USER", "")
            smtp_pass = os.environ.get("SMTP_PASS", "")
            from_addr = smtp_user or owner_email
            msg = MIMEText(body, "plain")
            msg["Subject"] = subject
            msg["From"] = from_addr
            msg["To"] = owner_email
            with smtplib.SMTP(smtp_host, smtp_port, timeout=8) as srv:
                srv.ehlo()
                srv.starttls()
                srv.login(smtp_user, smtp_pass)
                srv.sendmail(from_addr, [owner_email], msg.as_string())
            print(f"[NOTIFY] New request alert sent via SMTP to {owner_email}", file=sys.stderr)
            return

        # Dev fallback — print to stderr
        print(
            f"[NOTIFY] New request from {name} ({service}) — {phone} — {address}", file=sys.stderr
        )
    except Exception as exc:
        print(f"[NOTIFY] Failed to send owner notification: {exc}", file=sys.stderr)


@app.route("/request", methods=["GET", "POST"])
def public_request():
    """Public service request form. No auth required. Creates a lead on submit."""
    if request.method == "GET":
        return render_template_string(
            _REQUEST_FORM_HTML,
            services=_REQUEST_SERVICES,
            time_windows=_REQUEST_TIME_WINDOWS,
        )

    name = (request.form.get("name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    email = (request.form.get("email") or "").strip() or None
    service = (request.form.get("service") or "").strip()
    service_address = (request.form.get("service_address") or "").strip()
    requested_date = (request.form.get("requested_date") or "").strip() or None
    requested_time_window = (request.form.get("requested_time_window") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None

    _MAX_PHONE = 20

    errors = []
    if not name:
        errors.append("Name is required.")
    elif len(name) > MAX_NAME_LENGTH:
        errors.append(f"Name must be {MAX_NAME_LENGTH} characters or fewer.")
    if not phone:
        errors.append("Phone number is required.")
    elif len(phone) > _MAX_PHONE:
        errors.append(f"Phone number must be {_MAX_PHONE} characters or fewer.")
    if not service or service not in _REQUEST_SERVICES:
        errors.append("Please select a valid service.")
    if not service_address:
        errors.append("Service address is required.")
    elif len(service_address) > MAX_FIELD_LENGTH:
        errors.append(f"Service address must be {MAX_FIELD_LENGTH} characters or fewer.")
    if notes and len(notes) > MAX_FIELD_LENGTH:
        errors.append(f"Notes must be {MAX_FIELD_LENGTH} characters or fewer.")
    if email and not _valid_email(email):
        errors.append("Enter a valid email address.")
    if requested_date and not _valid_date(requested_date):
        errors.append("Enter a valid date.")
        requested_date = None
    if requested_time_window and requested_time_window not in {v for v, _ in _REQUEST_TIME_WINDOWS}:
        requested_time_window = None

    if errors:
        return (
            render_template_string(
                _REQUEST_FORM_HTML,
                error=" ".join(errors),
                services=_REQUEST_SERVICES,
                time_windows=_REQUEST_TIME_WINDOWS,
                name=name,
                phone=phone,
                email=email or "",
                service=service,
                service_address=service_address,
                requested_date=requested_date or "",
                requested_time_window=requested_time_window or "flexible",
                notes=notes or "",
            ),
            422,
        )

    # Soft availability check — warn but never block submission
    avail_warning = None
    if requested_date:
        try:
            avail = _avail.get_availability(user_id=1)
            check = _avail.check_date(requested_date, avail)
            if not check["ok"]:
                avail_warning = (
                    f"Note: your preferred date ({requested_date}) may not be available "
                    "\u2014 we'll reach out to confirm a time that works."
                )
        except Exception:
            pass  # availability check is best-effort

    add_lead(
        name=name,
        service=service,
        phone=phone,
        email=email,
        notes=notes,
        lead_source="public_request",
        requested_date=requested_date,
        requested_time_window=requested_time_window,
        service_address=service_address,
        user_id=1,
    )

    _send_new_request_notification(
        {
            "name": name,
            "service": service,
            "phone": phone,
            "service_address": service_address,
            "requested_date": requested_date,
            "requested_time_window": requested_time_window,
            "notes": notes,
        }
    )

    return render_template_string(_REQUEST_SUCCESS_HTML, name=name, avail_warning=avail_warning)


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

    # Normalize legacy 'won' status to 'paid' for API consumers
    raw_status = row["status"]
    status = "paid" if raw_status == "won" else raw_status

    return {
        "id": row["id"],
        "name": row["name"],
        "service": row["service"],
        "status": status,
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
        "lead_source": _safe_col("lead_source"),
        "requested_date": _safe_col("requested_date"),
        "requested_time_window": _safe_col("requested_time_window"),
        "service_address": _safe_col("service_address"),
        "scheduled_time_window": _safe_col("scheduled_time_window"),
        "request_seen_at": _safe_col("request_seen_at"),
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
        "unseen_requests_count": len(get_unseen_requests(user_id=user_id)),
        "invoice_reminders": [_lead_to_dict(r) for r in get_invoice_reminders(user_id=user_id)],
        "service_reminders": [_lead_to_dict(r) for r in get_service_reminders(user_id=user_id)],
        "job_today": [_lead_to_dict(r) for r in get_job_today_leads(user_id=user_id)],
        "review_reminders": [_lead_to_dict(r) for r in get_review_reminders(user_id=user_id)],
        "reactivation_30": [_lead_to_dict(r) for r in get_reactivation_leads(30, user_id=user_id)],
        "reactivation_60": [_lead_to_dict(r) for r in get_reactivation_leads(60, user_id=user_id)],
        "reactivation_90": [_lead_to_dict(r) for r in get_reactivation_leads(90, user_id=user_id)],
    }


def api_closed(user_id: int) -> dict:
    rows = get_closed_leads(user_id=user_id)
    return {"closed": [_lead_to_dict(r) for r in rows]}


def api_usage(user_id: int) -> dict:
    last30 = get_event_counts(days=30, user_id=user_id)
    alltime = get_event_counts(user_id=user_id)
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

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
with open(_TEMPLATE_PATH) as _f:
    _DASHBOARD_TEMPLATE = _f.read()


def _build_dashboard_html(user_email: str) -> str:
    """Return the full dashboard HTML with user email and signout link injected."""
    return (
        _DASHBOARD_TEMPLATE.replace("__LOST_REASONS_JS__", _LOST_REASONS_JS)
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
            "icons": [],
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
    scheduled_time_window = (data.get("scheduled_time_window") or "").strip() or None
    _VALID_WINDOWS = {"morning", "afternoon", "evening", "flexible"}
    if scheduled_time_window and scheduled_time_window not in _VALID_WINDOWS:
        scheduled_time_window = None
    mark_booked(
        lead_id,
        scheduled_date,
        scheduled_time_window=scheduled_time_window,
        user_id=current_user.id,
    )
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


# ---------------------------------------------------------------------------
# Availability settings endpoints
# ---------------------------------------------------------------------------


@app.route("/api/availability", methods=["GET"])
@login_required
@verified_required
def route_get_availability():
    """Return current availability settings for the logged-in user."""
    try:
        avail = _avail.get_availability(current_user.id)
        avail["next_available"] = _avail.next_available_date(avail)
        avail["working_days_hint"] = _avail.working_days_hint(avail)
        return jsonify(avail)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/availability", methods=["POST"])
@login_required
@verified_required
def route_set_availability():
    """Save availability settings."""
    data = request.get_json(silent=True) or {}
    allowed_weekdays = data.get("allowed_weekdays")
    if allowed_weekdays is None or not isinstance(allowed_weekdays, list):
        return jsonify({"error": "allowed_weekdays must be a list of ints (0=Mon...6=Sun)"}), 400
    if any(not isinstance(d, int) or d < 0 or d > 6 for d in allowed_weekdays):
        return jsonify({"error": "allowed_weekdays values must be 0–6"}), 400
    blocked_dates = data.get("blocked_dates")
    if blocked_dates is None:
        blocked_dates = []
    if not isinstance(blocked_dates, list):
        return jsonify({"error": "blocked_dates must be a list of YYYY-MM-DD strings"}), 400
    # Validate each blocked date
    for d in blocked_dates:
        if not _valid_date(str(d).strip()):
            return jsonify({"error": f"Invalid blocked date: {d!r}"}), 400
    _avail.set_availability(current_user.id, allowed_weekdays, blocked_dates)
    return jsonify({"ok": True})


@app.route("/api/availability/check")
@login_required
@verified_required
def route_check_availability():
    """Check whether a date is available. ?date=YYYY-MM-DD"""
    date_str = (request.args.get("date") or "").strip()
    if not date_str or not _valid_date(date_str):
        return jsonify({"error": "date query param required (YYYY-MM-DD)"}), 400
    avail = _avail.get_availability(current_user.id)
    result = _avail.check_date(date_str, avail)
    result["next_available"] = _avail.next_available_date(avail, from_date=date_str)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Requests tab endpoint
# ---------------------------------------------------------------------------

_REQUEST_FILTER_VALUES = {"unbooked", "booked", "all"}


@app.route("/api/requests")
@login_required
@verified_required
def route_api_requests():
    """Return public request leads filtered by status group."""
    filter_val = (request.args.get("filter") or "unbooked").strip()
    if filter_val not in _REQUEST_FILTER_VALUES:
        filter_val = "unbooked"
    try:
        rows = get_public_requests(user_id=current_user.id, filter=filter_val)
        return jsonify({"requests": [_lead_to_dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/requests/<int:lead_id>/seen", methods=["POST"])
@login_required
@verified_required
def route_mark_request_seen(lead_id):
    """Mark a single public request as seen by the owner."""
    lead = get_lead_by_id(lead_id, user_id=current_user.id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    mark_request_seen(lead_id, user_id=current_user.id)
    return jsonify({"ok": True})


@app.route("/api/requests/seen-all", methods=["POST"])
@login_required
@verified_required
def route_mark_all_requests_seen():
    """Mark all unseen public requests as seen."""
    count = mark_all_requests_seen(user_id=current_user.id)
    return jsonify({"ok": True, "marked": count})


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
        return jsonify(api_usage(current_user.id))
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
