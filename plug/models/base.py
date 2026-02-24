"""
Base types and abstract provider for LLM chat completions.
=============================================================

Defines the message format, tool-call structures, and the
ChatProvider ABC that all providers implement.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

logger = logging.getLogger(__name__)


# ── Message types ────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """A tool invocation requested by the model."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """A single message in the conversation.

    Roles:
        system   — system prompt
        user     — human input
        assistant — model output (may contain tool_calls)
        tool     — tool result (must reference tool_call_id)
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # For role="tool" messages
    name: str | None = None          # Tool name for role="tool"

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API message format."""
        msg: dict[str, Any] = {"role": self.role}

        if self.content is not None:
            msg["content"] = self.content

        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": (
                            tc.arguments
                            if isinstance(tc.arguments, str)
                            else __import__("json").dumps(tc.arguments)
                        ),
                    },
                }
                for tc in self.tool_calls
            ]

        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id

        if self.name is not None:
            msg["name"] = self.name

        return msg


@dataclass
class ChatResponse:
    """Response from a chat completion call."""
    message: Message
    model: str = ""
    finish_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.message.tool_calls)


# ── Abstract provider ───────────────────────────────────────────────────

class ChatProvider(ABC):
    """Abstract base for chat completion providers."""

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        """Send a chat completion request.

        Args:
            messages: Conversation history.
            model: Override the default model.
            tools: Tool definitions in OpenAI function-calling format.
            temperature: Sampling temperature.
            max_tokens: Max tokens to generate.

        Returns:
            ChatResponse with the model's reply (may contain tool_calls).
        """
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a chat completion, yielding text chunks.

        Note: Streaming does NOT support tool calls — use non-streaming
        `chat()` when tools are provided.
        """
        ...
        # Make it an async generator
        if False:
            yield ""

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources."""
        ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


# ── Provider chain (fallback logic) ─────────────────────────────────────

class ProviderChain:
    """Try providers and models in order until one succeeds.

    Supports multiple providers (e.g. proxy + ollama) with per-provider
    model lists. Retries within each provider before falling back to next.
    """

    def __init__(
        self,
        provider: ChatProvider,
        models: list[str],
        fallback_providers: list[tuple[ChatProvider, list[str]]] | None = None,
        max_retries: int = 2,
        retry_delay: float = 1.0,
    ):
        self.provider = provider
        self.models = models
        self.fallback_providers = fallback_providers or []
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._current_index = 0

    @property
    def current_model(self) -> str:
        return self.models[self._current_index]

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        """Try primary provider models with retry, then fallback providers.

        Rate-limit aware: detects 429 responses and uses exponential backoff
        with longer delays before trying the next model.
        """
        import asyncio

        last_error: Exception | None = None

        # If a specific model is requested, try it first
        models_to_try = [model] + self.models if model else self.models

        # Try primary provider (with retry + rate limit backoff)
        for i in range(len(models_to_try)):
            m = models_to_try[i]

            for attempt in range(self.max_retries):
                try:
                    response = await self.provider.chat(
                        messages, model=m, tools=tools,
                        temperature=temperature, max_tokens=max_tokens,
                    )
                    return response
                except Exception as e:
                    last_error = e
                    is_rate_limit = _is_rate_limit_error(e)

                    if attempt < self.max_retries - 1:
                        # Exponential backoff: longer for rate limits
                        if is_rate_limit:
                            delay = min(self.retry_delay * (2 ** (attempt + 2)), 30.0)
                            logger.warning(
                                "Model %s rate-limited (attempt %d) — backoff %.1fs",
                                m, attempt + 1, delay,
                            )
                        else:
                            delay = self.retry_delay * (attempt + 1)
                            logger.warning(
                                "Model %s attempt %d failed: %s — retrying in %.1fs",
                                m, attempt + 1, e, delay,
                            )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            "Model %s failed after %d attempts: %s",
                            m, self.max_retries, e,
                        )
                        # If rate-limited on primary, wait before trying fallbacks
                        if is_rate_limit:
                            logger.info("Rate limit detected — waiting 5s before fallback")
                            await asyncio.sleep(5.0)

        # Try fallback providers
        for fb_provider, fb_models in self.fallback_providers:
            for fb_model in fb_models:
                for attempt in range(self.max_retries):
                    try:
                        response = await fb_provider.chat(
                            messages, model=fb_model, tools=tools,
                            temperature=temperature, max_tokens=max_tokens,
                        )
                        logger.info("Fallback succeeded: %s on %s", fb_model, type(fb_provider).__name__)
                        return response
                    except Exception as e:
                        last_error = e
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(self.retry_delay * (attempt + 1))
                        else:
                            logger.warning("Fallback model %s failed: %s", fb_model, e)

        raise RuntimeError(
            f"All providers and models failed. Last error: {last_error}"
        ) from last_error


def _is_rate_limit_error(e: Exception) -> bool:
    """Detect rate limit errors from HTTP status or message."""
    err_str = str(e).lower()
    if "429" in err_str or "rate" in err_str or "too many" in err_str:
        return True
    # httpx.HTTPStatusError carries response
    if hasattr(e, "response") and hasattr(e.response, "status_code"):
        return e.response.status_code == 429
    return False

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream with the current model (no fallback mid-stream)."""
        async for chunk in self.provider.chat_stream(
            messages,
            model=self.current_model,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk

    async def close(self) -> None:
        await self.provider.close()
        for fb_provider, _ in self.fallback_providers:
            await fb_provider.close()
