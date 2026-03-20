"""Twitter/X posting service for StackAlpha daily content."""

import logging
from datetime import UTC, datetime

import tweepy

from app.config import settings
from app.services.llm.openrouter import get_openrouter_client

logger = logging.getLogger(__name__)

# Topics the LLM rotates through for variety
TWEET_TOPICS = [
    "a quick tip about risk management in algo trading",
    "why AI-powered trading signals beat gut feelings",
    "a lesson about position sizing and capital preservation",
    "how consensus-based AI models reduce false signals",
    "the importance of backtesting before going live",
    "a hot take on the current crypto market with an algo trading angle",
    "why most retail traders lose and how automation helps",
    "the future of AI in financial markets",
    "a simple explanation of how take-profit and stop-loss work",
    "why discipline matters more than prediction accuracy",
    "how StackAlpha uses multiple AI models to validate trades",
    "a myth about algo trading that needs debunking",
    "what separates profitable traders from the rest",
    "why 24/7 markets need 24/7 AI monitoring",
]


def _get_twitter_client() -> tweepy.Client:
    """Create an authenticated Twitter API v2 client using OAuth 1.0a (user context).

    Posting tweets requires all 4 OAuth 1.0a keys (consumer + access).
    The bearer token is optional and used for read-only app-level requests.
    """
    return tweepy.Client(
        bearer_token=settings.twitter_bearer_token or None,
        consumer_key=settings.twitter_consumer_key,
        consumer_secret=settings.twitter_consumer_secret,
        access_token=settings.twitter_access_token,
        access_token_secret=settings.twitter_access_token_secret,
        wait_on_rate_limit=True,
    )


async def generate_tweet(custom_prompt: str | None = None) -> str:
    """Generate a tweet using an LLM via OpenRouter.

    Args:
        custom_prompt: Override the default system prompt (admin-configurable).

    Returns:
        The generated tweet text (max 280 chars).
    """
    # Pick a topic based on the day of year for variety
    day_index = datetime.now(UTC).timetuple().tm_yday
    topic = TWEET_TOPICS[day_index % len(TWEET_TOPICS)]

    system_prompt = custom_prompt or settings.twitter_prompt

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"Today's focus: {topic}\n\n"
                f"Date: {datetime.now(UTC).strftime('%B %d, %Y')}\n\n"
                "Write exactly ONE tweet. Max 280 characters. "
                "Do not include quotes around it. Just the raw tweet text."
            ),
        },
    ]

    # Use the first active LLM model
    model = settings.llm_models[0] if settings.llm_models else "anthropic/claude-sonnet-4.6"

    client = get_openrouter_client()
    tweet = await client.get_completion_text(
        model=model,
        messages=messages,
        temperature=0.9,
        max_tokens=100,
    )

    # Clean up: strip quotes, ensure under 280 chars
    tweet = tweet.strip().strip('"').strip("'")
    if len(tweet) > 280:
        # Truncate at last space before 277 chars and add "..."
        tweet = tweet[:277].rsplit(" ", 1)[0] + "..."

    return tweet


def post_tweet(text: str) -> dict:
    """Post a tweet to X/Twitter.

    Returns:
        Dict with tweet id and text on success, or error info on failure.
    """
    try:
        client = _get_twitter_client()
        response = client.create_tweet(text=text)
        tweet_data = response.data
        tweet_id = tweet_data["id"] if tweet_data else "unknown"
        logger.info(f"Tweet posted successfully: id={tweet_id}, text={text[:50]}...")
        return {
            "success": True,
            "tweet_id": tweet_id,
            "text": text,
            "posted_at": datetime.now(UTC).isoformat(),
        }
    except tweepy.TweepyException as e:
        logger.error(f"Failed to post tweet: {e}")
        return {
            "success": False,
            "error": str(e),
            "text": text,
        }


async def generate_and_post() -> dict:
    """Generate a tweet via LLM and post it to X. Full pipeline."""
    if not settings.twitter_enabled:
        logger.info("Twitter agent is disabled — skipping")
        return {"success": False, "error": "Twitter agent is disabled"}

    if not all(
        [
            settings.twitter_consumer_key,
            settings.twitter_consumer_secret,
            settings.twitter_access_token,
            settings.twitter_access_token_secret,
        ]
    ):
        logger.error("Twitter OAuth 1.0a credentials not fully configured")
        return {
            "success": False,
            "error": "Twitter credentials not configured (need all 4 OAuth keys)",
        }

    tweet = await generate_tweet()
    logger.info(f"Generated tweet: {tweet}")
    result = post_tweet(tweet)
    return result
