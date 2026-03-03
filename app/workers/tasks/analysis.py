import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

TOP_GAINERS_LIMIT = 3
MIN_VOLUME_USD = 5_000_000


@celery_app.task(bind=True, max_retries=3)
def analyze_all_markets(self):
    try:
        asyncio.run(_analyze_all_markets())
    except Exception as e:
        logger.error(f"Market analysis failed: {e}")
        raise self.retry(exc=e, countdown=60) from e


async def _get_recent_signal_symbols(db) -> set[str]:
    """Get symbols that already have an active/pending signal to avoid duplicates."""
    from sqlalchemy import select

    from app.models import Signal, SignalStatus

    result = await db.execute(
        select(Signal.symbol).where(Signal.status.in_([SignalStatus.ACTIVE, SignalStatus.PENDING]))
    )
    return {row[0] for row in result.all()}


async def _analyze_all_markets():
    from sqlalchemy import select

    from app.models import TelegramConnection
    from app.services.hyperliquid import get_info_service
    from app.services.telegram_service import TelegramService
    from app.services.trading import SignalService
    from app.workers.database import get_worker_db

    logger.info("Starting market analysis (top gainers)...")

    info_service = get_info_service()

    try:
        top_gainers = await info_service.get_top_gainers(
            min_volume=MIN_VOLUME_USD,
            limit=TOP_GAINERS_LIMIT + 5,  # fetch extra to account for skips
        )
    except Exception as e:
        logger.error(f"Failed to fetch top gainers: {e}")
        return

    if not top_gainers:
        logger.warning("No top gainers found above volume threshold")
        return

    gainer_summary = ", ".join(
        f"{c['symbol']} (+{c['price_change_percent_24h']:.1f}%)" for c in top_gainers
    )
    logger.info(f"Top gainers: {gainer_summary}")

    signals_generated = []

    async with get_worker_db() as db:
        signal_service = SignalService(db)

        # Skip symbols that already have active signals
        existing_symbols = await _get_recent_signal_symbols(db)
        if existing_symbols:
            logger.info(f"Skipping symbols with active signals: {existing_symbols}")

        analyzed_count = 0
        for coin_data in top_gainers:
            if analyzed_count >= TOP_GAINERS_LIMIT:
                break

            symbol = coin_data.get("symbol")
            if not symbol:
                continue

            if symbol in existing_symbols:
                logger.info(f"Skipping {symbol} — active signal already exists")
                continue

            change_pct = coin_data.get("price_change_percent_24h", 0)
            volume = coin_data.get("volume_24h", 0)
            logger.info(
                f"Analyzing {symbol} (#{analyzed_count + 1}) — "
                f"+{change_pct:.1f}%, vol ${volume:,.0f}"
            )

            try:
                signal = await signal_service.generate_signal(symbol)
                analyzed_count += 1

                if signal:
                    signals_generated.append(signal)
                    logger.info(
                        f"Signal generated for {symbol}: "
                        f"{signal.direction.value} @ {signal.entry_price} "
                        f"(confidence={float(signal.confidence_score):.2f})"
                    )
                else:
                    logger.info(f"No consensus reached for {symbol}")

            except Exception as e:
                logger.error(f"Error analyzing {symbol}: {e}", exc_info=True)
                analyzed_count += 1
                continue

        await db.commit()

        # Send Telegram notifications for new signals
        if signals_generated:
            try:
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
                            logger.error(f"Failed to send Telegram notification: {e}")
            except Exception as e:
                logger.error(f"Telegram notification batch failed: {e}")

    logger.info(
        f"Market analysis complete. Analyzed {analyzed_count} top gainers, "
        f"generated {len(signals_generated)} signals."
    )


@celery_app.task(bind=True)
def analyze_single_market(self, symbol: str):
    try:
        asyncio.run(_analyze_single_market(symbol))
    except Exception as e:
        logger.error(f"Single market analysis failed for {symbol}: {e}")
        raise


async def _analyze_single_market(symbol: str):
    from app.services.trading import SignalService
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
        signal_service = SignalService(db)
        signal = await signal_service.generate_signal(symbol)
        await db.commit()

        if signal:
            logger.info(f"Signal generated for {symbol}: {signal.direction.value}")
        else:
            logger.info(f"No signal generated for {symbol}")


# ---------------------------------------------------------------------------
# Binance market analysis
# ---------------------------------------------------------------------------

BINANCE_TOP_MOVERS_LIMIT = 5
BINANCE_MIN_VOLUME_USD = 10_000_000


