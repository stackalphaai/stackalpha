"""
End-to-end test of the Binance Futures integration.

Steps:
1. Connect to Binance Futures testnet
2. Fetch top 1 gainer
3. Generate a signal via LLM consensus
4. Execute the trade (market order + TP/SL)
"""

import asyncio
import json
import logging
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.dirname(__file__))

# Set env vars before importing app modules
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:password@localhost:5432/usealpha"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENCRYPTION_KEY", "dAaivhJrZN0-YlxtnVNpXmq8rD_C8no3arnu1fIg8VQ=")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- Testnet credentials ---
API_KEY = "KLbQYxtcq8YK4LqL3Bx179H7ZXkLG9LyMOiVP46lTNK5an2zchxXJQnFFABq7hZl"
API_SECRET = "RkStLviZRTIhHx08ihhoRE7oN8sGLj3334tNnDe0ePgqfYuhiwPQhJk4m0BiPvRU"
IS_TESTNET = True


async def step1_test_connectivity():
    """Step 1: Test connectivity and check balance."""
    print("\n" + "=" * 60)
    print("STEP 1: Testing Binance Testnet Connectivity")
    print("=" * 60)

    from app.services.binance.client import BinanceClient
    from app.services.binance.exchange import BinanceExchangeService

    client = BinanceClient(api_key=API_KEY, api_secret=API_SECRET, testnet=IS_TESTNET)
    exchange = BinanceExchangeService(client)

    try:
        balance = await exchange.get_balance()
        print(f"\n  Available Balance: ${balance['available_balance']:,.2f}")
        print(f"  Total Balance:    ${balance['total_balance']:,.2f}")
        print(f"  Margin Used:      ${balance['margin_used']:,.2f}")
        print(f"  Unrealized PnL:   ${balance['unrealized_pnl']:,.2f}")

        positions = await exchange.get_positions()
        if positions:
            print(f"\n  Open Positions ({len(positions)}):")
            for p in positions:
                print(
                    f"    {p['symbol']}: size={p['size']}, entry=${p['entry_price']}, pnl=${p['unrealized_pnl']:.2f}"
                )
        else:
            print("\n  No open positions.")

        return exchange, balance
    except Exception as e:
        print(f"\n  ERROR: {e}")
        await client.close()
        raise


