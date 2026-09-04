"""End-to-end API tests: authentication, ownership isolation, and admin gating.

This is the layer no unit test reaches. A service can be perfectly correct
and still leak data if an endpoint forgets its ownership check — the bug
lives in the wiring, not the logic.

Every protected route is asserted twice:
  1. Unauthenticated → 401/403 (catches a missing Depends(get_current_user))
  2. Authenticated as the WRONG user → 403/404, never 200 (catches a
     missing user_id comparison)

Requests go through the real FastAPI app with real signed JWTs, so the
auth dependency, the token 'type' claim, and the is_active check are all
exercised rather than stubbed.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.account import Account, AccountBalance
from app.models.alert import Alert, AlertType
from app.models.conversation import Conversation
from app.models.portfolio import BrokerType, Portfolio
from app.models.watchlist import Watchlist, WatchlistItem


# ── Helpers ───────────────────────────────────────────────────────────


async def _portfolio(db, user, name: str = "P") -> Portfolio:
    p = Portfolio(user_id=user.id, name=name, broker=BrokerType.manual, currency="USD")
    db.add(p)
    await db.flush()
    return p


async def _account(db, user, name: str = "A") -> Account:
    a = Account(user_id=user.id, name=name, primary_currency="USD", is_active=True)
    db.add(a)
    await db.flush()
    db.add(AccountBalance(account_id=a.id, currency="USD", balance=1000))
    await db.flush()
    return a


async def _alert(db, user, ticker: str = "AAPL") -> Alert:
    al = Alert(
        user_id=user.id,
        ticker=ticker,
        alert_type=AlertType.price_above,
        threshold_value=Decimal("100"),
        is_active=True,
        channels="in_app",
    )
    db.add(al)
    await db.flush()
    return al


async def _watchlist(db, user, ticker: str = "AAPL") -> Watchlist:
    wl = Watchlist(user_id=user.id, name="Main")
    db.add(wl)
    await db.flush()
    db.add(WatchlistItem(watchlist_id=wl.id, ticker=ticker))
    await db.flush()
    return wl


async def _conversation(db, user) -> Conversation:
    c = Conversation(user_id=user.id, title="Chat")
    db.add(c)
    await db.flush()
    return c


# Routes that must reject anonymous callers. Kept as data so adding a
# router means adding one line here, and a route that silently loses its
# auth dependency shows up immediately.
PROTECTED_ROUTES = [
    ("GET", "/api/v1/users/me"),
    ("PATCH", "/api/v1/users/me"),
    ("GET", "/api/v1/portfolios/"),
    ("POST", "/api/v1/portfolios/"),
    ("GET", "/api/v1/accounts/"),
    ("POST", "/api/v1/accounts/"),
    ("GET", "/api/v1/accounts/liquidity"),
    ("GET", "/api/v1/alerts/"),
    ("POST", "/api/v1/alerts/"),
    ("GET", "/api/v1/watchlists/"),
    ("GET", "/api/v1/signals/"),
    ("GET", "/api/v1/strategies/"),
    ("GET", "/api/v1/advisor/conversations"),
    ("GET", "/api/v1/briefing/today"),
    ("GET", "/api/v1/market/quote/AAPL"),
    ("GET", "/api/v1/settings/models"),
    ("GET", "/api/v1/settings/models/catalog"),
]


# ── Authentication ────────────────────────────────────────────────────


@pytest.mark.parametrize("method,path", PROTECTED_ROUTES)
@pytest.mark.asyncio
async def test_protected_routes_reject_anonymous(api, method, path):
    r = await api.request(method, path, json={})
    assert r.status_code in (401, 403), (
        f"{method} {path} returned {r.status_code} without auth — "
        f"is Depends(get_current_user) missing?"
    )


@pytest.mark.asyncio
async def test_health_endpoint_is_public(api):
    """The load balancer polls this without credentials."""
    r = await api.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_malformed_token_rejected(api):
    api.headers.update({"Authorization": "Bearer not-a-real-jwt"})
    r = await api.get("/api/v1/users/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_rejected_as_access_token(api, make_user):
    """Tokens carry a 'type' claim. A refresh token must not be usable
    to call protected endpoints — otherwise a leaked long-lived refresh
    token becomes a permanent API key."""
    from app.core.security import create_refresh_token

    user = await make_user("a@example.com")
    api.headers.update({"Authorization": f"Bearer {create_refresh_token(user.id)}"})
    r = await api.get("/api/v1/users/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_deactivated_user_is_rejected(api, make_user, auth_headers):
    """is_active is checked on every request, so deactivating an account
    revokes already-issued tokens rather than waiting for expiry."""
    user = await make_user("gone@example.com", is_active=False)
    api.headers.update(auth_headers(user))
    r = await api.get("/api/v1/users/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_token_for_nonexistent_user_rejected(api):
    from app.core.security import create_access_token

    api.headers.update({"Authorization": f"Bearer {create_access_token(999_999)}"})
    r = await api.get("/api/v1/users/me")
    assert r.status_code == 401


# ── Ownership: portfolios ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portfolio_list_only_shows_own(db, api, make_user, auth_headers):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    await _portfolio(db, alice, "Alice's")
    await _portfolio(db, bob, "Bob's")

    api.headers.update(auth_headers(alice))
    r = await api.get("/api/v1/portfolios/")
    assert r.status_code == 200
    assert [p["name"] for p in r.json()] == ["Alice's"]


# Bodies must be schema-valid, otherwise Pydantic 422s before the
# ownership check ever runs and the test proves nothing.
_VALID_TRADE = {
    "ticker": "AAPL",
    "action": "buy",
    "quantity": 1,
    "price": 100,
    "traded_at": "2026-01-01T00:00:00Z",
}


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("GET", "", None),
        ("PATCH", "", {"name": "hijacked"}),
        ("DELETE", "", None),
        ("GET", "/positions", None),
        ("GET", "/trades", None),
        ("POST", "/trades", _VALID_TRADE),
        ("GET", "/performance", None),
        ("GET", "/risk", None),
    ],
)
@pytest.mark.asyncio
async def test_cannot_touch_another_users_portfolio(
    db, api, make_user, auth_headers, method, suffix, body
):
    """Every portfolio sub-resource must be gated. A missing check on any
    one of these leaks another user's holdings."""
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    bobs = await _portfolio(db, bob, "Bob's")

    api.headers.update(auth_headers(alice))
    r = await api.request(
        method, f"/api/v1/portfolios/{bobs.id}{suffix}", json=body or {}
    )
    assert r.status_code in (403, 404), (
        f"{method} portfolios/{{id}}{suffix} returned {r.status_code} "
        f"for a portfolio owned by someone else"
    )