@celery_app.task(bind=True, max_retries=3)
def analyze_binance_markets(self):
    try:
        asyncio.run(_analyze_binance_markets())
    except Exception as e:
        logger.error(f"Binance market analysis failed: {e}")
        raise self.retry(exc=e, countdown=60) from e


async def _get_recent_binance_signal_symbols(db) -> set[str]:
    """Get symbols that already have an active Binance signal."""
    from sqlalchemy import select

    from app.models import Signal, SignalStatus

    result = await db.execute(
        select(Signal.symbol).where(
            Signal.status.in_([SignalStatus.ACTIVE, SignalStatus.PENDING]),
            Signal.exchange == "binance",
        )
    )
    return {row[0] for row in result.all()}


async def _analyze_binance_markets():
    from sqlalchemy import select

    from app.config import settings as app_settings
    from app.models import TelegramConnection
    from app.services.binance import get_binance_info_service
    from app.services.telegram_service import TelegramService
    from app.services.trading import SignalService
    from app.workers.database import get_worker_db
    from app.workers.tasks.trading import auto_execute_binance_signal

    logger.info("Starting Binance market analysis (top gainers + losers)...")

    info_service = get_binance_info_service()
    limit = app_settings.binance_top_movers_limit
    min_vol = app_settings.binance_min_volume_usd

    # Fetch top gainers and losers
    try:
        top_gainers = await info_service.get_top_gainers(
            min_volume=min_vol,
            limit=limit + 3,
        )
    except Exception as e:
        logger.error(f"Failed to fetch Binance top gainers: {e}")
        top_gainers = []

    try:
        top_losers = await info_service.get_top_losers(
            min_volume=min_vol,
            limit=limit + 3,
        )
    except Exception as e:
        logger.error(f"Failed to fetch Binance top losers: {e}")
        top_losers = []

    if not top_gainers and not top_losers:
        logger.warning("No Binance movers found above volume threshold")
        return

    if top_gainers:
        summary = ", ".join(
            f"{c['symbol']} (+{c['price_change_percent_24h']:.1f}%)" for c in top_gainers[:limit]
        )
        logger.info(f"Binance top gainers: {summary}")

    if top_losers:
        summary = ", ".join(
            f"{c['symbol']} ({c['price_change_percent_24h']:.1f}%)" for c in top_losers[:limit]
        )
        logger.info(f"Binance top losers: {summary}")

    # Combine and deduplicate
    all_movers = []
    seen_symbols = set()
    for coin in top_gainers + top_losers:
        symbol = coin.get("symbol")
        if symbol and symbol not in seen_symbols:
            seen_symbols.add(symbol)
            all_movers.append(coin)

    signals_generated = []

    async with get_worker_db() as db:
        signal_service = SignalService(db)

        existing_symbols = await _get_recent_binance_signal_symbols(db)
        if existing_symbols:
            logger.info(f"Skipping Binance symbols with active signals: {existing_symbols}")

        analyzed_count = 0
        max_analyze = limit * 2  # analyze up to gainers + losers limit

        for coin_data in all_movers:
            if analyzed_count >= max_analyze:
                break

            symbol = coin_data.get("symbol")
            if not symbol or symbol in existing_symbols:
                if symbol:
                    logger.info(f"Skipping {symbol} — active Binance signal exists")
                continue

            change_pct = coin_data.get("price_change_percent_24h", 0)
            volume = coin_data.get("volume_24h", 0)
            logger.info(
                f"Analyzing Binance {symbol} (#{analyzed_count + 1}) — "
                f"{change_pct:+.1f}%, vol ${volume:,.0f}"
            )

            try:
                signal = await signal_service.generate_signal(symbol, exchange="binance")
                analyzed_count += 1

                if signal:
                    signals_generated.append(signal)
                    logger.info(
                        f"Binance signal generated for {symbol}: "
                        f"{signal.direction.value} @ {signal.entry_price} "
                        f"(confidence={float(signal.confidence_score):.2f})"
                    )

                    # Trigger auto-execution for subscribed users
                    auto_execute_binance_signal.delay(str(signal.id))
                else:
                    logger.info(f"No consensus reached for Binance {symbol}")

            except Exception as e:
                logger.error(f"Error analyzing Binance {symbol}: {e}", exc_info=True)
                analyzed_count += 1
                continue

        await db.commit()

        # Telegram notifications
        if signals_generated:
            try:
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
                            logger.error(f"Failed to send Telegram notification: {e}")
            except Exception as e:
                logger.error(f"Telegram notification batch failed: {e}")

    logger.info(
        f"Binance market analysis complete. Analyzed {analyzed_count} movers, "
        f"generated {len(signals_generated)} signals."
    )
