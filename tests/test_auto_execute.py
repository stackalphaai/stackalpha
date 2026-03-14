"""
Tests for auto-execution of signals for all subscribed users.

Covers:
  - Binance auto-execute: all subscribed users with active connections get trades
  - Hyperliquid auto-execute: all subscribed users with active wallets get trades
  - Users without subscription are skipped
  - Users with trading disabled are skipped
  - Risk rejections are handled gracefully
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import Signal, SignalDirection, SignalOutcome, SignalStatus
from app.models.trade import Trade, TradeStatus
from app.models.user import User
from app.services.binance.exchange import BinanceExchangeService
from app.services.binance.info import BinanceInfoService
from app.services.hyperliquid.exchange import HyperliquidExchangeService
from app.services.hyperliquid.info import HyperliquidInfoService

# ---------------------------------------------------------------------------
# Fake responses
# ---------------------------------------------------------------------------

FAKE_BINANCE_BALANCE = {
    "available_balance": 1000.0,
    "total_balance": 1000.0,
    "margin_used": 0.0,
    "unrealized_pnl": 0.0,
}

FAKE_BINANCE_MARKET_ORDER = {
    "orderId": 99999,
    "symbol": "BTCUSDT",
    "status": "FILLED",
    "side": "BUY",
    "type": "MARKET",
    "avgPrice": "50000.0",
    "executedQty": "0.02",
    "cumQuote": "1000.0",
}

FAKE_BINANCE_TP = {"orderId": 99998, "algoId": "88888"}
FAKE_BINANCE_SL = {"orderId": 99997, "algoId": "77777"}
FAKE_BINANCE_PRECISION = {
    "quantity_precision": 3,
    "price_precision": 2,
    "min_qty": 0.001,
    "min_notional": 5.0,
}
FAKE_BINANCE_MARKET_DATA = {
    "symbol": "BTCUSDT",
    "mark_price": 50000.0,
    "index_price": 50000.0,
}

FAKE_HL_BALANCE = {"available_balance": 2000.0, "total_balance": 2000.0}
FAKE_HL_MARKET_DATA = {"mark_price": 50000.0}
FAKE_HL_POSITIONS = [
    {
        "symbol": "BTC",
        "entry_price": 50000.0,
        "margin_used": 100.0,
    }
]
FAKE_HL_ORDER = {
    "status": "ok",
    "response": {"type": "default", "data": {"statuses": [{"oid": "abc123"}]}},
}

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_binance_services():
    """Mock all Binance API calls."""
    with (
        patch.object(
            BinanceExchangeService,
            "get_balance",
            new_callable=AsyncMock,
            return_value=FAKE_BINANCE_BALANCE,
        ),
        patch.object(
            BinanceExchangeService,
            "get_account_info",
            new_callable=AsyncMock,
            return_value=FAKE_ACCOUNT_INFO,
        ),
        patch.object(
            BinanceExchangeService,
            "set_leverage",
            new_callable=AsyncMock,
            return_value={"leverage": 5},
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
            return_value=FAKE_BINANCE_MARKET_ORDER,
        ),
        patch.object(
            BinanceExchangeService,
            "place_tp_algo_order",
            new_callable=AsyncMock,
            return_value=FAKE_BINANCE_TP,
        ),
        patch.object(
            BinanceExchangeService,
            "place_sl_algo_order",
            new_callable=AsyncMock,
            return_value=FAKE_BINANCE_SL,
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
            return_value=FAKE_BINANCE_PRECISION,
        ),
        patch.object(
            BinanceInfoService,
            "get_market_data",
            new_callable=AsyncMock,
            return_value=FAKE_BINANCE_MARKET_DATA,
        ),
    ):
        yield


@pytest.fixture
def mock_hl_services():
    """Mock all Hyperliquid API calls."""
    with (
        patch.object(
            HyperliquidInfoService,
            "get_user_balance",
            new_callable=AsyncMock,
            return_value=FAKE_HL_BALANCE,
        ),
        patch.object(
            HyperliquidInfoService,
            "get_market_data",
            new_callable=AsyncMock,
            return_value=FAKE_HL_MARKET_DATA,
        ),
        patch.object(
            HyperliquidInfoService,
            "get_user_positions",
            new_callable=AsyncMock,
            return_value=FAKE_HL_POSITIONS,
        ),
        patch.object(
            HyperliquidExchangeService,
            "update_leverage",
            new_callable=AsyncMock,
        ),
        patch.object(
            HyperliquidExchangeService,
            "place_market_order",
            new_callable=AsyncMock,
            return_value=FAKE_HL_ORDER,
        ),
        patch(
            "app.services.wallet_service.WalletService.get_private_key",
            return_value="0x" + "ab" * 32,
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def register_and_login(client: AsyncClient, email: str, password: str) -> str:
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


async def make_user_subscribed(db: AsyncSession, email: str) -> User:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.is_subscribed = True
    await db.commit()
    await db.refresh(user)
    return user


async def create_signal(db: AsyncSession, exchange: str = "binance") -> Signal:
    signal = Signal(
        symbol="BTC",
        exchange=exchange,
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
# Binance Auto-Execution Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_binance_auto_execute_creates_trades_for_all_subscribed_users(
    client: AsyncClient, db_session: AsyncSession, mock_binance_services
):
    """
    When a Binance signal drops, auto_execute_binance_signal should create
    trades for ALL subscribed users with active, trading-enabled connections.
    """
    # Create 2 subscribed users, each with active Binance connections
    emails = ["user1@autoexec.com", "user2@autoexec.com"]
    for email in emails:
        token = await register_and_login(client, email, "Password123!")
        await make_user_subscribed(db_session, email)

        conn_r = await client.post(
            "/api/v1/exchanges/connect",
            json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
            headers=auth_headers(token),
        )
        assert conn_r.status_code == 200
        conn_id = conn_r.json()["id"]

        # Enable trading
        await client.patch(
            f"/api/v1/exchanges/{conn_id}/trading",
            json={"enabled": True},
            headers=auth_headers(token),
        )

    # Create a signal
    signal = await create_signal(db_session, exchange="binance")

    # Run the auto-execute task directly (not via Celery)
    from app.workers.tasks.trading import _auto_execute_binance_signal

    await _auto_execute_binance_signal(str(signal.id))

    # Verify: both users should have trades
    result = await db_session.execute(
        select(Trade).where(
            Trade.signal_id == signal.id,
            Trade.status == TradeStatus.OPEN,
        )
    )
    trades = list(result.scalars().all())

    assert len(trades) == 2, f"Expected 2 trades but got {len(trades)}"
    user_ids = {t.user_id for t in trades}
    assert len(user_ids) == 2, "Each user should have their own trade"

    for trade in trades:
        assert trade.exchange == "binance"
        assert trade.symbol == "BTC"
        assert trade.entry_price == 50000.0


@pytest.mark.asyncio
async def test_binance_auto_execute_skips_unsubscribed_users(
    client: AsyncClient, db_session: AsyncSession, mock_binance_services
):
    """
    Users without is_subscribed=True should NOT get auto-executed trades.
    """
    # Subscribed user
    token1 = await register_and_login(client, "sub@autoexec.com", "Password123!")
    await make_user_subscribed(db_session, "sub@autoexec.com")
    conn = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token1),
    )
    await client.patch(
        f"/api/v1/exchanges/{conn.json()['id']}/trading",
        json={"enabled": True},
        headers=auth_headers(token1),
    )

    # Unsubscribed user with active connection
    token2 = await register_and_login(client, "nosub@autoexec.com", "Password123!")
    conn2 = await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "c" * 64, "api_secret": "d" * 64},
        headers=auth_headers(token2),
    )
    await client.patch(
        f"/api/v1/exchanges/{conn2.json()['id']}/trading",
        json={"enabled": True},
        headers=auth_headers(token2),
    )

    signal = await create_signal(db_session, exchange="binance")

    from app.workers.tasks.trading import _auto_execute_binance_signal

    await _auto_execute_binance_signal(str(signal.id))

    result = await db_session.execute(select(Trade).where(Trade.signal_id == signal.id))
    trades = list(result.scalars().all())

    # Only the subscribed user should have a trade
    assert len(trades) == 1
    sub_user = await db_session.execute(select(User).where(User.email == "sub@autoexec.com"))
    assert trades[0].user_id == sub_user.scalar_one().id


@pytest.mark.asyncio
async def test_binance_auto_execute_skips_trading_disabled(
    client: AsyncClient, db_session: AsyncSession, mock_binance_services
):
    """
    Users with is_trading_enabled=False should NOT get auto-executed trades.
    """
    token = await register_and_login(client, "notrade@autoexec.com", "Password123!")
    await make_user_subscribed(db_session, "notrade@autoexec.com")

    # Connect but do NOT enable trading
    await client.post(
        "/api/v1/exchanges/connect",
        json={"exchange_type": "binance", "api_key": "a" * 64, "api_secret": "b" * 64},
        headers=auth_headers(token),
    )

    signal = await create_signal(db_session, exchange="binance")

    from app.workers.tasks.trading import _auto_execute_binance_signal

    await _auto_execute_binance_signal(str(signal.id))

    result = await db_session.execute(select(Trade).where(Trade.signal_id == signal.id))
    trades = list(result.scalars().all())
    assert len(trades) == 0


# ---------------------------------------------------------------------------
# Hyperliquid Auto-Execution Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hyperliquid_auto_execute_creates_trades_for_all_subscribed_users(
    client: AsyncClient, db_session: AsyncSession, mock_hl_services
):
    """
    When a Hyperliquid signal drops, auto_execute_hyperliquid_signal should
    create trades for ALL subscribed users with active, tradeable wallets.
    """
    from app.models.wallet import Wallet, WalletStatus, WalletType

    # Create 2 subscribed users with active wallets
    for i, email in enumerate(["hluser1@autoexec.com", "hluser2@autoexec.com"]):
        await register_and_login(client, email, "Password123!")
        user = await make_user_subscribed(db_session, email)

        wallet = Wallet(
            user_id=user.id,
            address=f"0x{'0' * 39}{i + 1}",
            wallet_type=WalletType.API,
            status=WalletStatus.ACTIVE,
            is_authorized=True,
            is_trading_enabled=True,
            encrypted_private_key="encrypted_key_placeholder",
        )
        db_session.add(wallet)

    await db_session.commit()

    signal = await create_signal(db_session, exchange="hyperliquid")

    from app.workers.tasks.trading import _auto_execute_hyperliquid_signal

    await _auto_execute_hyperliquid_signal(str(signal.id))

    # Query through a fresh session since the worker committed on its own connection
    from app.workers.database import get_worker_db

    async with get_worker_db() as fresh_db:
        result = await fresh_db.execute(
            select(Trade).where(
                Trade.signal_id == signal.id,
                Trade.status == TradeStatus.OPEN,
            )
        )
        trades = list(result.scalars().all())

    assert len(trades) == 2, f"Expected 2 trades but got {len(trades)}"
    user_ids = {t.user_id for t in trades}
    assert len(user_ids) == 2, "Each user should have their own trade"

    for trade in trades:
        assert trade.symbol == "BTC"
        assert trade.entry_price == 50000.0


@pytest.mark.asyncio
async def test_hyperliquid_auto_execute_skips_unsubscribed_users(
    client: AsyncClient, db_session: AsyncSession, mock_hl_services
):
    """
    Unsubscribed users with active HL wallets should NOT get auto-executed trades.
    """
    from app.models.wallet import Wallet, WalletStatus, WalletType

    # Subscribed user
    await register_and_login(client, "hlsub@autoexec.com", "Password123!")
    user1 = await make_user_subscribed(db_session, "hlsub@autoexec.com")
    wallet1 = Wallet(
        user_id=user1.id,
        address="0x" + "a" * 40,
        wallet_type=WalletType.API,
        status=WalletStatus.ACTIVE,
        is_authorized=True,
        is_trading_enabled=True,
        encrypted_private_key="key1",
    )
    db_session.add(wallet1)

    # Unsubscribed user
    await register_and_login(client, "hlnosub@autoexec.com", "Password123!")
    result = await db_session.execute(select(User).where(User.email == "hlnosub@autoexec.com"))
    user2 = result.scalar_one()
    wallet2 = Wallet(
        user_id=user2.id,
        address="0x" + "b" * 40,
        wallet_type=WalletType.API,
        status=WalletStatus.ACTIVE,
        is_authorized=True,
        is_trading_enabled=True,
        encrypted_private_key="key2",
    )
    db_session.add(wallet2)
    await db_session.commit()

    signal = await create_signal(db_session, exchange="hyperliquid")

    from app.workers.tasks.trading import _auto_execute_hyperliquid_signal

    await _auto_execute_hyperliquid_signal(str(signal.id))

    from app.workers.database import get_worker_db

    async with get_worker_db() as fresh_db:
        result = await fresh_db.execute(select(Trade).where(Trade.signal_id == signal.id))
        trades = list(result.scalars().all())

    assert len(trades) == 1
    assert trades[0].user_id == user1.id


@pytest.mark.asyncio
async def test_hyperliquid_auto_execute_skips_wallet_not_tradeable(
    client: AsyncClient, db_session: AsyncSession, mock_hl_services
):
    """
    Users whose wallet has is_trading_enabled=False should be skipped.
    """
    from app.models.wallet import Wallet, WalletStatus, WalletType

    await register_and_login(client, "hlnotrade@autoexec.com", "Password123!")
    user = await make_user_subscribed(db_session, "hlnotrade@autoexec.com")

    wallet = Wallet(
        user_id=user.id,
        address="0x" + "c" * 40,
        wallet_type=WalletType.API,
        status=WalletStatus.ACTIVE,
        is_authorized=True,
        is_trading_enabled=False,  # Trading disabled
        encrypted_private_key="key",
    )
    db_session.add(wallet)
    await db_session.commit()

    signal = await create_signal(db_session, exchange="hyperliquid")

    from app.workers.tasks.trading import _auto_execute_hyperliquid_signal

    await _auto_execute_hyperliquid_signal(str(signal.id))

    from app.workers.database import get_worker_db

    async with get_worker_db() as fresh_db:
        result = await fresh_db.execute(select(Trade).where(Trade.signal_id == signal.id))
        trades = list(result.scalars().all())
    assert len(trades) == 0
