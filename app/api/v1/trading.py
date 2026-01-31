from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, Pagination, SubscribedUser
from app.models import SignalDirection, SignalStatus, Trade, TradeStatus
from app.schemas import (
    CloseTradeRequest,
    CreateTradeRequest,
    ExecuteSignalRequest,
    MarketDataResponse,
    PaginatedResponse,
    SignalDetailResponse,
    SignalResponse,
    TradeDetailResponse,
    TradeResponse,
)
from app.services import WalletService
from app.services.hyperliquid import get_info_service
from app.services.trading import SignalService, TradeExecutor

router = APIRouter(prefix="/trading", tags=["Trading"])

# Type alias for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/signals", response_model=PaginatedResponse[SignalResponse])
async def get_signals(
    pagination: Pagination,
    current_user: CurrentUser,
    db: DB,
    symbol: str | None = None,
    direction: SignalDirection | None = None,
    status: SignalStatus | None = None,
):
    signal_service = SignalService(db)
    signals, total = await signal_service.get_signals(
        pagination=pagination,
        symbol=symbol,
        direction=direction,
        status=status,
    )

    return PaginatedResponse.create(
        items=[SignalResponse.model_validate(s) for s in signals],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/signals/active", response_model=list[SignalResponse])
async def get_active_signals(
    current_user: SubscribedUser,
    db: DB,
):
    signal_service = SignalService(db)
    signals = await signal_service.get_active_signals()
    return signals


@router.get("/signals/{signal_id}", response_model=SignalDetailResponse)
async def get_signal(
    signal_id: str,
    current_user: CurrentUser,
    db: DB,
):
    signal_service = SignalService(db)
    signal = await signal_service.get_signal_by_id(signal_id)

    if not signal:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Signal")

    return signal


@router.post("/signals/{signal_id}/execute", response_model=TradeResponse)
async def execute_signal(
    signal_id: str,
    data: ExecuteSignalRequest,
    current_user: SubscribedUser,
    db: DB,
):
    signal_service = SignalService(db)
    signal = await signal_service.get_signal_by_id(signal_id)

    if not signal:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Signal")

    if not signal.is_valid:
        from app.core.exceptions import BadRequestError

        raise BadRequestError("Signal is no longer valid")

    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(data.wallet_id, current_user.id)

    if not wallet:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Wallet")

    executor = TradeExecutor(db)
    trade = await executor.execute_signal(
        user=current_user,
        wallet=wallet,
        signal=signal,
        position_size_percent=data.position_size_percent,
        leverage=data.leverage,
    )
    await db.commit()
    return trade


@router.get("/trades", response_model=PaginatedResponse[TradeResponse])
async def get_trades(
    pagination: Pagination,
    current_user: CurrentUser,
    db: DB,
    symbol: str | None = None,
    status: TradeStatus | None = None,
):
    query = select(Trade).where(Trade.user_id == current_user.id)

    if symbol:
        query = query.where(Trade.symbol == symbol)
    if status:
        query = query.where(Trade.status == status)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    query = query.order_by(Trade.created_at.desc())
    query = query.offset(pagination.offset).limit(pagination.limit)

    result = await db.execute(query)
    trades = list(result.scalars().all())

    return PaginatedResponse.create(
        items=[TradeResponse.model_validate(t) for t in trades],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/trades/open", response_model=list[TradeResponse])
async def get_open_trades(
    current_user: CurrentUser,
    db: DB,
):
    result = await db.execute(
        select(Trade)
        .where(
            Trade.user_id == current_user.id,
            Trade.status.in_([TradeStatus.OPEN, TradeStatus.OPENING]),
        )
        .order_by(Trade.created_at.desc())
    )
    trades = list(result.scalars().all())
    return trades


@router.get("/trades/{trade_id}", response_model=TradeDetailResponse)
async def get_trade(
    trade_id: str,
    current_user: CurrentUser,
    db: DB,
):
    result = await db.execute(
        select(Trade).where(
            Trade.id == trade_id,
            Trade.user_id == current_user.id,
        )
    )
    trade = result.scalar_one_or_none()

    if not trade:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Trade")

    return trade


@router.post("/trades", response_model=TradeResponse)
async def create_trade(
    data: CreateTradeRequest,
    current_user: SubscribedUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(data.wallet_id, current_user.id)

    if not wallet:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Wallet")

    executor = TradeExecutor(db)
    trade = await executor.open_trade(
        user=current_user,
        wallet=wallet,
        symbol=data.symbol,
        direction=data.direction,
        position_size_usd=data.position_size_usd,
        leverage=data.leverage,
        take_profit_price=data.take_profit_price,
        stop_loss_price=data.stop_loss_price,
    )
    await db.commit()
    return trade


@router.post("/trades/{trade_id}/close", response_model=TradeResponse)
async def close_trade(
    trade_id: str,
    data: CloseTradeRequest,
    current_user: SubscribedUser,
    db: DB,
):
    result = await db.execute(
        select(Trade).where(
            Trade.id == trade_id,
            Trade.user_id == current_user.id,
        )
    )
    trade = result.scalar_one_or_none()

    if not trade:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Trade")

    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(trade.wallet_id)

    executor = TradeExecutor(db)
    trade = await executor.close_trade(trade, wallet, data.reason)
    await db.commit()
    return trade


@router.get("/markets", response_model=list[MarketDataResponse])
async def get_markets(
    current_user: CurrentUser,
):
    info_service = get_info_service()
    markets = await info_service.get_all_market_data()

    return [
        MarketDataResponse(
            symbol=m.get("symbol", ""),
            mark_price=m.get("mark_price", 0),
            index_price=m.get("index_price", 0),
            funding_rate=m.get("funding_rate", 0),
            open_interest=m.get("open_interest", 0),
            volume_24h=m.get("volume_24h", 0),
            high_24h=0,
            low_24h=0,
            price_change_24h=m.get("price_change_24h", 0),
            price_change_percent_24h=m.get("price_change_percent_24h", 0),
        )
        for m in markets
    ]


@router.get("/markets/{symbol}", response_model=MarketDataResponse)
async def get_market(
    symbol: str,
    current_user: CurrentUser,
):
    info_service = get_info_service()
    market = await info_service.get_market_data(symbol)

    if not market:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Market")

    return MarketDataResponse(
        symbol=market.get("symbol", symbol),
        mark_price=market.get("mark_price", 0),
        index_price=market.get("index_price", 0),
        funding_rate=market.get("funding_rate", 0),
        open_interest=market.get("open_interest", 0),
        volume_24h=market.get("volume_24h", 0),
        high_24h=0,
        low_24h=0,
        price_change_24h=market.get("price_change_24h", 0),
        price_change_percent_24h=market.get("price_change_percent_24h", 0),
    )


@router.get("/signal-stats")
async def get_signal_stats(
    current_user: CurrentUser,
    db: DB,
):
    signal_service = SignalService(db)
    stats = await signal_service.get_signal_stats()
    return stats
