"""
tests/test_stripe.py - Tests for Stripe billing integration.

Covers:
- Trial state on signup
- has_active_subscription logic
- trial_days_remaining calculation
- @subscription_required decorator (enabled vs disabled)
- Billing routes (checkout, portal, success, cancel)
- Webhook handling (signature validation, event processing)
- Graceful degradation when Stripe env vars are missing
- /api/billing endpoint
"""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from leadclaw.db import get_conn, get_user_by_id, init_db, update_user_stripe
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
    limiter.reset()


@pytest.fixture
def auth_client(client):
    """A test client logged in as a verified user with active trial."""
    import bcrypt

    from leadclaw.db import create_user, verify_user_email

    email = "stripe-test@example.com"
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    token = "stripe-verify-token"
    user_id = create_user(email, pw_hash, token)
    verify_user_email(user_id)
    # Set trial
    trial_end = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=14)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    update_user_stripe(user_id, subscription_status="trialing", trial_ends_at=trial_end)

    client.post("/login", data={"email": email, "password": "password123"})
    client._test_user_id = user_id
    return client


# ---------------------------------------------------------------------------
# DB migration tests
# ---------------------------------------------------------------------------


def test_stripe_columns_exist_after_init():
    """init_db should create the Stripe columns on users table."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = 1").fetchone()
    # Check Stripe columns are accessible
    assert row["subscription_status"] is not None  # has default 'trialing'
    # stripe_customer_id should be None by default
    assert row["stripe_customer_id"] is None


def test_update_user_stripe():
    """update_user_stripe should set the specified fields."""
    update_user_stripe(1, stripe_customer_id="cus_test123", subscription_status="active")
    row = get_user_by_id(1)
    assert row["stripe_customer_id"] == "cus_test123"
    assert row["subscription_status"] == "active"


def test_update_user_stripe_ignores_invalid_fields():
    """update_user_stripe should silently ignore non-Stripe fields."""
    update_user_stripe(1, email="hacker@evil.com", subscription_status="active")
    row = get_user_by_id(1)
    assert row["subscription_status"] == "active"
    assert row["email"] != "hacker@evil.com"


# ---------------------------------------------------------------------------
# User model tests
# ---------------------------------------------------------------------------


def test_user_has_active_subscription_trialing():
    """User in trial with future end date has active subscription."""
    from leadclaw.web import User

    trial_end = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    update_user_stripe(1, subscription_status="trialing", trial_ends_at=trial_end)
    row = get_user_by_id(1)
    user = User(row)
    assert user.has_active_subscription is True
    assert user.trial_days_remaining >= 6


def test_user_has_active_subscription_expired_trial():
    """User with expired trial does NOT have active subscription."""
    from leadclaw.web import User

    trial_end = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    update_user_stripe(1, subscription_status="trialing", trial_ends_at=trial_end)
    row = get_user_by_id(1)
    user = User(row)
    assert user.has_active_subscription is False
    assert user.trial_days_remaining == 0


def test_user_has_active_subscription_active():
    """User with 'active' status has active subscription regardless of dates."""
    from leadclaw.web import User

    update_user_stripe(1, subscription_status="active")
    row = get_user_by_id(1)
    user = User(row)
    assert user.has_active_subscription is True


def test_user_has_active_subscription_canceled():
    """User with 'canceled' status does NOT have active subscription."""
    from leadclaw.web import User

    update_user_stripe(1, subscription_status="canceled")
    row = get_user_by_id(1)
    user = User(row)
    assert user.has_active_subscription is False


def test_trial_days_remaining_calculation():
    """trial_days_remaining should return correct number of days."""
    from leadclaw.web import User

    trial_end = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    update_user_stripe(1, subscription_status="trialing", trial_ends_at=trial_end)
    row = get_user_by_id(1)
    user = User(row)
    assert 9 <= user.trial_days_remaining <= 10


# ---------------------------------------------------------------------------
# Signup sets trial
# ---------------------------------------------------------------------------


def test_signup_sets_trial(client):
    """New user signup should set a 14-day trial."""
    from leadclaw.web import limiter

    limiter.reset()

    r = client.post(
        "/signup",
        data={"email": "newuser@example.com", "password": "password123", "confirm": "password123"},
    )
    assert r.status_code in (200, 302)

    # Check the user in DB
    from leadclaw.db import get_user_by_email

    row = get_user_by_email("newuser@example.com")
    assert row is not None
    assert row["subscription_status"] == "trialing"
    assert row["trial_ends_at"] is not None
    # Trial should be ~14 days from now
    trial_end = datetime.strptime(row["trial_ends_at"][:19], "%Y-%m-%d %H:%M:%S")
    diff = (trial_end - datetime.now(timezone.utc).replace(tzinfo=None)).days
    assert 13 <= diff <= 14


# ---------------------------------------------------------------------------
# @subscription_required decorator (Stripe disabled — default in tests)
# ---------------------------------------------------------------------------


def test_subscription_not_required_when_stripe_disabled(auth_client):
    """When _STRIPE_ENABLED is False, subscription_required is a no-op."""
    import leadclaw.web as web_mod

    # Ensure Stripe is disabled (default in test env)
    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = False
    try:
        r = auth_client.get("/api/summary")
        assert r.status_code == 200
    finally:
        web_mod._STRIPE_ENABLED = original


def test_subscription_required_blocks_expired_trial(auth_client):
    """When _STRIPE_ENABLED is True and trial expired, user sees 402 paywall."""
    import leadclaw.web as web_mod

    # Set expired trial
    expired = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    update_user_stripe(
        auth_client._test_user_id, subscription_status="trialing", trial_ends_at=expired
    )

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = True
    try:
        r = auth_client.get("/api/summary")
        assert r.status_code == 402
        assert b"trial has ended" in r.data
    finally:
        web_mod._STRIPE_ENABLED = original


def test_subscription_required_allows_active(auth_client):
    """When _STRIPE_ENABLED is True and user has active subscription, access allowed."""
    import leadclaw.web as web_mod

    update_user_stripe(auth_client._test_user_id, subscription_status="active")

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = True
    try:
        r = auth_client.get("/api/summary")
        assert r.status_code == 200
    finally:
        web_mod._STRIPE_ENABLED = original


def test_subscription_required_allows_trial(auth_client):
    """When _STRIPE_ENABLED is True and user in active trial, access allowed."""
    import leadclaw.web as web_mod

    trial_end = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    update_user_stripe(
        auth_client._test_user_id, subscription_status="trialing", trial_ends_at=trial_end
    )

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = True
    try:
        r = auth_client.get("/api/summary")
        assert r.status_code == 200
    finally:
        web_mod._STRIPE_ENABLED = original


# ---------------------------------------------------------------------------
# Billing routes
# ---------------------------------------------------------------------------


def test_billing_checkout_redirects_when_stripe_disabled(auth_client):
    """When Stripe is disabled, /billing/checkout redirects to dashboard."""
    import leadclaw.web as web_mod

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = False
    try:
        r = auth_client.get("/billing/checkout")
        assert r.status_code == 302
        assert "/" in r.headers.get("Location", "")
    finally:
        web_mod._STRIPE_ENABLED = original


def test_billing_portal_redirects_when_stripe_disabled(auth_client):
    """When Stripe is disabled, /billing/portal redirects to dashboard."""
    import leadclaw.web as web_mod

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = False
    try:
        r = auth_client.get("/billing/portal")
        assert r.status_code == 302
    finally:
        web_mod._STRIPE_ENABLED = original


def test_billing_success_page(auth_client):
    """The /billing/success page renders successfully."""
    r = auth_client.get("/billing/success")
    assert r.status_code == 200
    assert b"subscribed" in r.data.lower()


def test_billing_cancel_redirects(auth_client):
    """The /billing/cancel page redirects to dashboard."""
    r = auth_client.get("/billing/cancel")
    assert r.status_code == 302


def test_billing_checkout_creates_stripe_customer(auth_client):
    """When Stripe is enabled, /billing/checkout creates a Stripe customer and session."""
    import leadclaw.web as web_mod

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = True
    try:
        mock_customer = MagicMock()
        mock_customer.id = "cus_test_12345"
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/test"

        with (
            patch("stripe.Customer.create", return_value=mock_customer) as mock_cust_create,
            patch("stripe.checkout.Session.create", return_value=mock_session) as mock_sess_create,
        ):
            r = auth_client.get("/billing/checkout")
            assert r.status_code == 303
            assert mock_cust_create.called
            assert mock_sess_create.called

        # Verify customer ID was saved
        row = get_user_by_id(auth_client._test_user_id)
        assert row["stripe_customer_id"] == "cus_test_12345"
    finally:
        web_mod._STRIPE_ENABLED = original


def test_billing_portal_redirects_to_stripe(auth_client):
    """When user has a Stripe customer ID, /billing/portal redirects to Stripe portal."""
    import leadclaw.web as web_mod

    update_user_stripe(auth_client._test_user_id, stripe_customer_id="cus_portal_test")

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = True
    try:
        mock_portal = MagicMock()
        mock_portal.url = "https://billing.stripe.com/test"

        with patch("stripe.billing_portal.Session.create", return_value=mock_portal):
            r = auth_client.get("/billing/portal")
            assert r.status_code == 303
    finally:
        web_mod._STRIPE_ENABLED = original


# ---------------------------------------------------------------------------
# Webhook tests
# ---------------------------------------------------------------------------


def test_webhook_returns_400_when_stripe_disabled(client):
    """Webhook should return 400 when Stripe is not configured."""
    import leadclaw.web as web_mod

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = False
    try:
        r = client.post("/stripe/webhook", data=b"{}", content_type="application/json")
        assert r.status_code == 400
    finally:
        web_mod._STRIPE_ENABLED = original


def test_webhook_checkout_completed_activates_subscription(client):
    """checkout.session.completed event should activate the user's subscription."""
    import leadclaw.web as web_mod

    # Set up a user with a stripe customer ID
    update_user_stripe(1, stripe_customer_id="cus_webhook_test", subscription_status="trialing")

    original_enabled = web_mod._STRIPE_ENABLED
    original_secret = web_mod._STRIPE_WEBHOOK_SECRET
    web_mod._STRIPE_ENABLED = True
    web_mod._STRIPE_WEBHOOK_SECRET = ""  # disable signature check for test
    try:
        event_payload = json.dumps(
            {
                "id": "evt_test",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "customer": "cus_webhook_test",
                    }
                },
            }
        )

        with patch("stripe.Event.construct_from") as mock_construct:
            mock_construct.return_value = json.loads(event_payload)
            r = client.post(
                "/stripe/webhook",
                data=event_payload,
                content_type="application/json",
            )
            assert r.status_code == 200

        # Verify subscription was activated
        row = get_user_by_id(1)
        assert row["subscription_status"] == "active"
    finally:
        web_mod._STRIPE_ENABLED = original_enabled
        web_mod._STRIPE_WEBHOOK_SECRET = original_secret


