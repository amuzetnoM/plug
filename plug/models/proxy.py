"""
Proxy Chat Provider
====================

OpenAI-compatible provider that talks to copilot-proxy at localhost:3000.
Supports tool calling (multi-turn) and streaming.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from plug.models.base import ChatProvider, ChatResponse, Message, ToolCall

logger = logging.getLogger(__name__)


class ProxyChatProvider(ChatProvider):
    """Chat provider using an OpenAI-compatible proxy endpoint."""

    def __init__(
        self,
        base_url: str = "http://localhost:3000/v1",
        api_key: str = "n/a",
        timeout: float = 120.0,
        default_model: str = "claude-opus-4.6",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _build_request(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Build the API request body."""
        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [m.to_api_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if tools and not stream:
            # Only include tools for non-streaming requests
            body["tools"] = tools
            body["tool_choice"] = "auto"

        return body

    # ── Non-streaming chat ───────────────────────────────────────────────

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        body = self._build_request(
            messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )

        used_model = body["model"]
        logger.debug("Chat request to %s (tools=%d)", used_model, len(tools or []))

        resp = await self._client.post("/chat/completions", json=body)
        if resp.status_code >= 400:
            body_text = resp.text[:500] if resp.text else "(empty)"
            logger.error(
                "API error %d for model %s: %s",
                resp.status_code, used_model, body_text,
            )
        resp.raise_for_status()
        data = resp.json()

        return self._parse_response(data)

    def _parse_response(self, data: dict[str, Any]) -> ChatResponse:
        """Parse an OpenAI-format chat completion response."""
        choice = data["choices"][0]
        msg_data = choice["message"]

        # Parse tool calls if present
        tool_calls = None
        if "tool_calls" in msg_data and msg_data["tool_calls"]:
            tool_calls = []
            for tc in msg_data["tool_calls"]:
                fn = tc["function"]
                args_raw = fn.get("arguments", "{}")
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        args = {"_raw": args_raw}
                else:
                    args = args_raw

                tool_calls.append(ToolCall(
                    id=tc["id"],
                    name=fn["name"],
                    arguments=args,
                ))

        message = Message(
            role="assistant",
            content=msg_data.get("content"),
            tool_calls=tool_calls,
        )

        usage = data.get("usage", {})

        return ChatResponse(
            message=message,
            model=data.get("model", ""),
            finish_reason=choice.get("finish_reason", ""),
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        )

    # ── Streaming chat ───────────────────────────────────────────────────

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream text chunks from the model.

        Yields content deltas as they arrive.
        Note: streaming with tool_calls is complex — if tools are needed,
        use non-streaming `chat()` instead. This method ignores tools.
        """
        body = self._build_request(
            messages,
            model=model,
            tools=None,  # No tools in streaming mode
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        async with self._client.stream(
            "POST", "/chat/completions", json=body
        ) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue

                payload = line[6:].strip()
                if payload == "[DONE]":
                    break

                try:
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
