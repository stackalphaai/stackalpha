"""
Task guard: check if a Celery task is enabled before running.

Uses the SystemConfig table to look up `task_enabled:<task_name>`.
Missing key = enabled by default.
"""

import logging

from sqlalchemy import select

logger = logging.getLogger(__name__)


async def is_task_enabled(db, task_name: str) -> bool:
    """Return True if the task is enabled (default: True when no config row exists)."""
    from app.models import SystemConfig

    key = f"task_enabled:{task_name}"
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        return True
    enabled = row.value.strip().lower() not in ("false", '"false"', "0")
    return enabled
