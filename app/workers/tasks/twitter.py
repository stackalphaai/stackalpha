"""Celery tasks for the StackAlpha Twitter/X agent."""

import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2)
def post_daily_tweet(self):
    """Generate and post a daily tweet about AI/algo trading."""
    try:
        result = asyncio.run(_post_daily_tweet())
        return result
    except Exception as e:
        logger.error(f"Daily tweet task failed: {e}")
        raise self.retry(exc=e, countdown=300) from e


async def _post_daily_tweet() -> dict:
    from app.services.twitter_service import generate_and_post
    from app.workers.database import load_worker_config_overrides

    # Load admin config overrides (twitter_enabled, twitter_prompt, etc.)
    await load_worker_config_overrides()

    result = await generate_and_post()

    if result.get("success"):
        logger.info(f"Daily tweet posted: {result.get('tweet_id')}")
    else:
        logger.warning(f"Daily tweet failed: {result.get('error')}")

    return result