@pytest.mark.asyncio
async def test_foreign_portfolio_write_creates_nothing(db, api, make_user, auth_headers):
    """Status code alone isn't proof — assert no Trade row appeared on
    the victim's portfolio."""
    from sqlalchemy import select

    from app.models.portfolio import Trade

    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    bobs = await _portfolio(db, bob, "Bob's")

    api.headers.update(auth_headers(alice))
    await api.post(f"/api/v1/portfolios/{bobs.id}/trades", json=_VALID_TRADE)

    rows = (await db.execute(
        select(Trade).where(Trade.portfolio_id == bobs.id)
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_missing_portfolio_is_404_not_500(db, api, make_user, auth_headers):
    alice = await make_user("alice@example.com")
    api.headers.update(auth_headers(alice))
    r = await api.get("/api/v1/portfolios/999999")
    assert r.status_code == 404


# ── Ownership: accounts (cash — the money surface) ────────────────────


@pytest.mark.asyncio
async def test_account_list_only_shows_own(db, api, make_user, auth_headers):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    await _account(db, alice, "Alice Cash")
    await _account(db, bob, "Bob Cash")

    api.headers.update(auth_headers(alice))
    r = await api.get("/api/v1/accounts/")
    assert r.status_code == 200
    assert [a["name"] for a in r.json()] == ["Alice Cash"]


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("GET", "", None),
        ("PATCH", "", {"name": "hijacked"}),
        ("DELETE", "", None),
        ("GET", "/transactions", None),
        ("POST", "/deposit", {"currency": "USD", "amount": 1000}),
        ("POST", "/withdraw", {"currency": "USD", "amount": 1000}),
    ],
)
@pytest.mark.asyncio
async def test_cannot_touch_another_users_account(
    db, api, make_user, auth_headers, method, suffix, body
):
    """Deposit/withdraw on someone else's account would be direct theft."""
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    bobs = await _account(db, bob, "Bob Cash")

    api.headers.update(auth_headers(alice))
    r = await api.request(method, f"/api/v1/accounts/{bobs.id}{suffix}", json=body or {})
    assert r.status_code in (403, 404), (
        f"{method} accounts/{{id}}{suffix} returned {r.status_code}"
    )


@pytest.mark.asyncio
async def test_failed_withdraw_on_foreign_account_does_not_move_money(
    db, api, make_user, auth_headers
):
    """Belt and braces: assert the balance, not just the status code."""
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    bobs = await _account(db, bob, "Bob Cash")

    api.headers.update(auth_headers(alice))
    await api.post(
        f"/api/v1/accounts/{bobs.id}/withdraw",
        json={"currency": "USD", "amount": 500},
    )

    await db.refresh(bobs, ["balances"])
    assert float(bobs.balances[0].balance) == pytest.approx(1000.0)


# ── Ownership: alerts ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alert_list_only_shows_own(db, api, make_user, auth_headers):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    await _alert(db, alice, "AAPL")
    await _alert(db, bob, "TSLA")

    api.headers.update(auth_headers(alice))
    r = await api.get("/api/v1/alerts/")
    assert r.status_code == 200
    assert [a["ticker"] for a in r.json()] == ["AAPL"]


@pytest.mark.parametrize(
    "method,suffix",
    [("PATCH", ""), ("DELETE", ""), ("POST", "/acknowledge"), ("POST", "/rearm")],
)
@pytest.mark.asyncio
async def test_cannot_touch_another_users_alert(
    db, api, make_user, auth_headers, method, suffix
):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    bobs = await _alert(db, bob, "TSLA")

    api.headers.update(auth_headers(alice))
    r = await api.request(method, f"/api/v1/alerts/{bobs.id}{suffix}", json={})
    assert r.status_code in (403, 404)