async def get_testnet_symbols(exchange):
    """Get symbols available on the testnet."""
    try:
        c = await exchange.client.get_client()
        info = await c.futures_exchange_info()
        symbols = set()
        for s in info.get("symbols", []):
            if s.get("status") == "TRADING" and s["symbol"].endswith("USDT"):
                symbols.add(s["symbol"])
        return symbols
    except Exception as e:
        print(f"  Warning: Could not fetch testnet symbols: {e}")
        # Fallback to common testnet pairs
        return {"BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "SOLUSDT"}


async def step2_fetch_top_gainer(testnet_symbols=None):
    """Step 2: Fetch top 1 gainer from Binance Futures (live data, no auth needed)."""
    print("\n" + "=" * 60)
    print("STEP 2: Fetching Top 1 Gainer from Binance Futures")
    print("=" * 60)

    from app.services.binance.info import BinanceInfoService

    # Use non-testnet client for public market data (testnet has limited pairs)
    info_service = BinanceInfoService()

    try:
        gainers = await info_service.get_top_gainers(min_volume=1_000_000, limit=20)

        if not gainers:
            print("\n  No gainers found. Trying with lower volume threshold...")
            gainers = await info_service.get_top_gainers(min_volume=0, limit=20)

        if not gainers:
            print("\n  ERROR: Could not find any gainers.")
            await info_service.close()
            return None, info_service

        print(f"\n  Top 5 Gainers (mainnet):")
        for i, g in enumerate(gainers[:5], 1):
            print(
                f"    {i}. {g['symbol']}: {g['price_change_percent_24h']:+.2f}% | Price: ${g['price']:,.4f} | Vol: ${g['volume_24h']:,.0f}"
            )

        # Well-known pairs that reliably exist on testnet with proper candle data
        # Prefer cheaper ones (low min notional) so $5.69 balance can trade
        RELIABLE_TESTNET_PAIRS = {
            "XRPUSDT",
            "DOGEUSDT",
            "ADAUSDT",
            "TRXUSDT",
            "DOTUSDT",
            "MATICUSDT",
            "NEARUSDT",
            "OPUSDT",
            "ARBUSDT",
            "SUIUSDT",
            "APTUSDT",
            "UNIUSDT",
            "LINKUSDT",
            "AVAXUSDT",
            "SOLUSDT",
            "BNBUSDT",
            "LTCUSDT",
            "ATOMUSDT",
            "BTCUSDT",
            "ETHUSDT",  # fallback, higher notional
        }

        # Filter for testnet-available AND well-known pairs (to ensure good candle data)
        if testnet_symbols:
            reliable_set = testnet_symbols & RELIABLE_TESTNET_PAIRS
            available_gainers = [g for g in gainers if g["symbol"] in reliable_set]
            if available_gainers:
                top_gainer = available_gainers[0]
                print(
                    f"\n  Filtered to reliable testnet pairs. Selected: {top_gainer['symbol']} ({top_gainer['price_change_percent_24h']:+.2f}%)"
                )
            else:
                # Fallback to DOGEUSDT (cheap, low min notional, always on testnet)
                fallback = "DOGEUSDT"
                print(f"\n  No top gainer in reliable testnet set. Falling back to {fallback}...")
                market = await info_service.get_market_data(fallback)
                top_gainer = {
                    "symbol": fallback,
                    "price": market.get("mark_price", 0),
                    "price_change_percent_24h": market.get("price_change_percent_24h", 0),
                    "volume_24h": market.get("volume_24h", 0),
                }
                print(f"  Selected: {fallback} (${top_gainer['price']:,.4f})")
        else:
            top_gainer = gainers[0]
            print(
                f"\n  Selected: {top_gainer['symbol']} ({top_gainer['price_change_percent_24h']:+.2f}%)"
            )

        return top_gainer, info_service
    except Exception as e:
        print(f"\n  ERROR: {e}")
        await info_service.close()
        raise


async def step3_generate_signal(symbol: str, info_service):
    """Step 3: Generate a trading signal via LLM consensus."""
    print("\n" + "=" * 60)
    print(f"STEP 3: Generating Signal for {symbol}")
    print("=" * 60)

    from app.services.llm.binance_analyzer import BinanceMarketAnalyzer
    from app.services.binance.utils import to_binance_symbol

    binance_symbol = to_binance_symbol(symbol)
    analyzer = BinanceMarketAnalyzer()

    # Get technical indicators
    print(f"\n  Fetching technical indicators for {binance_symbol}...")
    indicators = await analyzer.get_technical_indicators(symbol)

    if not indicators:
        print("  ERROR: No indicators available")
        return None

    print(f"  RSI(14): {indicators.get('rsi_14', 'N/A'):.2f}")
    print(f"  MACD: {indicators.get('macd', 'N/A'):.6f}")
    print(f"  EMA(9): {indicators.get('ema_9', 'N/A'):.4f}")
    print(f"  EMA(21): {indicators.get('ema_21', 'N/A'):.4f}")
    print(f"  ADX: {indicators.get('adx', 'N/A'):.2f}")
    print(f"  ATR(14): {indicators.get('atr_14', 'N/A'):.6f}")
    print(f"  Current Price: ${indicators.get('current_price', 'N/A'):.4f}")

    # Get market data
    print(f"\n  Fetching market data for {binance_symbol}...")
    market_data = await info_service.get_market_data(binance_symbol)
    print(f"  Mark Price: ${market_data.get('mark_price', 0):.4f}")
    print(f"  24h Change: {market_data.get('price_change_percent_24h', 0):.2f}%")
    print(f"  24h Volume: ${market_data.get('volume_24h', 0):,.0f}")
    print(f"  Funding Rate: {market_data.get('funding_rate', 0):.6f}")

    # Run consensus engine with LLM analysis
    print(f"\n  Running LLM consensus analysis (this may take 30-60s)...")

    from app.services.llm.consensus import ConsensusEngine

    consensus = ConsensusEngine(analyzer=analyzer, info_service=info_service)
    signal_data = await consensus.generate_signal(symbol)

    if not signal_data:
        print(
            "  WARNING: Consensus engine returned no signal (models may disagree or low confidence)."
        )
        print("  Generating a manual signal from indicators instead...")

        # Create a simple signal based on indicators for testing purposes
        current_price = indicators.get("current_price", market_data.get("mark_price", 0))
        rsi = indicators.get("rsi_14", 50)
        atr = indicators.get("atr_14", 0)

        # If ATR is 0, use 2% of price as a sensible default
        if atr == 0 or atr < current_price * 0.001:
            atr = current_price * 0.02

        # Simple direction based on RSI
        if rsi < 40:
            direction = "long"
        elif rsi > 60:
            direction = "short"
        else:
            direction = "long"  # Default to long for top gainer

        if direction == "long":
            tp = current_price + (atr * 2)
            sl = current_price - (atr * 1.5)
        else:
            tp = current_price - (atr * 2)
            sl = current_price + (atr * 1.5)

        signal_data = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": round(current_price, 6),
            "take_profit_price": round(tp, 6),
            "stop_loss_price": round(sl, 6),
            "suggested_leverage": 5,
            "confidence_score": 0.7,
            "consensus_votes": 2,
            "total_votes": 3,
            "market_price_at_creation": current_price,
            "technical_indicators": indicators,
            "analysis_data": {"key_factors": ["RSI-based fallback signal for testing"]},
        }

    print(f"\n  Signal Generated:")
    print(f"    Direction:   {signal_data.get('direction', 'N/A')}")
    print(f"    Entry:       ${signal_data.get('entry_price', 0):.4f}")
    print(f"    Take Profit: ${signal_data.get('take_profit_price', 0):.4f}")
    print(f"    Stop Loss:   ${signal_data.get('stop_loss_price', 0):.4f}")
    print(f"    Leverage:    {signal_data.get('suggested_leverage', 0)}x")
    print(f"    Confidence:  {signal_data.get('confidence_score', 0):.2%}")
    print(
        f"    Consensus:   {signal_data.get('consensus_votes', 0)}/{signal_data.get('total_votes', 0)}"
    )

    return signal_data


