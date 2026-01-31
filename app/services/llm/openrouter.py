import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.core.exceptions import LLMServiceError

logger = logging.getLogger(__name__)


class OpenRouterClient:
    def __init__(self):
        self.base_url = settings.openrouter_base_url
        self.api_key = settings.openrouter_api_key
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=60.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://stackalpha.io",
                    "X-Title": "StackAlpha",
                },
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self.get_client()

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format:
            payload["response_format"] = response_format

        try:
            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenRouter API error: {e.response.status_code} - {e.response.text}")
            raise LLMServiceError(f"LLM API request failed: {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"OpenRouter request error: {str(e)}")
            raise LLMServiceError(f"LLM request failed: {str(e)}") from e
        except Exception as e:
            logger.error(f"Unexpected error in OpenRouter client: {str(e)}")
            raise LLMServiceError(f"Unexpected LLM error: {str(e)}") from e

    async def get_completion_text(
        self,
        model: str,
        messages: list[dict[str, str]],
        **kwargs,
    ) -> str:
        response = await self.chat_completion(model, messages, **kwargs)

        try:
            return response["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            logger.error(f"Failed to extract completion text: {e}")
            raise LLMServiceError("Invalid response format from LLM") from e

    async def get_available_models(self) -> list[dict[str, Any]]:
        client = await self.get_client()

        try:
            response = await client.get("/models")
            response.raise_for_status()
            data = response.json()
            return data.get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch models: {e}")
            return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


_client_instance: OpenRouterClient | None = None


def get_openrouter_client() -> OpenRouterClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = OpenRouterClient()
    return _client_instance


async def close_openrouter_client():
    global _client_instance
    if _client_instance:
        await _client_instance.close()
        _client_instance = None
