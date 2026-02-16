"""
Ollama Chat Provider
=====================

Local Ollama provider for fallback when proxy is unavailable.
Supports tool calling (Ollama 0.5+) and streaming.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from plug.models.base import ChatProvider, ChatResponse, Message, ToolCall

logger = logging.getLogger(__name__)


class OllamaChatProvider(ChatProvider):
    """Chat provider using local Ollama instance."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        default_model: str = "qwen2.5-coder:7b",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[str]:
        """List available Ollama models."""
        try:
            resp = await self._client.get("/api/tags")
            resp.raise_for_status()
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.warning("Failed to list Ollama models: %s", e)
            return []

    async def is_available(self) -> bool:
        """Quick health check."""
        try:
            resp = await self._client.get("/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        used_model = model or self.default_model

        body: dict[str, Any] = {
            "model": used_model,
            "messages": [self._to_ollama_msg(m) for m in messages],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        if tools:
            body["tools"] = tools

        logger.debug("Ollama chat: model=%s tools=%d", used_model, len(tools or []))

        resp = await self._client.post("/api/chat", json=body)
        resp.raise_for_status()
        data = resp.json()

        return self._parse_response(data, used_model)

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        used_model = model or self.default_model

        body: dict[str, Any] = {
            "model": used_model,
            "messages": [self._to_ollama_msg(m) for m in messages],
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        async with self._client.stream("POST", "/api/chat", json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

    @staticmethod
    def _to_ollama_msg(m: Message) -> dict[str, Any]:
        """Convert Message to Ollama format."""
        msg: dict[str, Any] = {"role": m.role, "content": m.content or ""}

        if m.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in m.tool_calls
            ]

        return msg

    @staticmethod
    def _parse_response(data: dict[str, Any], model: str) -> ChatResponse:
        msg_data = data.get("message", {})

        tool_calls = None
        raw_calls = msg_data.get("tool_calls")
        if raw_calls:
            tool_calls = []
            for i, tc in enumerate(raw_calls):
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                tool_calls.append(ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=fn.get("name", "unknown"),
                    arguments=args,
                ))

        message = Message(
            role="assistant",
            content=msg_data.get("content"),
            tool_calls=tool_calls,
        )

        return ChatResponse(
            message=message,
            model=model,
            finish_reason="stop",
            usage={
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            },
        )
