import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Signal, SignalDirection, SignalOutcome, SignalStatus
from app.schemas.common import PaginationParams
from app.services.llm import get_consensus_engine

logger = logging.getLogger(__name__)


class SignalService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.consensus_engine = get_consensus_engine()

    async def generate_signal(self, symbol: str) -> Signal | None:
        signal_data = await self.consensus_engine.generate_signal(symbol)

        if not signal_data:
            return None

        signal = Signal(
            symbol=signal_data["symbol"],
            direction=signal_data["direction"],
            status=SignalStatus.ACTIVE,
            outcome=SignalOutcome.PENDING,
            entry_price=signal_data["entry_price"],
            take_profit_price=signal_data["take_profit_price"],
            stop_loss_price=signal_data["stop_loss_price"],
            suggested_leverage=signal_data["suggested_leverage"],
            suggested_position_size_percent=signal_data["suggested_position_size_percent"],
            confidence_score=signal_data["confidence_score"],
            consensus_votes=signal_data["consensus_votes"],
            total_votes=signal_data["total_votes"],
            market_price_at_creation=signal_data["market_price_at_creation"],
            technical_indicators=signal_data.get("technical_indicators"),
            llm_responses=signal_data.get("llm_responses"),
            analysis_data=signal_data.get("analysis_data"),
            expires_at=datetime.now(UTC) + timedelta(hours=settings.analysis_interval_hours),
        )

        self.db.add(signal)
        await self.db.flush()
        await self.db.refresh(signal)

        logger.info(
            f"Generated signal: {signal.symbol} {signal.direction.value} "
            f"confidence={signal.confidence_score:.2f}"
        )

        return signal

    async def get_signal_by_id(self, signal_id: str) -> Signal | None:
        result = await self.db.execute(select(Signal).where(Signal.id == signal_id))
        return result.scalar_one_or_none()

    async def get_active_signals(self) -> list[Signal]:
        result = await self.db.execute(
            select(Signal)
            .where(Signal.status == SignalStatus.ACTIVE)
            .order_by(Signal.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_signals(
        self,
        pagination: PaginationParams,
        symbol: str | None = None,
        direction: SignalDirection | None = None,
        status: SignalStatus | None = None,
    ) -> tuple[list[Signal], int]:
        query = select(Signal)

        if symbol:
            query = query.where(Signal.symbol == symbol)
        if direction:
            query = query.where(Signal.direction == direction)
        if status:
            query = query.where(Signal.status == status)

        count_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar() or 0

        query = query.order_by(Signal.created_at.desc())
        query = query.offset(pagination.offset).limit(pagination.limit)

        result = await self.db.execute(query)
        signals = list(result.scalars().all())

        return signals, total

    async def expire_old_signals(self) -> int:
        now = datetime.now(UTC)

        result = await self.db.execute(
            select(Signal).where(
                Signal.status == SignalStatus.ACTIVE,
                Signal.expires_at < now,
            )
        )
        expired_signals = list(result.scalars().all())

        for signal in expired_signals:
            signal.status = SignalStatus.EXPIRED
            signal.outcome = SignalOutcome.EXPIRED

        return len(expired_signals)

    async def update_signal_outcome(
        self,
        signal: Signal,
        outcome: SignalOutcome,
        exit_price: float,
    ) -> Signal:
        signal.outcome = outcome
        signal.actual_exit_price = exit_price
        signal.status = SignalStatus.EXECUTED
        signal.closed_at = datetime.now(UTC)

        if signal.direction == SignalDirection.LONG:
            pnl_percent = (exit_price - signal.entry_price) / signal.entry_price * 100
        else:
            pnl_percent = (signal.entry_price - exit_price) / signal.entry_price * 100

        signal.actual_pnl_percent = pnl_percent

        return signal

    async def get_signal_stats(self) -> dict:
        result = await self.db.execute(select(Signal).where(Signal.status == SignalStatus.EXECUTED))
        executed_signals = list(result.scalars().all())

        if not executed_signals:
            return {
                "total_signals": 0,
                "successful_signals": 0,
                "failed_signals": 0,
                "success_rate": 0.0,
                "average_pnl": 0.0,
                "total_pnl": 0.0,
            }

        total = len(executed_signals)
        successful = sum(1 for s in executed_signals if s.outcome == SignalOutcome.TP_HIT)
        failed = sum(1 for s in executed_signals if s.outcome == SignalOutcome.SL_HIT)

        pnls = [s.actual_pnl_percent for s in executed_signals if s.actual_pnl_percent]
        total_pnl = sum(pnls) if pnls else 0.0
        avg_pnl = total_pnl / len(pnls) if pnls else 0.0

        return {
            "total_signals": total,
            "successful_signals": successful,
            "failed_signals": failed,
            "success_rate": round(successful / total * 100, 2) if total > 0 else 0.0,
            "average_pnl": round(avg_pnl, 2),
            "total_pnl": round(total_pnl, 2),
        }
