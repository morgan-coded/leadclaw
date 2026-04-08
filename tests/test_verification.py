"""
tests/test_verification.py - Tests for email verification on signup.

Covers:
- Signup sends verification email and does NOT auto-login (REQUIRE_VERIFICATION=True)
- Signup auto-verifies when REQUIRE_VERIFICATION=False
- Unverified user is blocked by @verified_required
- GET /verify/<token> verifies and logs in
- Invalid token returns error
- POST /verify/resend sends new email and rate limits
- Resend for unknown/verified email shows generic message
"""

import os

import bcrypt
import pytest

from leadclaw.db import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    init_db,
    verify_user_email,
)
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


# ---------------------------------------------------------------------------
# 11a: Signup sends verification email, does NOT auto-login
# ---------------------------------------------------------------------------


def test_signup_sends_verification_email(client):
    """When REQUIRE_VERIFICATION is True, signup sends email and shows check-email page."""
    import leadclaw.config as cfg

    original = cfg.REQUIRE_VERIFICATION
    cfg.REQUIRE_VERIFICATION = True
    # Also patch web module's reference
    import leadclaw.web as web_mod

    web_mod.REQUIRE_VERIFICATION = True
    try:
        with unittest_mock_send_verification() as mock_send:
            r = client.post(
                "/signup",
                data={
                    "email": "verify@example.com",
                    "password": "password123",
                    "confirm": "password123",
                },
            )
            assert r.status_code == 200
            assert b"verification link" in r.data.lower() or b"verify" in r.data.lower()
            assert mock_send.called
            # Email should have been passed
            call_args = mock_send.call_args
            assert call_args[0][0] == "verify@example.com"
    finally:
        cfg.REQUIRE_VERIFICATION = original
        web_mod.REQUIRE_VERIFICATION = original


def test_signup_does_not_auto_login_when_verification_required(client):
    """When REQUIRE_VERIFICATION is True, user should NOT be logged in after signup."""
    import leadclaw.config as cfg
    import leadclaw.web as web_mod

    original = cfg.REQUIRE_VERIFICATION
    cfg.REQUIRE_VERIFICATION = True
    web_mod.REQUIRE_VERIFICATION = True
    try:
        with unittest_mock_send_verification():
            client.post(
                "/signup",
                data={
                    "email": "nologin@example.com",
                    "password": "password123",
                    "confirm": "password123",
                },
            )
        # Dashboard should redirect to login since user is not logged in
        r = client.get("/")
        assert r.status_code == 302
        assert "/login" in r.headers.get("Location", "")
    finally:
        cfg.REQUIRE_VERIFICATION = original
        web_mod.REQUIRE_VERIFICATION = original


def test_signup_user_not_verified_in_db(client):
    """When REQUIRE_VERIFICATION is True, user's email_verified should be False."""
    import leadclaw.config as cfg
    import leadclaw.web as web_mod

    original = cfg.REQUIRE_VERIFICATION
    cfg.REQUIRE_VERIFICATION = True
    web_mod.REQUIRE_VERIFICATION = True
    try:
        with unittest_mock_send_verification():
            client.post(
                "/signup",
                data={
                    "email": "unverified@example.com",
                    "password": "password123",
                    "confirm": "password123",
                },
            )
        row = get_user_by_email("unverified@example.com")
        assert row is not None
        assert row["email_verified"] == 0
    finally:
        cfg.REQUIRE_VERIFICATION = original
        web_mod.REQUIRE_VERIFICATION = original


# ---------------------------------------------------------------------------
# 11e: Graceful degradation — auto-verify when disabled
# ---------------------------------------------------------------------------


def test_signup_auto_verifies_when_disabled(client):
    """When REQUIRE_VERIFICATION is False, signup auto-verifies and logs in."""
    import leadclaw.config as cfg
    import leadclaw.web as web_mod

    original = cfg.REQUIRE_VERIFICATION
    cfg.REQUIRE_VERIFICATION = False
    web_mod.REQUIRE_VERIFICATION = False
    try:
        r = client.post(
            "/signup",
            data={
                "email": "autoverify@example.com",
                "password": "password123",
                "confirm": "password123",
            },
        )
        assert r.status_code == 302  # redirect to dashboard

        row = get_user_by_email("autoverify@example.com")
        assert row is not None
        assert row["email_verified"] == 1

        # Dashboard should be accessible
        r2 = client.get("/")
        assert r2.status_code == 200
    finally:
        cfg.REQUIRE_VERIFICATION = original
        web_mod.REQUIRE_VERIFICATION = original


# ---------------------------------------------------------------------------
# 11b: Verify token route
# ---------------------------------------------------------------------------


def test_verify_token_verifies_and_logs_in(client):
    """GET /verify/<token> should verify user and redirect to dashboard."""
    # Create an unverified user
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    token = "test-verify-token-123"
    uid = create_user("toverify@example.com", pw_hash, token)

    r = client.get(f"/verify/{token}")
    assert r.status_code == 302
    assert "/" in r.headers.get("Location", "")

    # User should now be verified
    row = get_user_by_id(uid)
    assert row["email_verified"] == 1

    # Should be able to access dashboard
    r2 = client.get("/")
    assert r2.status_code == 200