def test_webhook_subscription_deleted_cancels(client):
    """customer.subscription.deleted event should cancel the subscription."""
    import leadclaw.web as web_mod

    update_user_stripe(1, stripe_customer_id="cus_cancel_test", subscription_status="active")

    original_enabled = web_mod._STRIPE_ENABLED
    original_secret = web_mod._STRIPE_WEBHOOK_SECRET
    web_mod._STRIPE_ENABLED = True
    web_mod._STRIPE_WEBHOOK_SECRET = ""
    try:
        event_payload = json.dumps(
            {
                "id": "evt_cancel",
                "type": "customer.subscription.deleted",
                "data": {
                    "object": {
                        "customer": "cus_cancel_test",
                    }
                },
            }
        )

        with patch("stripe.Event.construct_from") as mock_construct:
            mock_construct.return_value = json.loads(event_payload)
            r = client.post(
                "/stripe/webhook",
                data=event_payload,
                content_type="application/json",
            )
            assert r.status_code == 200

        row = get_user_by_id(1)
        assert row["subscription_status"] == "canceled"
    finally:
        web_mod._STRIPE_ENABLED = original_enabled
        web_mod._STRIPE_WEBHOOK_SECRET = original_secret


def test_webhook_signature_validation(client):
    """When webhook secret is set, invalid signature should return 400."""
    import leadclaw.web as web_mod

    original_enabled = web_mod._STRIPE_ENABLED
    original_secret = web_mod._STRIPE_WEBHOOK_SECRET
    web_mod._STRIPE_ENABLED = True
    web_mod._STRIPE_WEBHOOK_SECRET = "whsec_test_secret"
    try:
        import stripe

        with patch(
            "stripe.Webhook.construct_event",
            side_effect=stripe.error.SignatureVerificationError("bad sig", "header"),
        ):
            r = client.post(
                "/stripe/webhook",
                data=b'{"type":"test"}',
                content_type="application/json",
                headers={"Stripe-Signature": "invalid_signature"},
            )
            assert r.status_code == 400
            data = json.loads(r.data)
            assert "signature" in data.get("error", "").lower()
    finally:
        web_mod._STRIPE_ENABLED = original_enabled
        web_mod._STRIPE_WEBHOOK_SECRET = original_secret


