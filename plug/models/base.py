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
    """Try models in order until one succeeds.

    Uses a single provider (they all go through the same proxy)
    but rotates through model names on failure.
    """

    def __init__(
        self,
        provider: ChatProvider,
        models: list[str],
    ):
        self.provider = provider
        self.models = models
        self._current_index = 0

    @property
    def current_model(self) -> str:
        return self.models[self._current_index]

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        """Try each model in turn until one succeeds."""
        last_error: Exception | None = None

        for i in range(len(self.models)):
            idx = (self._current_index + i) % len(self.models)
            model = self.models[idx]

            try:
                response = await self.provider.chat(
                    messages,
                    model=model,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                # Success — remember this model for next time
                self._current_index = idx
                return response

            except Exception as e:
                logger.warning("Model %s failed: %s", model, e)
                last_error = e
                continue

        # All models failed
        raise RuntimeError(
            f"All models failed. Last error: {last_error}"
        ) from last_error

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
