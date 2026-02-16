"""
GitHub Copilot Chat Provider
==============================

Direct access to GitHub Copilot API using the local GitHub token.
Falls back to proxy provider on failure.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from plug.models.base import ChatProvider, ChatResponse, Message, ToolCall

logger = logging.getLogger(__name__)

COPILOT_ENDPOINT = "https://api.githubcopilot.com/chat/completions"
COPILOT_HOSTS_FILE = Path.home() / ".config" / "github-copilot" / "hosts.json"


def _get_github_token() -> str | None:
    """Get GitHub token from copilot hosts.json or gh CLI."""
    # Try hosts.json first
    if COPILOT_HOSTS_FILE.exists():
        try:
            data = json.loads(COPILOT_HOSTS_FILE.read_text())
            for key, val in data.items():
                if "github.com" in key and isinstance(val, dict):
                    token = val.get("oauth_token")
                    if token:
                        return token
        except Exception as e:
            logger.debug("Failed to read copilot hosts.json: %s", e)

    # Try gh CLI
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        logger.debug("gh auth token failed: %s", e)

    return None


class CopilotChatProvider(ChatProvider):
    """Chat provider using GitHub Copilot API directly."""

    def __init__(
        self,
        default_model: str = "claude-opus-4.6",
        timeout: float = 120.0,
        fallback: ChatProvider | None = None,
    ):
        self.default_model = default_model
        self.fallback = fallback
        self._token = _get_github_token()
        self._client: httpx.AsyncClient | None = None

        if self._token:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "Editor-Version": "vscode/1.95.0",
                    "Editor-Plugin-Version": "copilot/1.0.0",
                    "Openai-Intent": "conversation-panel",
                    "Copilot-Integration-Id": "vscode-chat",
                },
                timeout=httpx.Timeout(timeout, connect=10.0),
            )
            logger.info("Copilot provider initialized with GitHub token")
        else:
            logger.warning("No GitHub token found — copilot provider will use fallback")

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        if not self._client:
            return await self._do_fallback(
                messages, model=model, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
            )

        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [m.to_api_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            resp = await self._client.post(COPILOT_ENDPOINT, json=body)
            resp.raise_for_status()
            data = resp.json()
            return self._parse_response(data)
        except Exception as e:
            logger.warning("Copilot API failed: %s — falling back", e)
            return await self._do_fallback(
                messages, model=model, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
            )

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        if not self._client:
            if self.fallback:
                async for chunk in self.fallback.chat_stream(
                    messages, model=model, tools=tools,
                    temperature=temperature, max_tokens=max_tokens,
                ):
                    yield chunk
                return
            raise RuntimeError("No GitHub token and no fallback provider")

        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [m.to_api_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        try:
            async with self._client.stream("POST", COPILOT_ENDPOINT, json=body) as resp:
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
        except Exception as e:
            logger.warning("Copilot stream failed: %s — falling back", e)
            if self.fallback:
                async for chunk in self.fallback.chat_stream(
                    messages, model=model, temperature=temperature, max_tokens=max_tokens,
                ):
                    yield chunk
            else:
                raise

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
        if self.fallback:
            await self.fallback.close()

    async def _do_fallback(
        self,
        messages: list[Message],
        **kwargs: Any,
    ) -> ChatResponse:
        if not self.fallback:
            raise RuntimeError("Copilot unavailable and no fallback provider configured")
        return await self.fallback.chat(messages, **kwargs)

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> ChatResponse:
        choice = data["choices"][0]
        msg_data = choice["message"]

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
                tool_calls.append(ToolCall(id=tc["id"], name=fn["name"], arguments=args))

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
