import logging
import time
from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from redis import asyncio as aioredis
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: FastAPI,
        redis_url: str,
        requests_limit: int = 100,
        window_seconds: int = 60,
    ):
        super().__init__(app)
        self.redis_url = redis_url
        self.requests_limit = requests_limit
        self.window_seconds = window_seconds
        self.redis: aioredis.Redis | None = None
        # Disable rate limiting in development and testing environments
        self.enabled = settings.app_env == "production"

    async def get_redis(self) -> aioredis.Redis:
        if self.redis is None:
            self.redis = await aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self.redis

    def get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return await call_next(request)

        # Skip rate limiting for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip rate limiting for documentation
        if request.url.path.startswith("/docs") or request.url.path.startswith("/openapi"):
            return await call_next(request)

        # Skip rate limiting for webhooks
        if request.url.path.startswith("/api/v1/webhooks"):
            return await call_next(request)

        # Skip rate limiting for health checks
        if request.url.path in ("/", "/health", "/api/v1/health"):
            return await call_next(request)

        client_ip = self.get_client_ip(request)
        rate_key = f"rate_limit:{client_ip}"

        try:
            redis = await self.get_redis()
            current = await redis.get(rate_key)

            if current is None:
                await redis.setex(rate_key, self.window_seconds, 1)
            elif int(current) >= self.requests_limit:
                logger.warning(f"Rate limit exceeded for IP: {client_ip}")
                # Return proper JSON response instead of raising exception
                from starlette.responses import JSONResponse

                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Rate limit exceeded. Try again in {self.window_seconds} seconds.",
                        "error": "rate_limit_exceeded",
                    },
                    headers={"Retry-After": str(self.window_seconds)},
                )
            else:
                await redis.incr(rate_key)

        except aioredis.RedisError as e:
            logger.error(f"Redis error in rate limiting: {e}")

        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()

        response = await call_next(request)

        process_time = time.time() - start_time
        logger.info(
            f"{request.method} {request.url.path} "
            f"status={response.status_code} "
            f"duration={process_time:.3f}s"
        )

        response.headers["X-Process-Time"] = str(process_time)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        return response


def setup_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Process-Time"],
    )


def setup_middlewares(app: FastAPI) -> None:
    # Order matters: last added = outermost in the ASGI stack.
    # CORS must be outermost so ALL responses (including errors from
    # BaseHTTPMiddleware subclasses) get CORS headers.
    app.add_middleware(
        RateLimitMiddleware,
        redis_url=settings.redis_url,
        requests_limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    setup_cors(app)
