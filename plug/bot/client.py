"""
PLUG Discord Bot Client
========================

discord.py bot with:
- Mention-only in guilds, allowlist for DMs
- Agent loop: LLM → tool calls → LLM → response (multi-turn)
- Typing indicator during generation
- Reply threading
- Message chunking
- Graceful reconnection with exponential backoff
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Any

import discord
from discord import Intents, Message as DiscordMessage

from plug.bot.chunker import chunk_message
from plug.config import PlugConfig, DB_FILE
from plug.models.base import ChatProvider, Message, ProviderChain
from plug.models.proxy import ProxyChatProvider
from plug.prompt import load_system_prompt
from plug.sessions.compactor import Compactor, count_message_tokens
from plug.sessions.store import SessionStore
from plug.tools.definitions import TOOL_DEFINITIONS
from plug.tools.executor import ToolExecutor
from plug.cron.scheduler import CronStore, CronScheduler, CronJob
from plug.agents.manager import AgentManager

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 15  # Safety limit for tool-call loops


class PlugBot:
    """The main PLUG Discord bot."""

    def __init__(self, config: PlugConfig):
        self.config = config
        self._system_prompt: str | None = None

        # Discord client
        intents = Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True

        self.client = discord.Client(intents=intents)

        # Components (initialized in start())
        self.store: SessionStore | None = None
        self.provider: ChatProvider | None = None
        self.chain: ProviderChain | None = None
        self.executor: ToolExecutor | None = None
        self.compactor: Compactor | None = None
        self.cron_store: CronStore | None = None
        self.cron_scheduler: CronScheduler | None = None
        self.agent_manager: AgentManager | None = None

        # State
        self._processing: set[str] = set()  # channel IDs currently being processed

        # Wire up events
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize components and start the bot."""
        # Session store
        self.store = SessionStore(DB_FILE)
        await self.store.open()

        # Model provider
        proxy_cfg = self.config.models.proxy
        self.provider = ProxyChatProvider(
            base_url=proxy_cfg.base_url,
            api_key=proxy_cfg.api_key,
            timeout=proxy_cfg.timeout,
            default_model=self.config.models.primary,
        )

        # Fallback chain
        all_models = [self.config.models.primary] + self.config.models.fallbacks
        self.chain = ProviderChain(self.provider, all_models)

        # Tool executor
        self.executor = ToolExecutor(workspace=self.config.agent.workspace)

        # Compactor
        self.compactor = Compactor(
            store=self.store,
            provider=self.provider,
            max_context_tokens=self.config.compaction.max_context_tokens,
            target_tokens=self.config.compaction.target_tokens,
            summary_model=self.config.compaction.summary_model or None,
        )

        # System prompt
        self._system_prompt = load_system_prompt(
            self.config.agent.workspace,
            self.config.agent.system_prompt_files,
        )

        # Cron scheduler
        cron_db = DB_FILE.parent / "cron.db"
        self.cron_store = CronStore(cron_db)
        await self.cron_store.open()
        self.cron_scheduler = CronScheduler(
            store=self.cron_store,
            executor=self._execute_cron_job,
        )

        # Sub-agent manager
        self.agent_manager = AgentManager(
            run_fn=self._run_subagent,
            deliver_fn=self._deliver_to_channel,
            max_concurrent=self.config.agent.max_subagents if hasattr(self.config.agent, 'max_subagents') else 5,
        )

        # Handle graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Start Discord client
        token = self.config.discord.token
        if not token:
            raise RuntimeError("Discord bot token not configured. Run `plug setup`.")

        logger.info("Starting PLUG bot...")
        await self.client.start(token)

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down PLUG bot...")

        if self.cron_scheduler:
            self.cron_scheduler.stop()
        if self.agent_manager:
            await self.agent_manager.cancel_all()
        if self.cron_store:
            await self.cron_store.close()
        if self.executor:
            await self.executor.close()
        if self.provider:
            await self.provider.close()
        if self.store:
            await self.store.close()

        if not self.client.is_closed():
            await self.client.close()

        logger.info("PLUG bot stopped.")

    # ── Discord Events ───────────────────────────────────────────────────

    async def on_ready(self) -> None:
        """Called when the bot connects to Discord."""
        logger.info("PLUG connected as %s (ID: %s)", self.client.user, self.client.user.id)

        # Set status
        status_text = self.config.discord.status_message
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name=status_text,
        )
        await self.client.change_presence(activity=activity)
        logger.info("Status set: %s", status_text)

        # Start cron scheduler
        if self.cron_scheduler:
            self.cron_scheduler.start()
            logger.info("Cron scheduler started")

    async def on_message(self, message: DiscordMessage) -> None:
        """Handle incoming Discord messages."""
        # Ignore own messages
        if message.author == self.client.user:
            return

        # Ignore bots
        if message.author.bot:
            return

        # Check if we should respond
        if not self._should_respond(message):
            return

        # Prevent concurrent processing of same channel
        channel_id = str(message.channel.id)
        if channel_id in self._processing:
            logger.debug("Already processing %s, skipping", channel_id)
            return

        self._processing.add(channel_id)
        try:
            await self._handle_message(message)
        except Exception as e:
            logger.error("Error handling message in %s: %s", channel_id, e, exc_info=True)
            try:
                await message.reply(f"Something went wrong: {type(e).__name__}", mention_author=False)
            except Exception:
                pass
        finally:
            self._processing.discard(channel_id)

    # ── Message routing ──────────────────────────────────────────────────

    def _should_respond(self, message: DiscordMessage) -> bool:
        """Determine if the bot should respond to this message."""
        is_dm = isinstance(message.channel, discord.DMChannel)

        if is_dm:
            return self._check_dm_policy(message)
        else:
            return self._check_guild_policy(message)

    def _check_dm_policy(self, message: DiscordMessage) -> bool:
        """Check DM policy."""
        policy = self.config.discord.dm_policy
        if policy == "open":
            return True

        # allowlist
        user_id = str(message.author.id)
        return user_id in self.config.discord.dm_allowlist

    def _check_guild_policy(self, message: DiscordMessage) -> bool:
        """Check guild/mention policy."""
        # Check guild whitelist
        if self.config.discord.guild_ids:
            guild_id = str(message.guild.id) if message.guild else ""
            if guild_id not in self.config.discord.guild_ids:
                return False

        # Check mention requirement
        if self.config.discord.require_mention:
            if not self.client.user:
                return False
            # Check if bot is mentioned
            mentioned = any(
                user.id == self.client.user.id
                for user in message.mentions
            )
            if not mentioned:
                return False

        return True

    # ── Agent loop ───────────────────────────────────────────────────────

    async def _handle_message(self, message: DiscordMessage) -> None:
        """Main agent loop: process a message through the LLM."""
        channel_id = str(message.channel.id)

        # Extract the user's text (strip the mention)
        user_text = self._extract_text(message)
        if not user_text.strip():
            return

        # Store the user message
        user_msg = Message(role="user", content=user_text)
        user_tokens = count_message_tokens(user_msg)
        await self.store.add_message(channel_id, user_msg, token_count=user_tokens)

        # Check compaction before building context
        if self.config.compaction.enabled:
            await self.compactor.check_and_compact(channel_id)

        # Build conversation for the API
        conversation = await self._build_conversation(channel_id)

        # Agent loop: call LLM, execute tools, repeat until text response
        final_text = await self._run_agent_loop(channel_id, conversation, message)

        # Send the response
        if final_text:
            await self._send_response(message, final_text)

    async def _run_agent_loop(
        self,
        channel_id: str,
        conversation: list[Message],
        discord_message: DiscordMessage,
    ) -> str | None:
        """Run the agent loop until the LLM returns a text response.

        Handles multi-turn tool calling:
        1. Call LLM with conversation + tools
        2. If LLM returns tool_calls, execute them
        3. Add tool results to conversation
        4. Repeat from 1
        5. When LLM returns text (no tool_calls), return it
        """
        for round_num in range(MAX_TOOL_ROUNDS):
            # Show typing indicator
            async with discord_message.channel.typing():
                try:
                    response = await self.chain.chat(
                        conversation,
                        tools=TOOL_DEFINITIONS,
                        temperature=self.config.models.temperature,
                        max_tokens=self.config.models.max_tokens,
                    )
                except Exception as e:
                    logger.error("LLM call failed (round %d): %s", round_num, e)
                    return f"LLM error: {e}"

            assistant_msg = response.message
            assistant_tokens = count_message_tokens(assistant_msg)
            await self.store.add_message(channel_id, assistant_msg, token_count=assistant_tokens)
            conversation.append(assistant_msg)

            # If no tool calls, we're done
            if not response.has_tool_calls:
                return assistant_msg.content or ""

            # Execute tool calls
            logger.info(
                "Round %d: %d tool calls",
                round_num,
                len(assistant_msg.tool_calls),
            )

            for tc in assistant_msg.tool_calls:
                logger.info("Executing tool: %s(%s)", tc.name, _truncate_args(tc.arguments))

                result = await self.executor.execute(tc.name, tc.arguments)

                # Store tool result
                tool_msg = Message(
                    role="tool",
                    content=result,
                    tool_call_id=tc.id,
                    name=tc.name,
                )
                tool_tokens = count_message_tokens(tool_msg)
                await self.store.add_message(channel_id, tool_msg, token_count=tool_tokens)
                conversation.append(tool_msg)

        # Safety: too many rounds
        logger.warning("Agent loop hit max rounds (%d) for %s", MAX_TOOL_ROUNDS, channel_id)
        return "[Agent reached maximum tool-call rounds. Stopping.]"

    async def _build_conversation(self, channel_id: str) -> list[Message]:
        """Build the full conversation for the API call.

        Prepends system prompt, then all active messages.
        """
        messages: list[Message] = []

        # System prompt
        if self._system_prompt:
            messages.append(Message(role="system", content=self._system_prompt))

        # Session history
        history = await self.store.get_messages(channel_id)
        messages.extend(history)

        return messages

    # ── Response handling ────────────────────────────────────────────────

    async def _send_response(
        self,
        original: DiscordMessage,
        text: str,
    ) -> None:
        """Send a response, chunking if necessary. Reply to the original message."""
        max_len = self.config.discord.max_message_length
        chunks = chunk_message(text, max_length=max_len)

        for i, chunk in enumerate(chunks):
            try:
                if i == 0:
                    # Reply to the original message
                    await original.reply(chunk, mention_author=False)
                else:
                    # Follow-up chunks go to the channel
                    await original.channel.send(chunk)

                # Small delay between chunks to avoid rate limiting
                if i < len(chunks) - 1:
                    await asyncio.sleep(0.5)

            except discord.HTTPException as e:
                logger.error("Failed to send chunk %d: %s", i, e)
                break

    # ── Helpers ──────────────────────────────────────────────────────────

    def _extract_text(self, message: DiscordMessage) -> str:
        """Extract the user's text, removing the bot mention if present."""
        text = message.content

        if self.client.user:
            # Remove <@BOT_ID> and <@!BOT_ID> mentions
            bot_id = self.client.user.id
            text = text.replace(f"<@{bot_id}>", "").replace(f"<@!{bot_id}>", "")

        return text.strip()

    # ── Cron & Sub-agent callbacks ───────────────────────────────────────

    async def _execute_cron_job(self, job: CronJob) -> str:
        """Execute a cron job payload."""
        if job.payload_kind == "system_event":
            # Inject text as a system message into the channel
            if job.channel_id:
                await self._deliver_to_channel(job.channel_id, f"⏰ **Cron** `{job.name}`: {job.payload_text}")
            return job.payload_text

        elif job.payload_kind == "agent_turn":
            # Run a full agent turn and deliver the result
            result = await self._run_subagent(job.payload_text, job.payload_model, job.payload_timeout)
            if job.channel_id:
                await self._deliver_to_channel(job.channel_id, f"⏰ **Cron** `{job.name}`:\n\n{result}")
            return result

        return f"Unknown payload kind: {job.payload_kind}"

    async def _run_subagent(self, task: str, model: str | None, timeout: float) -> str:
        """Run an isolated agent turn (for sub-agents and cron agent_turn payloads)."""
        # Build a minimal conversation with system prompt + task
        conversation = []
        if self._system_prompt:
            conversation.append(Message(role="system", content=self._system_prompt))
        conversation.append(Message(role="user", content=task))

        # Run agent loop (reuse the same logic but without Discord message context)
        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                model_list = [model] if model else [self.config.models.primary] + self.config.models.fallbacks
                chain = ProviderChain(self.provider, model_list)
                response = await chain.chat(
                    conversation,
                    tools=TOOL_DEFINITIONS,
                    temperature=self.config.models.temperature,
                    max_tokens=self.config.models.max_tokens,
                )
            except Exception as e:
                return f"LLM error: {e}"

            assistant_msg = response.message
            conversation.append(assistant_msg)

            if not response.has_tool_calls:
                return assistant_msg.content or "(no output)"

            for tc in assistant_msg.tool_calls:
                result = await self.executor.execute(tc.name, tc.arguments)
                conversation.append(Message(
                    role="tool", content=result,
                    tool_call_id=tc.id, name=tc.name,
                ))

        return "[Sub-agent reached maximum tool-call rounds]"

    async def _deliver_to_channel(self, channel_id: str, text: str) -> None:
        """Send a message to a Discord channel by ID."""
        try:
            channel = self.client.get_channel(int(channel_id))
            if not channel:
                channel = await self.client.fetch_channel(int(channel_id))
            if channel and hasattr(channel, 'send'):
                chunks = chunk_message(text, max_length=self.config.discord.max_message_length)
                for chunk in chunks:
                    await channel.send(chunk)
                    await asyncio.sleep(0.3)
        except Exception as e:
            logger.error("Failed to deliver to channel %s: %s", channel_id, e)


def _truncate_args(args: dict[str, Any], max_len: int = 200) -> str:
    """Truncate tool arguments for logging."""
    s = json.dumps(args)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
