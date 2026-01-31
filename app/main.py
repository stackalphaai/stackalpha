import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.admin import setup_admin
from app.api.v1.router import router as api_router
from app.api.webhooks.nowpayments import router as nowpayments_router
from app.api.webhooks.telegram import router as telegram_router
from app.config import settings
from app.core.exceptions import HyperTradeException
from app.core.middleware import setup_middlewares
from app.database import close_db

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting StackAlpha Backend...")

    logger.info("Application startup complete")
    yield

    logger.info("Shutting down StackAlpha Backend...")
    await close_db()

    from app.services.hyperliquid import close_hyperliquid_client, close_ws_manager

    await close_hyperliquid_client()
    await close_ws_manager()

    from app.services.llm import close_openrouter_client

    await close_openrouter_client()

    logger.info("Application shutdown complete")


app = FastAPI(
    title=settings.app_name,
    description="AI-Powered Trading Platform with Hyperliquid Integration",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

setup_middlewares(app)

# Session middleware required for SQLAdmin authentication (must be after other middlewares)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

# Setup SQLAdmin for automatic admin CRUD interface
admin = setup_admin(app)
logger.info("SQLAdmin mounted at /admin")


@app.exception_handler(HyperTradeException)
async def hypertrade_exception_handler(request: Request, exc: HyperTradeException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        errors.append(
            {
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
            }
        )

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": "Validation error",
            "details": errors,
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception: {exc}")

    if settings.debug:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
                "type": type(exc).__name__,
            },
        )

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
        },
    )


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "admin": "/admin",
    }


app.include_router(api_router, prefix="/api")
app.include_router(nowpayments_router, prefix="/api/v1")
app.include_router(telegram_router, prefix="/api/v1")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        workers=1 if settings.debug else 4,
    )
