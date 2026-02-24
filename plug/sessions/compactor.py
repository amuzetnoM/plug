"""
Session Compactor
==================

When a conversation exceeds the token budget, compact older messages
into a summary, keeping recent context intact.

Uses the same LLM provider to generate summaries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import tiktoken

from plug.models.base import Message

if TYPE_CHECKING:
    from plug.models.base import ChatProvider
    from plug.sessions.store import SessionStore

logger = logging.getLogger(__name__)

# Use cl100k_base (GPT-4/Claude approximation)
try:
    _encoder = tiktoken.get_encoding("cl100k_base")
except Exception:
    _encoder = None


def count_tokens(text: str) -> int:
    """Count tokens in a string using tiktoken."""
    if _encoder is None:
        # Rough fallback: 1 token ≈ 4 chars
        return len(text) // 4
    return len(_encoder.encode(text, disallowed_special=()))


def count_message_tokens(message: Message) -> int:
    """Estimate token count for a message (content + overhead)."""
    tokens = 4  # message overhead (role, separators)
    if message.content:
        tokens += count_tokens(message.content)
    if message.tool_calls:
        for tc in message.tool_calls:
            tokens += count_tokens(tc.name)
            import json
            tokens += count_tokens(json.dumps(tc.arguments))
    if message.name:
        tokens += count_tokens(message.name)
    return tokens


COMPACTION_PROMPT = """You are summarizing a conversation segment for context continuity.

Summarize the following conversation messages into a concise but comprehensive summary.
Preserve:
- Key decisions and their reasoning
- Important facts, names, IDs, file paths, and technical details
- Action items and outcomes
- The current state of any ongoing work

Keep the summary factual and dense. No filler. Format as a structured summary.

Messages to summarize:
---
{messages}
---

Write the summary:"""


def _safe_split_point(messages: list[Message], split: int) -> int:
    """Adjust split point to never orphan tool_call/tool_result pairs.

    The kept (active) messages are messages[split:]. The compacted are messages[:split].
    An orphan occurs when:
    - A tool result is in kept but its assistant tool_call is in compacted
    - An assistant with tool_calls is in compacted but some tool results are in kept

    Fix: walk split backward until messages[split] is NOT a tool result.
    This ensures any tool results stay together with their assistant.
    """
    n = len(messages)
    if split <= 0 or split >= n:
        return split

    # Walk backward past any tool results at the boundary
    while split > 0 and messages[split].role == "tool":
        split -= 1

    # Now messages[split] should be an assistant (with tool_calls) or user/system.
    # If it's an assistant with tool_calls, its tool results are at split+1, split+2...
    # and they'll all be in the kept set. The assistant itself is also in kept. ✓
    # But we need the assistant itself to be KEPT (not compacted), so split should
    # point AT the assistant, not after it.
    # Since messages[:split] is compacted and messages[split:] is kept,
    # messages[split] IS in the kept set. ✓

    if split <= 0:
        return 0

    return split


class Compactor:
    """Token-aware conversation compactor.

    When context exceeds max_context_tokens, summarizes the oldest
    messages down to target_tokens, keeping the most recent messages
    intact.
    """

    def __init__(
        self,
        store: SessionStore,
        provider: ChatProvider,
        *,
        max_context_tokens: int = 100_000,
        target_tokens: int = 60_000,
        summary_model: str = "",
    ):
        self.store = store
        self.provider = provider
        self.max_context_tokens = max_context_tokens
        self.target_tokens = target_tokens
        self.summary_model = summary_model or None

    async def check_and_compact(self, channel_id: str) -> bool:
        """Check if compaction is needed and do it.

        Returns True if compaction was performed.
        """
        current_tokens = await self.store.get_token_count(channel_id)

        if current_tokens <= self.max_context_tokens:
            return False

        logger.info(
            "Compaction needed for %s: %d tokens > %d max",
            channel_id,
            current_tokens,
            self.max_context_tokens,
        )

        messages = await self.store.get_messages(channel_id)
        if len(messages) < 4:
            # Too few messages to compact
            return False

        # Find the split point: compact everything except the most recent
        # messages that fit within target_tokens
        keep_tokens = 0
        keep_from = len(messages)

        for i in range(len(messages) - 1, -1, -1):
            msg_tokens = count_message_tokens(messages[i])
            if keep_tokens + msg_tokens > self.target_tokens:
                break
            keep_tokens += msg_tokens
            keep_from = i

        # Must keep at least the last 2 messages
        keep_from = min(keep_from, len(messages) - 2)
        if keep_from <= 0:
            return False

        # INTEGRITY: Never split tool_call/tool_result pairs.
        # If keep_from lands on a tool result, walk backward to include
        # the assistant message that issued the tool_call.
        # If keep_from lands right after an assistant with tool_calls,
        # walk forward to include all its tool results.
        keep_from = _safe_split_point(messages, keep_from)

        # Messages to summarize
        to_compact = messages[:keep_from]

        # Generate summary
        summary = await self._summarize(to_compact)
        if not summary:
            return False

        # Get the database ID of the last message to compact
        message_ids = await self.store.get_message_ids(channel_id)
        if keep_from > len(message_ids):
            return False

        compact_up_to = message_ids[keep_from - 1]

        # Mark old messages as compacted
        marked = await self.store.mark_compacted(channel_id, compact_up_to)

        # Insert the summary as a system message
        summary_msg = Message(
            role="system",
            content=f"[Previous conversation summary]\n{summary}",
        )
        summary_tokens = count_message_tokens(summary_msg)
        await self.store.add_message(channel_id, summary_msg, token_count=summary_tokens)

        logger.info(
            "Compacted %s: %d messages summarized, %d kept, summary=%d tokens",
            channel_id,
            marked,
            len(messages) - keep_from,
            summary_tokens,
        )
        return True

    async def _summarize(self, messages: list[Message]) -> str | None:
        """Use the LLM to summarize a list of messages."""
        # Format messages for the summary prompt
        formatted = []
        for msg in messages:
            prefix = msg.role.upper()
            if msg.name:
                prefix += f" ({msg.name})"

            if msg.content:
                formatted.append(f"[{prefix}]: {msg.content}")
            elif msg.tool_calls:
                calls = ", ".join(tc.name for tc in msg.tool_calls)
                formatted.append(f"[{prefix}]: [Called tools: {calls}]")

        text = "\n".join(formatted)

        # Truncate if the text to summarize is itself too long
        max_summary_input = 80_000
        if len(text) > max_summary_input:
            text = text[:max_summary_input] + "\n[...truncated...]"

        prompt = COMPACTION_PROMPT.format(messages=text)

        try:
            response = await self.provider.chat(
                [Message(role="user", content=prompt)],
                model=self.summary_model,
                temperature=0.3,
                max_tokens=2048,
            )
            return response.message.content
        except Exception as e:
            logger.error("Compaction summary failed: %s", e)
            return None
