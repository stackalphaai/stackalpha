import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def analyze_all_markets(self):
    try:
        asyncio.run(_analyze_all_markets())
    except Exception as e:
        logger.error(f"Market analysis failed: {e}")
        raise self.retry(exc=e, countdown=60) from e


async def _analyze_all_markets():
    from sqlalchemy import select

    from app.database import get_db_context
    from app.models import TelegramConnection
    from app.services.hyperliquid import get_info_service
    from app.services.telegram_service import TelegramService
    from app.services.trading import SignalService

    logger.info("Starting market analysis...")

    info_service = get_info_service()
    high_volume_coins = await info_service.get_high_volume_coins(
        min_volume=5_000_000,
        limit=3,  # Reduced from 10 to save API credits
    )

    signals_generated = []

    async with get_db_context() as db:
        signal_service = SignalService(db)

        for coin_data in high_volume_coins:
            symbol = coin_data.get("symbol")
            if not symbol:
                continue

            logger.info(f"Analyzing {symbol}...")

            try:
                signal = await signal_service.generate_signal(symbol)

                if signal:
                    signals_generated.append(signal)
                    logger.info(
                        f"Signal generated for {symbol}: "
                        f"{signal.direction.value} @ {signal.entry_price}"
                    )

            except Exception as e:
                logger.error(f"Error analyzing {symbol}: {e}")
                continue

        await db.commit()

        if signals_generated:
            telegram_service = TelegramService(db)

            result = await db.execute(
                select(TelegramConnection).where(
                    TelegramConnection.is_verified,
                    TelegramConnection.signal_notifications,
                )
            )
            connections = list(result.scalars().all())

            for signal in signals_generated:
                for conn in connections:
                    try:
                        await telegram_service.send_signal_notification(conn, signal)
                    except Exception as e:
                        logger.error(f"Failed to send signal notification: {e}")

    logger.info(f"Market analysis complete. Generated {len(signals_generated)} signals.")


@celery_app.task(bind=True)
def analyze_single_market(self, symbol: str):
    try:
        asyncio.run(_analyze_single_market(symbol))
    except Exception as e:
        logger.error(f"Single market analysis failed for {symbol}: {e}")
        raise


async def _analyze_single_market(symbol: str):
    from app.database import get_db_context
    from app.services.trading import SignalService

    async with get_db_context() as db:
        signal_service = SignalService(db)
        signal = await signal_service.generate_signal(symbol)
        await db.commit()

        if signal:
            logger.info(f"Signal generated for {symbol}: {signal.direction.value}")
        else:
            logger.info(f"No signal generated for {symbol}")