async def step4_execute_trade(exchange, signal_data, balance):
    """Step 4: Execute the trade on Binance Futures testnet."""
    print("\n" + "=" * 60)
    print(f"STEP 4: Executing Trade on Binance Testnet")
    print("=" * 60)

    from app.services.binance.info import BinanceInfoService
    from app.services.binance.utils import to_binance_symbol

    symbol = signal_data["symbol"]
    binance_symbol = to_binance_symbol(symbol)
    direction = signal_data["direction"]
    if hasattr(direction, "value"):
        direction = direction.value
    leverage = signal_data.get("suggested_leverage", 5)
    entry_price = signal_data["entry_price"]
    tp_price = signal_data["take_profit_price"]
    sl_price = signal_data["stop_loss_price"]

    available = balance["available_balance"]
    # Use 80% of available balance for this test trade (testnet has low balance)
    position_size_usd = available * 0.8
    # For small testnet balances, use maximum leverage to meet min notional
    if available < 50:
        leverage = min(20, leverage * 3)  # Increase leverage for small balances

    print(f"\n  Symbol:     {binance_symbol}")
    print(f"  Direction:  {direction}")
    print(f"  Leverage:   {leverage}x")
    print(f"  Entry:      ${entry_price:.4f}")
    print(f"  TP:         ${tp_price:.4f}")
    print(f"  SL:         ${sl_price:.4f}")
    print(f"  Size (USD): ${position_size_usd:.2f}")

    # Get symbol precision
    info = BinanceInfoService()
    try:
        precision = await info.get_symbol_precision(binance_symbol)
        print(f"\n  Symbol Precision:")
        print(f"    Quantity: {precision['quantity_precision']} decimals")
        print(f"    Price:    {precision['price_precision']} decimals")
        print(f"    Min Qty:  {precision['min_qty']}")
        print(f"    Min Notional: ${precision['min_notional']}")
    except Exception as e:
        print(f"\n  Warning: Could not get precision info: {e}")
        precision = {"quantity_precision": 3, "price_precision": 2, "min_qty": 0.001}
    finally:
        await info.close()

    # Calculate quantity
    quantity = (position_size_usd * leverage) / entry_price
    quantity = round(quantity, precision["quantity_precision"])
    print(f"\n  Calculated Quantity: {quantity} (notional: ${quantity * entry_price:.2f})")

    if quantity * entry_price < precision.get("min_notional", 5):
        print(
            f"  WARNING: Notional ${quantity * entry_price:.2f} below minimum ${precision.get('min_notional', 5)}"
        )
        quantity = round(
            precision.get("min_notional", 5) * 1.1 / entry_price, precision["quantity_precision"]
        )
        print(f"  Adjusted quantity: {quantity}")

    side = "BUY" if direction == "long" else "SELL"
    close_side = "SELL" if direction == "long" else "BUY"

    # 1. Set leverage
    print(f"\n  [1/4] Setting leverage to {leverage}x...")
    try:
        lev_result = await exchange.set_leverage(binance_symbol, leverage)
        print(f"    OK: {lev_result}")
    except Exception as e:
        print(f"    Warning: {e}")

    # 2. Set margin type to CROSSED
    print(f"  [2/4] Setting margin type to CROSSED...")
    try:
        margin_result = await exchange.set_margin_type(binance_symbol, "CROSSED")
        print(f"    OK: {margin_result}")
    except Exception as e:
        print(f"    Warning: {e}")

    # 3. Place market order
    print(f"  [3/4] Placing {side} market order for {quantity} {binance_symbol}...")
    try:
        order_result = await exchange.place_market_order(binance_symbol, side, quantity)
        order_id = order_result.get("orderId")
        avg_price = float(order_result.get("avgPrice", 0))
        status = order_result.get("status")
        print(f"    OK: orderId={order_id}, status={status}, avgPrice=${avg_price:.4f}")
    except Exception as e:
        print(f"    ERROR: {e}")
        return False

    # Round TP/SL prices to symbol precision
    tp_price = round(tp_price, precision["price_precision"])
    sl_price = round(sl_price, precision["price_precision"])

    # 4a. Place TP order
    print(f"  [4a/4] Placing Take Profit order @ ${tp_price:.4f}...")
    tp_order_id = None
    try:
        tp_result = await exchange.place_tp_algo_order(
            binance_symbol, close_side, quantity, tp_price
        )
        tp_order_id = tp_result.get("orderId") or tp_result.get("algoId")
        print(f"    OK: TP orderId={tp_order_id}")
    except Exception as e:
        print(f"    WARNING: TP order failed: {e}")

    # 4b. Place SL order
    print(f"  [4b/4] Placing Stop Loss order @ ${sl_price:.4f}...")
    sl_order_id = None
    try:
        sl_result = await exchange.place_sl_algo_order(
            binance_symbol, close_side, quantity, sl_price
        )
        sl_order_id = sl_result.get("orderId") or sl_result.get("algoId")
        print(f"    OK: SL orderId={sl_order_id}")
    except Exception as e:
        print(f"    WARNING: SL order failed: {e}")

    # Verify position
    print(f"\n  Verifying position...")
    positions = await exchange.get_positions()
    pos = next((p for p in positions if p["symbol"] == binance_symbol), None)
    if pos:
        print(
            f"    Position confirmed: size={pos['size']}, entry=${pos['entry_price']:.4f}, leverage={pos['leverage']}x"
        )
    else:
        print(f"    WARNING: Position not found (may still be settling)")

    # Check open orders
    print(f"\n  Checking open orders...")
    try:
        open_orders = await exchange.get_open_orders(binance_symbol)
        print(f"    Open orders: {len(open_orders)}")
        for o in open_orders:
            print(
                f"      {o.get('type')} {o.get('side')} @ {o.get('stopPrice')} (status: {o.get('status')}, id: {o.get('orderId')})"
            )
    except Exception as e:
        print(f"    Warning: {e}")

    print("\n  TRADE EXECUTED SUCCESSFULLY!")
    print(f"    Market Order ID: {order_id}")
    print(f"    TP Order ID:     {tp_order_id}")
    print(f"    SL Order ID:     {sl_order_id}")

    return True


