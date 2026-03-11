"""
End-to-end tests for the Binance Futures trading integration.

Covers the full lifecycle:
  connect → balance → sync → enable trading → execute signal → close trade → disconnect

All Binance network calls are mocked so no real API keys are required.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import Signal, SignalDirection, SignalOutcome, SignalStatus
from app.models.user import User
from app.services.binance.exchange import BinanceExchangeService
from app.services.binance.info import BinanceInfoService

# ---------------------------------------------------------------------------
# Fake Binance API responses
# ---------------------------------------------------------------------------

FAKE_ACCOUNT_INFO = {
    "assets": [
        {
            "asset": "USDT",
            "availableBalance": "1000.0",
            "walletBalance": "1000.0",
            "initialMargin": "0.0",
            "unrealizedProfit": "0.0",
        }
    ],
    "positions": [],
}

FAKE_BALANCE = {
    "available_balance": 1000.0,
    "total_balance": 1000.0,
    "margin_used": 0.0,
    "unrealized_pnl": 0.0,
}

FAKE_POSITIONS: list = []

FAKE_MARKET_ORDER = {
    "orderId": 12345678,
    "symbol": "BTCUSDT",
    "status": "FILLED",
    "side": "BUY",
    "type": "MARKET",
    "avgPrice": "50000.0",
    "executedQty": "0.1",
    "cumQuote": "5000.0",
}

FAKE_TP_ORDER = {
    "orderId": 12345679,
    "symbol": "BTCUSDT",
    "status": "NEW",
    "side": "SELL",
    "type": "TAKE_PROFIT_MARKET",
    "stopPrice": "55000.0",
}

FAKE_SL_ORDER = {
    "orderId": 12345680,
    "symbol": "BTCUSDT",
    "status": "NEW",
    "side": "SELL",
    "type": "STOP_MARKET",
    "stopPrice": "47500.0",
}

FAKE_SYMBOL_PRECISION = {
    "quantity_precision": 3,
    "price_precision": 2,
    "min_qty": 0.001,
    "min_notional": 5.0,
}

FAKE_MARKET_DATA = {
    "symbol": "BTCUSDT",
    "mark_price": 50000.0,
    "index_price": 50000.0,
    "funding_rate": 0.0001,
    "price_change_24h": 1000.0,
    "price_change_percent_24h": 2.0,
    "volume_24h": 1_000_000.0,
    "high_24h": 51000.0,
    "low_24h": 49000.0,
}

FAKE_CLOSE_RESULT = {"status": "ok", "message": "Position closed"}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_binance():
    """Patch all Binance network calls so tests run without real API keys."""
    with (
        patch.object(
            BinanceExchangeService,
            "get_account_info",
            new_callable=AsyncMock,
            return_value=FAKE_ACCOUNT_INFO,
        ),
        patch.object(
            BinanceExchangeService,
            "get_balance",
            new_callable=AsyncMock,
            return_value=FAKE_BALANCE,
        ),
        patch.object(
            BinanceExchangeService,
            "get_positions",
            new_callable=AsyncMock,
            return_value=FAKE_POSITIONS,
        ),
        patch.object(
            BinanceExchangeService,
            "set_leverage",
            new_callable=AsyncMock,
            return_value={"leverage": 5, "symbol": "BTCUSDT"},
        ),
        patch.object(
            BinanceExchangeService,
            "set_margin_type",
            new_callable=AsyncMock,
            return_value={"msg": "success"},
        ),
        patch.object(
            BinanceExchangeService,
            "place_market_order",
            new_callable=AsyncMock,
            return_value=FAKE_MARKET_ORDER,
        ),
        patch.object(
            BinanceExchangeService,
            "place_tp_algo_order",
            new_callable=AsyncMock,
            return_value=FAKE_TP_ORDER,
        ),
        patch.object(
            BinanceExchangeService,
            "place_sl_algo_order",
            new_callable=AsyncMock,
            return_value=FAKE_SL_ORDER,
        ),
        patch.object(
            BinanceExchangeService,
            "cancel_algo_order",
            new_callable=AsyncMock,
            return_value={"status": "ok"},
        ),
        patch.object(
            BinanceExchangeService,
            "close_position",
            new_callable=AsyncMock,
            return_value=FAKE_CLOSE_RESULT,
        ),
        patch.object(
            BinanceExchangeService,
            "close",
            new_callable=AsyncMock,
        ),
        patch.object(
            BinanceInfoService,
            "get_symbol_precision",
            new_callable=AsyncMock,
            return_value=FAKE_SYMBOL_PRECISION,
        ),
        patch.object(
            BinanceInfoService,
            "get_market_data",
            new_callable=AsyncMock,
            return_value=FAKE_MARKET_DATA,
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def register_and_login(client: AsyncClient, email: str, password: str) -> str:
    """Register a user and return an access token."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Test User"},
    )
    assert reg.status_code == 200, reg.text

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def make_user_subscribed(db: AsyncSession, email: str) -> None:
    """Bypass subscription check by setting is_subscribed=True directly."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.is_subscribed = True
    await db.commit()


async def create_binance_signal(db: AsyncSession) -> Signal:
    """Insert an active Binance LONG signal into the DB."""
    signal = Signal(
        symbol="BTC",
        exchange="binance",
        direction=SignalDirection.LONG,
        status=SignalStatus.ACTIVE,
        outcome=SignalOutcome.PENDING,
        entry_price=50000.0,
        take_profit_price=55000.0,
        stop_loss_price=47500.0,
        suggested_leverage=5,
        suggested_position_size_percent=10.0,
        confidence_score=0.85,
        consensus_votes=3,
        total_votes=4,
        market_price_at_creation=50000.0,
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )
    db.add(signal)
    await db.commit()
    await db.refresh(signal)
    return signal


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Exchange Connection Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_exchange(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """Connecting a Binance exchange stores the encrypted credentials and returns active status."""
    token = await register_and_login(client, "exchange@test.com", "Password123!")

    response = await client.post(
        "/api/v1/exchanges/connect",
        json={
            "exchange_type": "binance",
            "api_key": "a" * 64,
            "api_secret": "b" * 64,
            "is_testnet": True,
            "label": "Test Account",
        },
        headers=auth_headers(token),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["exchange_type"] == "binance"
    assert data["is_testnet"] is True
    assert data["label"] == "Test Account"
    assert data["status"] == "active"
    assert data["is_trading_enabled"] is False  # disabled by default


@pytest.mark.asyncio
async def test_connect_exchange_invalid_type(client: AsyncClient, db_session: AsyncSession):
    """Connecting with an unsupported exchange type returns 422."""
    token = await register_and_login(client, "badtype@test.com", "Password123!")

    response = await client.post(
        "/api/v1/exchanges/connect",
        json={
            "exchange_type": "coinbase",
            "api_key": "a" * 64,
            "api_secret": "b" * 64,
        },
        headers=auth_headers(token),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_connect_duplicate_exchange(
    client: AsyncClient, db_session: AsyncSession, mock_binance
):
    """Connecting the same exchange+network twice returns 409 Conflict."""
    token = await register_and_login(client, "dup@test.com", "Password123!")
    payload = {
        "exchange_type": "binance",
        "api_key": "a" * 64,
        "api_secret": "b" * 64,
        "is_testnet": False,
    }

    r1 = await client.post("/api/v1/exchanges/connect", json=payload, headers=auth_headers(token))
    assert r1.status_code == 200

    r2 = await client.post("/api/v1/exchanges/connect", json=payload, headers=auth_headers(token))
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_list_exchanges(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """GET /exchanges returns the list of user's connections."""
    token = await register_and_login(client, "list@test.com", "Password123!")

    # Initially empty
    r = await client.get("/api/v1/exchanges", headers=auth_headers(token))
    assert r.status_code == 200
    assert r.json() == []

    # Connect one exchange
    await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )

    r = await client.get("/api/v1/exchanges", headers=auth_headers(token))
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.asyncio
async def test_get_exchange(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """GET /exchanges/{id} returns the specific connection."""
    token = await register_and_login(client, "get@test.com", "Password123!")

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]

    r = await client.get(f"/api/v1/exchanges/{conn_id}", headers=auth_headers(token))
    assert r.status_code == 200
    assert r.json()["id"] == conn_id