# ---------------------------------------------------------------------------
# /api/billing endpoint
# ---------------------------------------------------------------------------


def test_api_billing_returns_status(auth_client):
    """The /api/billing endpoint returns billing status."""
    r = auth_client.get("/api/billing")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "stripe_enabled" in data
    assert "subscription_status" in data
    assert "trial_days_remaining" in data
    assert "has_active_subscription" in data


def test_api_billing_reflects_trial(auth_client):
    """The /api/billing endpoint shows correct trial info."""
    trial_end = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=5)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    update_user_stripe(
        auth_client._test_user_id, subscription_status="trialing", trial_ends_at=trial_end
    )

    r = auth_client.get("/api/billing")
    data = json.loads(r.data)
    assert data["subscription_status"] == "trialing"
    assert 4 <= data["trial_days_remaining"] <= 5
    assert data["has_active_subscription"] is True


def test_api_billing_reflects_active(auth_client):
    """The /api/billing endpoint correctly reflects active subscription."""
    update_user_stripe(auth_client._test_user_id, subscription_status="active")

    r = auth_client.get("/api/billing")
    data = json.loads(r.data)
    assert data["subscription_status"] == "active"
    assert data["has_active_subscription"] is True


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_stripe_disabled_no_impact_on_existing_routes(auth_client):
    """When Stripe is not configured, all existing routes work normally."""
    import leadclaw.web as web_mod

    original = web_mod._STRIPE_ENABLED
    web_mod._STRIPE_ENABLED = False
    try:
        # Dashboard
        r = auth_client.get("/")
        assert r.status_code == 200

        # API summary
        r = auth_client.get("/api/summary")
        assert r.status_code == 200

        # API billing should still respond
        r = auth_client.get("/api/billing")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["stripe_enabled"] is False
    finally:
        web_mod._STRIPE_ENABLED = original
