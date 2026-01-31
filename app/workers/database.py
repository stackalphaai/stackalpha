"""
Worker-specific database utilities.

Creates fresh database connections for each Celery task execution
to avoid event loop closed issues with asyncio.run().
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


def create_worker_engine():
    """Create a fresh async engine for worker tasks."""
    return create_async_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=settings.debug,
    )


@asynccontextmanager
async def get_worker_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Get a database session for worker tasks.

    Creates a fresh engine and session for each task to avoid
    event loop issues when using asyncio.run() in Celery tasks.
    """
    engine = create_worker_engine()
    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with async_session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    # Dispose of the engine to clean up connections
    await engine.dispose()