# ── Ownership: watchlists ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watchlist_list_only_shows_own(db, api, make_user, auth_headers):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    await _watchlist(db, alice, "AAPL")
    await _watchlist(db, bob, "TSLA")

    api.headers.update(auth_headers(alice))
    r = await api.get("/api/v1/watchlists/")
    assert r.status_code == 200
    tickers = [i["ticker"] for wl in r.json() for i in wl["items"]]
    assert tickers == ["AAPL"]


@pytest.mark.asyncio
async def test_cannot_add_to_another_users_watchlist(
    db, api, make_user, auth_headers
):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    bobs = await _watchlist(db, bob, "TSLA")

    api.headers.update(auth_headers(alice))
    r = await api.post(f"/api/v1/watchlists/{bobs.id}/items", json={"ticker": "NVDA"})
    assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_cannot_delete_from_another_users_watchlist(
    db, api, make_user, auth_headers
):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    bobs = await _watchlist(db, bob, "TSLA")

    api.headers.update(auth_headers(alice))
    r = await api.delete(f"/api/v1/watchlists/{bobs.id}/items/TSLA")
    assert r.status_code in (403, 404)


# ── Ownership: conversations (chat history is sensitive) ──────────────


@pytest.mark.asyncio
async def test_conversation_list_only_shows_own(db, api, make_user, auth_headers):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    await _conversation(db, alice)
    await _conversation(db, bob)

    api.headers.update(auth_headers(alice))
    r = await api.get("/api/v1/advisor/conversations")
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.parametrize("method", ["GET", "DELETE"])
@pytest.mark.asyncio
async def test_cannot_read_or_delete_another_users_conversation(
    db, api, make_user, auth_headers, method
):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    bobs = await _conversation(db, bob)

    api.headers.update(auth_headers(alice))
    r = await api.request(method, f"/api/v1/advisor/conversations/{bobs.id}")
    assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_cannot_chat_against_another_users_portfolio(
    db, api, make_user, auth_headers
):
    """The advisor takes a portfolio_id for context — passing someone
    else's would leak their holdings into the model's reply."""
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    bobs = await _portfolio(db, bob, "Bob's")

    api.headers.update(auth_headers(alice))
    r = await api.post(
        "/api/v1/advisor/chat",
        json={"message": "what do I own?", "portfolio_id": bobs.id},
    )
    assert r.status_code in (403, 404)


# ── Admin gating ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/v1/settings/models", None),
        ("GET", "/api/v1/settings/models/catalog", None),
        ("PUT", "/api/v1/settings/models", {"news_model": "gemini-2.5-flash"}),
    ],
)
@pytest.mark.asyncio
async def test_settings_routes_require_admin(
    api, make_user, auth_headers, method, path, body
):
    """Model selection is global and billed to the operator's API keys —
    a non-admin must not be able to read the catalog or switch models."""
    plain = await make_user("plain@example.com", is_admin=False)
    api.headers.update(auth_headers(plain))
    r = await api.request(method, path, json=body or {})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_read_model_settings(api, make_user, auth_headers):
    admin = await make_user("admin@example.com", is_admin=True)
    api.headers.update(auth_headers(admin))
    r = await api.get("/api/v1/settings/models")
    assert r.status_code == 200
    assert "news_model" in r.json()


@pytest.mark.asyncio
async def test_users_me_exposes_is_admin_flag(api, make_user, auth_headers):
    """The frontend gates the admin card on this field, so it must be
    present and correct for both roles."""
    admin = await make_user("admin@example.com", is_admin=True)
    api.headers.update(auth_headers(admin))
    assert (await api.get("/api/v1/users/me")).json()["is_admin"] is True

    plain = await make_user("plain@example.com", is_admin=False)
    api.headers.update(auth_headers(plain))
    assert (await api.get("/api/v1/users/me")).json()["is_admin"] is False


# ── Auth flow ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_then_login_issues_working_token(api):
    reg = await api.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": "hunter2hunter2", "full_name": "New"},
    )
    assert reg.status_code == 201, reg.text

    login = await api.post(
        "/api/v1/auth/login",
        json={"email": "new@example.com", "password": "hunter2hunter2"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    api.headers.update({"Authorization": f"Bearer {token}"})
    me = await api.get("/api/v1/users/me")
    assert me.status_code == 200
    assert me.json()["email"] == "new@example.com"
    # New users must never be admin by default.
    assert me.json()["is_admin"] is False


@pytest.mark.asyncio
async def test_login_with_wrong_password_rejected(api, make_user):
    await make_user("a@example.com", password="correct-horse-battery")
    r = await api.post(
        "/api/v1/auth/login",
        json={"email": "a@example.com", "password": "wrong-password"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_duplicate_registration_rejected(api, make_user):
    await make_user("taken@example.com")
    r = await api.post(
        "/api/v1/auth/register",
        json={"email": "taken@example.com", "password": "hunter2hunter2"},
    )
    assert r.status_code in (400, 409)