def test_verify_invalid_token_shows_error(client):
    """GET /verify/<bad-token> should show error on login page."""
    r = client.get("/verify/nonexistent-token-xyz")
    assert r.status_code == 200
    assert b"invalid" in r.data.lower() or b"expired" in r.data.lower()


def test_verify_token_already_used(client):
    """A token that's already been used (cleared) should fail."""
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    token = "once-use-token"
    uid = create_user("once@example.com", pw_hash, token)
    verify_user_email(uid)  # This clears the token

    r = client.get(f"/verify/{token}")
    assert b"invalid" in r.data.lower() or b"expired" in r.data.lower()


# ---------------------------------------------------------------------------
# 11c: POST /verify/resend
# ---------------------------------------------------------------------------


def test_resend_verification_sends_new_email(client):
    """POST /verify/resend should generate new token and send email."""
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    token = "old-token-resend"
    uid = create_user("resend@example.com", pw_hash, token)

    with unittest_mock_send_verification() as mock_send:
        r = client.post("/verify/resend", data={"email": "resend@example.com"})
        assert r.status_code == 200
        assert mock_send.called
        # Should have been called with a new token (not the old one)
        new_token = mock_send.call_args[0][1]
        assert new_token != token

    # Old token should no longer work
    row = get_user_by_id(uid)
    assert row["verify_token"] == new_token


def test_resend_for_verified_user_no_email(client):
    """POST /verify/resend for already-verified user should NOT send email."""
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    token = "already-verified"
    uid = create_user("verified@example.com", pw_hash, token)
    verify_user_email(uid)

    with unittest_mock_send_verification() as mock_send:
        r = client.post("/verify/resend", data={"email": "verified@example.com"})
        assert r.status_code == 200
        assert not mock_send.called
    # Should show generic message (no information leakage)
    assert b"if an account" in r.data.lower()


def test_resend_for_unknown_email_no_leak(client):
    """POST /verify/resend for unknown email should show generic message."""
    with unittest_mock_send_verification() as mock_send:
        r = client.post("/verify/resend", data={"email": "nobody@example.com"})
        assert r.status_code == 200
        assert not mock_send.called
    assert b"if an account" in r.data.lower()


def test_resend_rate_limit(client):
    """POST /verify/resend should be rate limited to 3/hour."""
    from leadclaw.web import limiter

    limiter.reset()

    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    create_user("ratelimit@example.com", pw_hash, "rl-token")

    with unittest_mock_send_verification():
        for _ in range(3):
            r = client.post("/verify/resend", data={"email": "ratelimit@example.com"})
            assert r.status_code == 200

        # 4th request should be rate limited
        r = client.post("/verify/resend", data={"email": "ratelimit@example.com"})
        assert r.status_code == 429


# ---------------------------------------------------------------------------
# 11d: Unverified user blocked by @verified_required
# ---------------------------------------------------------------------------


def test_unverified_user_blocked_from_dashboard(client):
    """Unverified user who logs in should see verification page, not dashboard."""
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    create_user("unverified-login@example.com", pw_hash, "uv-token")

    # Log in directly (bypassing signup flow)
    client.post("/login", data={"email": "unverified-login@example.com", "password": "password123"})

    # Dashboard should show unverified HTML
    r = client.get("/")
    assert r.status_code == 200
    assert b"verify your email" in r.data.lower()


def test_unverified_html_has_resend_button(client):
    """UNVERIFIED_HTML should include a resend verification button."""
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    create_user("btn@example.com", pw_hash, "btn-token")

    client.post("/login", data={"email": "btn@example.com", "password": "password123"})
    r = client.get("/")
    assert b"/verify/resend" in r.data
    assert b"Resend" in r.data


# ---------------------------------------------------------------------------
# Signup generates request slug
# ---------------------------------------------------------------------------


def test_signup_generates_request_slug(client):
    """Signup should generate a request_slug for the new user."""
    import leadclaw.config as cfg
    import leadclaw.web as web_mod

    original = cfg.REQUIRE_VERIFICATION
    cfg.REQUIRE_VERIFICATION = False
    web_mod.REQUIRE_VERIFICATION = False
    try:
        client.post(
            "/signup",
            data={
                "email": "slugtest@example.com",
                "password": "password123",
                "confirm": "password123",
            },
        )
        row = get_user_by_email("slugtest@example.com")
        assert row is not None
        assert row["request_slug"] is not None
        assert len(row["request_slug"]) > 0
    finally:
        cfg.REQUIRE_VERIFICATION = original
        web_mod.REQUIRE_VERIFICATION = original


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def unittest_mock_send_verification():
    """Context manager that mocks _send_verification_email."""
    from unittest.mock import patch

    return patch("leadclaw.web._send_verification_email")