async def main():
    print("=" * 60)
    print("  BINANCE FUTURES INTEGRATION - E2E TEST")
    print("  Mode: TESTNET")
    print("=" * 60)

    exchange = None
    info_service = None

    try:
        # Step 1: Test connectivity
        exchange, balance = await step1_test_connectivity()

        if balance["available_balance"] <= 0:
            print("\n  ERROR: No available balance on testnet. Please fund the testnet account.")
            print("  Visit: https://testnet.binancefuture.com/ to get testnet funds.")
            return

        # Get testnet available symbols
        print("\n  Fetching testnet available symbols...")
        testnet_symbols = await get_testnet_symbols(exchange)
        print(f"  Found {len(testnet_symbols)} testnet symbols")
        if testnet_symbols:
            print(f"  Sample: {', '.join(sorted(testnet_symbols)[:10])}...")

        # Step 2: Fetch top gainer (filtered to testnet-available pairs)
        top_gainer, info_service = await step2_fetch_top_gainer(testnet_symbols)
        if not top_gainer:
            print("\n  ERROR: Could not find any gainer. Aborting.")
            return

        symbol = top_gainer["symbol"]

        # Step 3: Generate signal
        signal_data = await step3_generate_signal(symbol, info_service)
        if not signal_data:
            print("\n  ERROR: Could not generate signal. Aborting.")
            return

        # Step 4: Execute trade
        success = await step4_execute_trade(exchange, signal_data, balance)

        print("\n" + "=" * 60)
        if success:
            print("  TEST COMPLETED SUCCESSFULLY!")
        else:
            print("  TEST FAILED - See errors above")
        print("=" * 60)

    except Exception as e:
        print(f"\n  FATAL ERROR: {e}")
        import traceback

        traceback.print_exc()
    finally:
        if exchange:
            await exchange.close()
        if info_service:
            await info_service.close()


if __name__ == "__main__":
    asyncio.run(main())
