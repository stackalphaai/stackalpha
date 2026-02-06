import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.top_gainers_service import get_top_gainers_service

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
