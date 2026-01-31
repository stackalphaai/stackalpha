from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser
from app.models import Signal, SignalOutcome, Trade, TradeStatus

router = APIRouter(prefix="/analytics", tags=["Analytics"])

# Type alias for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


class TradeAnalytics(BaseModel):
    period: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    average_pnl: float
    best_trade: float
    worst_trade: float
    average_duration_seconds: int | None = None


class SignalAnalytics(BaseModel):
    period: str
    total_signals: int
    executed_signals: int
    tp_hit: int
    sl_hit: int
    success_rate: float
    average_confidence: float


class PerformanceBySymbol(BaseModel):
    symbol: str
    total_trades: int
    win_rate: float
    total_pnl: float


class DailyPnL(BaseModel):
    date: str
    pnl: float
    trade_count: int


@router.get("/trades", response_model=TradeAnalytics)
async def get_trade_analytics(
    current_user: CurrentUser,
    db: DB,
    period: str = Query("30d", pattern="^(7d|30d|90d|all)$"),
):
    now = datetime.now(UTC)

    if period == "7d":
        start_date = now - timedelta(days=7)
    elif period == "30d":
        start_date = now - timedelta(days=30)
    elif period == "90d":
        start_date = now - timedelta(days=90)
    else:
        start_date = None

    query = select(Trade).where(
        Trade.user_id == current_user.id,
        Trade.status == TradeStatus.CLOSED,
    )

    if start_date:
        query = query.where(Trade.closed_at >= start_date)

    result = await db.execute(query)
    trades = list(result.scalars().all())

    if not trades:
        return TradeAnalytics(
            period=period,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            total_pnl=0.0,
            average_pnl=0.0,
            best_trade=0.0,
            worst_trade=0.0,
        )

    total = len(trades)
    winning = sum(1 for t in trades if t.realized_pnl and t.realized_pnl > 0)
    losing = sum(1 for t in trades if t.realized_pnl and t.realized_pnl < 0)

    pnls = [float(t.realized_pnl) for t in trades if t.realized_pnl is not None]
    total_pnl = sum(pnls) if pnls else 0.0
    avg_pnl = total_pnl / len(pnls) if pnls else 0.0
    best = max(pnls) if pnls else 0.0
    worst = min(pnls) if pnls else 0.0

    durations = [t.duration_seconds for t in trades if t.duration_seconds]
    avg_duration = sum(durations) // len(durations) if durations else None

    return TradeAnalytics(
        period=period,
        total_trades=total,
        winning_trades=winning,
        losing_trades=losing,
        win_rate=round(winning / total * 100, 2) if total > 0 else 0.0,
        total_pnl=round(total_pnl, 2),
        average_pnl=round(avg_pnl, 2),
        best_trade=round(best, 2),
        worst_trade=round(worst, 2),
        average_duration_seconds=avg_duration,
    )


@router.get("/signals", response_model=SignalAnalytics)
async def get_signal_analytics(
    current_user: CurrentUser,
    db: DB,
    period: str = Query("30d", pattern="^(7d|30d|90d|all)$"),
):
    now = datetime.now(UTC)

    if period == "7d":
        start_date = now - timedelta(days=7)
    elif period == "30d":
        start_date = now - timedelta(days=30)
    elif period == "90d":
        start_date = now - timedelta(days=90)
    else:
        start_date = None

    query = select(Signal)

    if start_date:
        query = query.where(Signal.created_at >= start_date)

    result = await db.execute(query)
    signals = list(result.scalars().all())

    if not signals:
        return SignalAnalytics(
            period=period,
            total_signals=0,
            executed_signals=0,
            tp_hit=0,
            sl_hit=0,
            success_rate=0.0,
            average_confidence=0.0,
        )

    total = len(signals)
    executed = sum(1 for s in signals if s.outcome != SignalOutcome.PENDING)
    tp_hit = sum(1 for s in signals if s.outcome == SignalOutcome.TP_HIT)
    sl_hit = sum(1 for s in signals if s.outcome == SignalOutcome.SL_HIT)

    confidences = [float(s.confidence_score) for s in signals]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    success_rate = (tp_hit / executed * 100) if executed > 0 else 0.0

    return SignalAnalytics(
        period=period,
        total_signals=total,
        executed_signals=executed,
        tp_hit=tp_hit,
        sl_hit=sl_hit,
        success_rate=round(success_rate, 2),
        average_confidence=round(avg_confidence, 4),
    )


@router.get("/performance-by-symbol", response_model=list[PerformanceBySymbol])
async def get_performance_by_symbol(
    current_user: CurrentUser,
    db: DB,
):
    result = await db.execute(
        select(Trade).where(
            Trade.user_id == current_user.id,
            Trade.status == TradeStatus.CLOSED,
        )
    )
    trades = list(result.scalars().all())

    symbol_stats: dict[str, dict] = {}
    for trade in trades:
        if trade.symbol not in symbol_stats:
            symbol_stats[trade.symbol] = {
                "total": 0,
                "wins": 0,
                "pnl": 0.0,
            }

        symbol_stats[trade.symbol]["total"] += 1
        if trade.realized_pnl and trade.realized_pnl > 0:
            symbol_stats[trade.symbol]["wins"] += 1
        symbol_stats[trade.symbol]["pnl"] += float(trade.realized_pnl or 0)

    return [
        PerformanceBySymbol(
            symbol=symbol,
            total_trades=stats["total"],
            win_rate=round(stats["wins"] / stats["total"] * 100, 2) if stats["total"] > 0 else 0,
            total_pnl=round(stats["pnl"], 2),
        )
        for symbol, stats in sorted(
            symbol_stats.items(),
            key=lambda x: x[1]["pnl"],
            reverse=True,
        )
    ]


@router.get("/daily-pnl", response_model=list[DailyPnL])
async def get_daily_pnl(
    current_user: CurrentUser,
    db: DB,
    days: int = Query(30, ge=7, le=90),
):
    now = datetime.now(UTC)
    start_date = now - timedelta(days=days)

    result = await db.execute(
        select(Trade)
        .where(
            Trade.user_id == current_user.id,
            Trade.status == TradeStatus.CLOSED,
            Trade.closed_at >= start_date,
        )
        .order_by(Trade.closed_at)
    )
    trades = list(result.scalars().all())

    daily_data: dict[str, dict] = {}
    for trade in trades:
        if trade.closed_at:
            date_str = trade.closed_at.strftime("%Y-%m-%d")
            if date_str not in daily_data:
                daily_data[date_str] = {"pnl": 0.0, "count": 0}
            daily_data[date_str]["pnl"] += float(trade.realized_pnl or 0)
            daily_data[date_str]["count"] += 1

    current = start_date
    result_list = []
    while current <= now:
        date_str = current.strftime("%Y-%m-%d")
        data = daily_data.get(date_str, {"pnl": 0.0, "count": 0})
        result_list.append(
            DailyPnL(
                date=date_str,
                pnl=round(data["pnl"], 2),
                trade_count=data["count"],
            )
        )
        current += timedelta(days=1)

    return result_list
