import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.security import decode_token
from app.services.top_gainers_service import get_top_gainers_service
from app.services.trade_stream_service import get_trade_stream_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])


@router.websocket("/top-gainers")
async def top_gainers_ws(websocket: WebSocket):
    """
    WebSocket endpoint for real-time top gainers data.

    Streams top gainers, losers, and volume leaders from Hyperliquid
    every 2 seconds. No authentication required (public market data).

    Message format:
    {
        "type": "top_gainers_update",
        "timestamp": 1234567890.123,
        "data": {
            "gainers": [...],
            "losers": [...],
            "top_volume": [...],
            "total_coins": 150
        }
    }
    """
    await websocket.accept()

    service = get_top_gainers_service()
    await service.register_client(websocket)

    try:
        # Keep the connection alive by reading client messages (pings/pongs)
        while True:
            # Wait for any message from client (heartbeat or close)
            data = await websocket.receive_text()
            # Client can send "ping" to keep alive
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected normally")
    except Exception as e:
        logger.debug(f"WebSocket client error: {e}")
    finally:
        service.unregister_client(websocket)


@router.websocket("/admin/trades")
async def admin_trades_ws(websocket: WebSocket, token: str = Query(...)):
    """
    Admin WebSocket endpoint for real-time ALL open trades.

    Requires JWT access token from an admin user: /ws/admin/trades?token=<jwt>

    Streams all open trades across all users with live prices and PnL every 2s.
    """
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models import User

    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    user_id = payload.get("sub")
    if not user_id:
        await websocket.close(code=4001, reason="Invalid token payload")
        return

    # Verify user is admin
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_admin:
            await websocket.close(code=4003, reason="Admin access required")
            return

    await websocket.accept()

    service = get_trade_stream_service()
    await service.register_admin_client(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        logger.debug("Admin trade stream client disconnected")
    except Exception as e:
        logger.debug(f"Admin trade stream client error: {e}")
    finally:
        service.unregister_admin_client(websocket)


@router.websocket("/trades")
async def trades_ws(websocket: WebSocket, token: str = Query(...)):
    """
    Authenticated WebSocket endpoint for real-time open trade data.

    Requires JWT access token as query parameter: /ws/trades?token=<jwt>

    Streams open trades with live mark prices, unrealized PnL, and
    TP/SL proximity every 2 seconds.

    Message format:
    {
        "type": "trades_update",
        "timestamp": 1234567890.123,
        "data": {
            "trades": [...],
            "summary": { "total_open": 3, "total_unrealized_pnl": 45.20, ... }
        }
    }
    """
    # Validate JWT token
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    user_id = payload.get("sub")
    if not user_id:
        await websocket.close(code=4001, reason="Invalid token payload")
        return

    await websocket.accept()

    service = get_trade_stream_service()
    await service.register_client(user_id, websocket)

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        logger.debug(f"Trade stream client disconnected for user {user_id}")
    except Exception as e:
        logger.debug(f"Trade stream client error for user {user_id}: {e}")
    finally:
        service.unregister_client(user_id, websocket)
