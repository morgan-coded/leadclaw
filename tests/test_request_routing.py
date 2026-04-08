"""
tests/test_request_routing.py - Tests for per-user /request/<slug> routing.

Covers:
- POST /request/<valid_slug> creates lead with correct user_id
- GET /request/<valid_slug> renders the form
- GET /request/<invalid_slug> returns 404
- POST /request (no slug) still routes to user_id=1
- Slug for unverified user returns 404
- Slug for expired-subscription user returns 404 (when Stripe enabled)
- Business name branding in form title
- /api/billing returns request_url
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest

from leadclaw.db import (
    create_user,
    init_db,
    set_user_slug,
    update_user_stripe,
    verify_user_email,
)
from leadclaw.queries import get_all_active_leads
from tests.conftest import TEST_DB


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


@pytest.fixture
def client():
    from leadclaw.web import app, limiter

    app.config["TESTING"] = True
    limiter.reset()
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_client(client):
    """Logged in, verified user with a request slug."""
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user("owner@example.com", pw_hash, "tok")
    verify_user_email(uid)
    set_user_slug(uid, "test-slug-123")
    trial_end = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=14)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    update_user_stripe(uid, subscription_status="trialing", trial_ends_at=trial_end)

    client.post("/login", data={"email": "owner@example.com", "password": "password123"})
    client._test_user_id = uid
    return client


def _valid_form_data():
    """Return minimal valid form data for public request."""
    return {
        "name": "Test Customer",
        "phone": "555-1234",
        "service": "Lawn Mowing",
        "service_address": "123 Main St",
        "_form_ts": str(int(time.time()) - 10),
    }


# ---------------------------------------------------------------------------
# 12b: GET/POST /request/<slug>
# ---------------------------------------------------------------------------


def test_get_request_form_by_slug(client):
    """GET /request/<valid_slug> should render the request form."""
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user("slug-owner@example.com", pw_hash, "stok")
    verify_user_email(uid)
    set_user_slug(uid, "my-slug")

    r = client.get("/request/my-slug")
    assert r.status_code == 200
    assert b"Request Service" in r.data


def test_post_request_by_slug_creates_lead(client):
    """POST /request/<slug> should create a lead owned by the correct user."""
    from leadclaw.web import limiter

    limiter.reset()

    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user("lead-owner@example.com", pw_hash, "ltok")
    verify_user_email(uid)
    set_user_slug(uid, "owner-slug")

    r = client.post("/request/owner-slug", data=_valid_form_data())
    assert r.status_code == 200
    assert b"Request Received" in r.data

    # Lead should belong to this user, not user_id=1
    leads = get_all_active_leads(user_id=uid)
    assert len(leads) >= 1
    assert leads[0]["name"] == "Test Customer"


def test_post_request_by_slug_not_user_1(client):
    """Lead created via /request/<slug> should NOT be owned by user_id=1."""
    from leadclaw.web import limiter

    limiter.reset()

    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user("other-owner@example.com", pw_hash, "otok")
    verify_user_email(uid)
    set_user_slug(uid, "other-slug")

    client.post("/request/other-slug", data=_valid_form_data())

    # Should NOT appear in user_id=1's leads
    leads_for_1 = get_all_active_leads(user_id=1)
    names_for_1 = [lead["name"] for lead in leads_for_1]
    assert "Test Customer" not in names_for_1


# ---------------------------------------------------------------------------
# 12b: Invalid slug returns 404
# ---------------------------------------------------------------------------


def test_get_request_invalid_slug_404(client):
    """GET /request/<nonexistent_slug> should return 404."""
    r = client.get("/request/nonexistent-slug-xyz")
    assert r.status_code == 404


def test_post_request_invalid_slug_404(client):
    """POST /request/<nonexistent_slug> should return 404."""
    r = client.post("/request/nonexistent-slug-xyz", data=_valid_form_data())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 12b: Unverified user slug returns 404
# ---------------------------------------------------------------------------


def test_request_slug_unverified_user_404(client):
    """Slug belonging to unverified user should return 404."""
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user("unverified-slug@example.com", pw_hash, "uvtok")
    # Do NOT verify
    set_user_slug(uid, "unverified-slug")

    r = client.get("/request/unverified-slug")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 12b: Expired subscription slug returns 404 (Stripe enabled)
# ---------------------------------------------------------------------------


def test_request_slug_expired_sub_404_when_stripe_enabled(client):
    """Slug for expired-trial user should return 404 when Stripe is enabled."""
    import leadclaw.web as web_mod

    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user("expired-slug@example.com", pw_hash, "etok")
    verify_user_email(uid)
    set_user_slug(uid, "expired-slug")
    expired = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    update_user_stripe(uid, subscription_status="trialing", trial_ends_at=expired)

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = True
    try:
        r = client.get("/request/expired-slug")
        assert r.status_code == 404
    finally:
        web_mod._STRIPE_ENABLED = original


def test_request_slug_active_sub_works_when_stripe_enabled(client):
    """Slug for active-subscription user should work when Stripe is enabled."""
    import leadclaw.web as web_mod

    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user("active-slug@example.com", pw_hash, "atok")
    verify_user_email(uid)
    set_user_slug(uid, "active-slug")
    update_user_stripe(uid, subscription_status="active")

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = True
    try:
        r = client.get("/request/active-slug")
        assert r.status_code == 200
        assert b"Request Service" in r.data
    finally:
        web_mod._STRIPE_ENABLED = original


# ---------------------------------------------------------------------------
# 12c: Legacy /request still routes to user_id=1
# ---------------------------------------------------------------------------


def test_legacy_request_still_routes_to_user_1(client):
    """POST /request (no slug) should still route to user_id=1."""
    from leadclaw.web import limiter

    limiter.reset()

    r = client.post("/request", data=_valid_form_data())
    assert r.status_code == 200
    assert b"Request Received" in r.data

    leads = get_all_active_leads(user_id=1)
    assert len(leads) >= 1
    assert leads[0]["name"] == "Test Customer"


def test_legacy_request_get_renders_form(client):
    """GET /request (no slug) should render the form."""
    r = client.get("/request")
    assert r.status_code == 200
    assert b"Request Service" in r.data


# ---------------------------------------------------------------------------
# 12f: Business name branding
# ---------------------------------------------------------------------------


def test_request_form_shows_business_name(client):
    """When user has business_name, form title should include it."""
    from leadclaw.db import get_conn

    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user("biz@example.com", pw_hash, "btok")
    verify_user_email(uid)
    set_user_slug(uid, "biz-slug")
    with get_conn() as conn:
        conn.execute("UPDATE users SET business_name = ? WHERE id = ?", ("Green Thumb Lawns", uid))

    r = client.get("/request/biz-slug")
    assert r.status_code == 200
    assert b"Green Thumb Lawns" in r.data


def test_request_form_no_business_name_generic(client):
    """When user has no business_name, form should show generic title."""
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user("nobiz@example.com", pw_hash, "ntok")
    verify_user_email(uid)
    set_user_slug(uid, "nobiz-slug")

    r = client.get("/request/nobiz-slug")
    assert r.status_code == 200
    assert b"Request Service" in r.data


# ---------------------------------------------------------------------------
# 12d: /api/billing returns request_url
# ---------------------------------------------------------------------------


def test_api_billing_includes_request_url(auth_client):
    """The /api/billing response should include the user's request URL."""
    r = auth_client.get("/api/billing")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "request_url" in data
    assert data["request_url"] is not None
    assert "test-slug-123" in data["request_url"]


# ---------------------------------------------------------------------------
# Anti-spam applied to slug route too
# ---------------------------------------------------------------------------


def test_slug_route_honeypot_works(client):
    """Honeypot should work on /request/<slug> too."""
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    uid = create_user("hp@example.com", pw_hash, "hptok")
    verify_user_email(uid)
    set_user_slug(uid, "hp-slug")

    data = _valid_form_data()
    data["website"] = "http://spam.com"  # honeypot filled

    r = client.post("/request/hp-slug", data=data)
    assert r.status_code == 200
    assert b"Request Received" in r.data

    # Lead should NOT have been created
    leads = get_all_active_leads(user_id=uid)
    assert len(leads) == 0