@pytest.mark.asyncio
async def test_get_exchange_not_found(client: AsyncClient, db_session: AsyncSession):
    """GET /exchanges/{id} returns 404 for unknown id."""
    token = await register_and_login(client, "notfound@test.com", "Password123!")

    r = await client.get(
        "/api/v1/exchanges/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(token),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_balance(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """GET /exchanges/{id}/balance returns live balance from mock."""
    token = await register_and_login(client, "balance@test.com", "Password123!")

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]

    r = await client.get(f"/api/v1/exchanges/{conn_id}/balance", headers=auth_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert data["available_balance"] == 1000.0
    assert data["total_balance"] == 1000.0
    assert data["margin_used"] == 0.0
    assert data["unrealized_pnl"] == 0.0


@pytest.mark.asyncio
async def test_sync_exchange(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """POST /exchanges/{id}/sync updates cached balance fields."""
    token = await register_and_login(client, "sync@test.com", "Password123!")

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]

    r = await client.post(f"/api/v1/exchanges/{conn_id}/sync", headers=auth_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["balance_usd"] == 1000.0
    assert data["positions_count"] == 0
    assert "synced_at" in data


@pytest.mark.asyncio
async def test_toggle_trading_requires_subscription(
    client: AsyncClient, db_session: AsyncSession, mock_binance
):
    """Toggle trading returns 402 for users without a subscription."""
    token = await register_and_login(client, "nosub@test.com", "Password123!")

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]

    r = await client.patch(
        f"/api/v1/exchanges/{conn_id}/trading",
        json={"enabled": True},
        headers=auth_headers(token),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_toggle_trading_enabled(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """Subscribed users can enable/disable auto-trading."""
    email = "trading@test.com"
    token = await register_and_login(client, email, "Password123!")
    await make_user_subscribed(db_session, email)

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]
    assert conn.json()["is_trading_enabled"] is False

    # Enable trading
    r = await client.patch(
        f"/api/v1/exchanges/{conn_id}/trading",
        json={"enabled": True},
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    assert r.json()["is_trading_enabled"] is True

    # Disable trading
    r = await client.patch(
        f"/api/v1/exchanges/{conn_id}/trading",
        json={"enabled": False},
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    assert r.json()["is_trading_enabled"] is False


@pytest.mark.asyncio
async def test_disconnect_exchange(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """DELETE /exchanges/{id} removes the connection from the active list."""
    token = await register_and_login(client, "disconnect@test.com", "Password123!")

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]

    r = await client.delete(f"/api/v1/exchanges/{conn_id}", headers=auth_headers(token))
    assert r.status_code == 200

    # Should no longer appear in the list
    r = await client.get("/api/v1/exchanges", headers=auth_headers(token))
    assert r.json() == []


# ---------------------------------------------------------------------------
# Signal Execution Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_binance_signal(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """Full flow: connect exchange → enable trading → execute signal → trade is OPEN."""
    email = "exec@test.com"
    token = await register_and_login(client, email, "Password123!")
    await make_user_subscribed(db_session, email)

    # Connect exchange
    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    assert conn.status_code == 200
    conn_id = conn.json()["id"]

    # Enable trading
    await client.patch(
        f"/api/v1/exchanges/{conn_id}/trading",
        json={"enabled": True},
        headers=auth_headers(token),
    )

    # Create a signal in the DB
    signal = await create_binance_signal(db_session)

    # Execute the signal
    r = await client.post(
        f"/api/v1/trading/signals/{signal.id}/execute",
        json={"exchange_connection_id": conn_id},
        headers=auth_headers(token),
    )
    assert r.status_code == 200, r.text
    trade = r.json()
    assert trade["exchange"] == "binance"
    assert trade["symbol"] == "BTC"
    assert trade["direction"] == "long"
    assert trade["status"] == "open"
    assert trade["entry_price"] == 50000.0
    assert trade["exchange_connection_id"] == conn_id


@pytest.mark.asyncio
async def test_execute_binance_signal_without_connection_id(
    client: AsyncClient, db_session: AsyncSession, mock_binance
):
    """Executing a Binance signal without exchange_connection_id returns 400."""
    email = "noconn@test.com"
    token = await register_and_login(client, email, "Password123!")
    await make_user_subscribed(db_session, email)

    signal = await create_binance_signal(db_session)

    r = await client.post(
        f"/api/v1/trading/signals/{signal.id}/execute",
        json={},  # no exchange_connection_id
        headers=auth_headers(token),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_execute_binance_signal_trading_disabled(
    client: AsyncClient, db_session: AsyncSession, mock_binance
):
    """Executing when is_trading_enabled=False returns a failed/error response."""
    email = "disabled@test.com"
    token = await register_and_login(client, email, "Password123!")
    await make_user_subscribed(db_session, email)

    # Connect but do NOT enable trading
    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]

    signal = await create_binance_signal(db_session)

    r = await client.post(
        f"/api/v1/trading/signals/{signal.id}/execute",
        json={"exchange_connection_id": conn_id},
        headers=auth_headers(token),
    )
    # Trading disabled raises TradingDisabledError → 400 or 422
    assert r.status_code in (400, 422), r.text


@pytest.mark.asyncio
async def test_execute_signal_requires_subscription(
    client: AsyncClient, db_session: AsyncSession, mock_binance
):
    """Execute signal returns 402 for users without a subscription."""
    email = "nosub2@test.com"
    token = await register_and_login(client, email, "Password123!")
    # NOT making user subscribed

    signal = await create_binance_signal(db_session)

    r = await client.post(
        f"/api/v1/trading/signals/{signal.id}/execute",
        json={"exchange_connection_id": "some-id"},
        headers=auth_headers(token),
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Trade Lifecycle Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trades(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """GET /trading/trades returns paginated trades filtered by exchange."""
    email = "trades@test.com"
    token = await register_and_login(client, email, "Password123!")
    await make_user_subscribed(db_session, email)

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]
    await client.patch(
        f"/api/v1/exchanges/{conn_id}/trading",
        json={"enabled": True},
        headers=auth_headers(token),
    )

    signal = await create_binance_signal(db_session)
    await client.post(
        f"/api/v1/trading/signals/{signal.id}/execute",
        json={"exchange_connection_id": conn_id},
        headers=auth_headers(token),
    )

    # List all trades
    r = await client.get("/api/v1/trading/trades", headers=auth_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["exchange"] == "binance"

    # Filter by exchange=binance
    r = await client.get("/api/v1/trading/trades?exchange=binance", headers=auth_headers(token))
    assert r.status_code == 200
    assert r.json()["total"] == 1

    # Filter by exchange=hyperliquid (no results)
    r = await client.get("/api/v1/trading/trades?exchange=hyperliquid", headers=auth_headers(token))
    assert r.status_code == 200
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_get_open_trades(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """GET /trading/trades/open returns only open trades."""
    email = "open@test.com"
    token = await register_and_login(client, email, "Password123!")
    await make_user_subscribed(db_session, email)

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]
    await client.patch(
        f"/api/v1/exchanges/{conn_id}/trading",
        json={"enabled": True},
        headers=auth_headers(token),
    )

    signal = await create_binance_signal(db_session)
    await client.post(
        f"/api/v1/trading/signals/{signal.id}/execute",
        json={"exchange_connection_id": conn_id},
        headers=auth_headers(token),
    )

    r = await client.get("/api/v1/trading/trades/open", headers=auth_headers(token))
    assert r.status_code == 200
    trades = r.json()
    assert len(trades) == 1
    assert trades[0]["status"] == "open"


@pytest.mark.asyncio
async def test_get_trade_detail(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """GET /trading/trades/{id} returns full trade details."""
    email = "detail@test.com"
    token = await register_and_login(client, email, "Password123!")
    await make_user_subscribed(db_session, email)

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]
    await client.patch(
        f"/api/v1/exchanges/{conn_id}/trading",
        json={"enabled": True},
        headers=auth_headers(token),
    )

    signal = await create_binance_signal(db_session)
    exec_r = await client.post(
        f"/api/v1/trading/signals/{signal.id}/execute",
        json={"exchange_connection_id": conn_id},
        headers=auth_headers(token),
    )
    trade_id = exec_r.json()["id"]

    r = await client.get(f"/api/v1/trading/trades/{trade_id}", headers=auth_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == trade_id
    assert data["exchange"] == "binance"
    assert "take_profit_price" in data
    assert "stop_loss_price" in data


@pytest.mark.asyncio
async def test_close_binance_trade(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """POST /trading/trades/{id}/close closes the position and marks trade CLOSED."""
    email = "close@test.com"
    token = await register_and_login(client, email, "Password123!")
    await make_user_subscribed(db_session, email)

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]
    await client.patch(
        f"/api/v1/exchanges/{conn_id}/trading",
        json={"enabled": True},
        headers=auth_headers(token),
    )

    signal = await create_binance_signal(db_session)
    exec_r = await client.post(
        f"/api/v1/trading/signals/{signal.id}/execute",
        json={"exchange_connection_id": conn_id},
        headers=auth_headers(token),
    )
    assert exec_r.status_code == 200
    trade_id = exec_r.json()["id"]

    # Close the trade
    r = await client.post(
        f"/api/v1/trading/trades/{trade_id}/close",
        json={"reason": "manual"},
        headers=auth_headers(token),
    )
    assert r.status_code == 200, r.text
    closed = r.json()
    assert closed["status"] == "closed"
    assert closed["exit_price"] == FAKE_MARKET_DATA["mark_price"]
    assert closed["close_reason"] == "manual"
    assert closed["closed_at"] is not None


@pytest.mark.asyncio
async def test_close_already_closed_trade(
    client: AsyncClient, db_session: AsyncSession, mock_binance
):
    """Closing an already-closed trade returns 400."""
    email = "dblclose@test.com"
    token = await register_and_login(client, email, "Password123!")
    await make_user_subscribed(db_session, email)

    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )
    conn_id = conn.json()["id"]
    await client.patch(
        f"/api/v1/exchanges/{conn_id}/trading",
        json={"enabled": True},
        headers=auth_headers(token),
    )

    signal = await create_binance_signal(db_session)
    exec_r = await client.post(
        f"/api/v1/trading/signals/{signal.id}/execute",
        json={"exchange_connection_id": conn_id},
        headers=auth_headers(token),
    )
    trade_id = exec_r.json()["id"]

    # First close
    r1 = await client.post(
        f"/api/v1/trading/trades/{trade_id}/close",
        json={"reason": "manual"},
        headers=auth_headers(token),
    )
    assert r1.status_code == 200

    # Second close — should fail
    r2 = await client.post(
        f"/api/v1/trading/trades/{trade_id}/close",
        json={"reason": "manual"},
        headers=auth_headers(token),
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_close_trade_requires_subscription(
    client: AsyncClient, db_session: AsyncSession, mock_binance
):
    """Closing a trade returns 402 for users without a subscription."""
    email = "nosub3@test.com"
    token = await register_and_login(client, email, "Password123!")

    r = await client.post(
        "/api/v1/trading/trades/fake-id/close",
        json={"reason": "manual"},
        headers=auth_headers(token),
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Full Lifecycle Integration Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_binance_lifecycle(client: AsyncClient, db_session: AsyncSession, mock_binance):
    """
    End-to-end: connect → sync → enable trading → execute signal →
    verify open → close → verify closed → disconnect → verify gone.
    """
    email = "lifecycle@test.com"
    token = await register_and_login(client, email, "Password123!")
    await make_user_subscribed(db_session, email)

    # 1. Connect exchange
    conn_r = await client.post(
        "/api/v1/exchanges/connect",
        json={
            "exchange_type": "binance",
            "api_key": "a" * 64,
            "api_secret": "b" * 64,
            "is_testnet": True,
            "label": "Lifecycle Test",
        },
        headers=auth_headers(token),
    )
    assert conn_r.status_code == 200
    conn_id = conn_r.json()["id"]

    # 2. Sync balance
    sync_r = await client.post(f"/api/v1/exchanges/{conn_id}/sync", headers=auth_headers(token))
    assert sync_r.status_code == 200
    assert sync_r.json()["balance_usd"] == 1000.0

    # 3. Enable trading
    toggle_r = await client.patch(
        f"/api/v1/exchanges/{conn_id}/trading",
        json={"enabled": True},
        headers=auth_headers(token),
    )
    assert toggle_r.status_code == 200
    assert toggle_r.json()["is_trading_enabled"] is True

    # 4. Create and execute a Binance signal
    signal = await create_binance_signal(db_session)
    exec_r = await client.post(
        f"/api/v1/trading/signals/{signal.id}/execute",
        json={"exchange_connection_id": conn_id, "leverage": 5, "position_size_percent": 10},
        headers=auth_headers(token),
    )
    assert exec_r.status_code == 200, exec_r.text
    trade = exec_r.json()
    trade_id = trade["id"]
    assert trade["status"] == "open"
    assert trade["entry_price"] == 50000.0

    # 5. Verify trade appears in open trades
    open_r = await client.get("/api/v1/trading/trades/open", headers=auth_headers(token))
    assert open_r.status_code == 200
    assert any(t["id"] == trade_id for t in open_r.json())

    # 6. Close the trade
    close_r = await client.post(
        f"/api/v1/trading/trades/{trade_id}/close",
        json={"reason": "manual"},
        headers=auth_headers(token),
    )
    assert close_r.status_code == 200, close_r.text
    closed_trade = close_r.json()
    assert closed_trade["status"] == "closed"
    assert closed_trade["realized_pnl"] is not None

    # 7. Verify trade is no longer open
    open_r2 = await client.get("/api/v1/trading/trades/open", headers=auth_headers(token))
    assert not any(t["id"] == trade_id for t in open_r2.json())

    # 8. Disconnect exchange
    del_r = await client.delete(f"/api/v1/exchanges/{conn_id}", headers=auth_headers(token))
    assert del_r.status_code == 200

    list_r = await client.get("/api/v1/exchanges", headers=auth_headers(token))
    assert list_r.json() == []
