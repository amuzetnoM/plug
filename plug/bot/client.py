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
from plug.models.ollama import OllamaChatProvider
from plug.prompt import load_system_prompt
from plug.sessions.compactor import Compactor, count_message_tokens
from plug.sessions.store import SessionStore
from plug.tools.definitions import TOOL_DEFINITIONS
from plug.tools.executor import ToolExecutor
from plug.cron.scheduler import CronStore, CronScheduler, CronJob
from plug.agents.manager import AgentManager
from plug.health import HealthChecker
from plug.router import AgentRouter, AgentPersona

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 40  # Safety limit for tool-call loops

# ── Report-back: notify AVA when exec tasks complete ─────────────────────
AVA_REPORT_WEBHOOK = "https://discord.com/api/webhooks/1473633265410773106/IbfQs7cfG7RpWQJudwL2vbfOPctt-Myr03FEf6BmHAdPwl7sGb47i90shamzC_QyyMw0"
AVA_BOT_MENTION = "<@1459121107641569291>"  # @AVA#5921
EXEC_CHANNELS = {
    "1473617109685637192": "CTO",
    "1473617113301258354": "COO",
    "1473617116426014872": "CFO",
    "1473617119986843741": "CISO",
}


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
        self.health: HealthChecker | None = None
        self.router: AgentRouter | None = None
        self._persona_chains: dict[str, ProviderChain] = {}  # persona_name → chain

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

        # Model provider (primary: proxy)
        proxy_cfg = self.config.models.proxy
        self.provider = ProxyChatProvider(
            base_url=proxy_cfg.base_url,
            api_key=proxy_cfg.api_key,
            timeout=proxy_cfg.timeout,
            default_model=self.config.models.primary,
        )

        # Ollama fallback provider
        self.ollama_provider: OllamaChatProvider | None = None
        fallback_providers = []
        if self.config.ollama.enabled:
            self.ollama_provider = OllamaChatProvider(
                base_url=self.config.ollama.base_url,
                default_model=self.config.ollama.models[0] if self.config.ollama.models else "qwen2.5-coder:7b",
                timeout=self.config.ollama.timeout,
            )
            if await self.ollama_provider.is_available():
                available_models = await self.ollama_provider.list_models()
                # Use configured models that are actually pulled
                ollama_models = [
                    m for m in self.config.ollama.models
                    if m in available_models
                ] or available_models[:3]
                fallback_providers.append((self.ollama_provider, ollama_models))
                logger.info("Ollama fallback ready: %s", ollama_models)
            else:
                logger.warning("Ollama not available at %s", self.config.ollama.base_url)

        # Fallback chain: proxy models → ollama models
        all_models = [self.config.models.primary] + self.config.models.fallbacks
        self.chain = ProviderChain(
            self.provider, all_models,
            fallback_providers=fallback_providers,
            max_retries=2,
            retry_delay=1.0,
        )

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

        # Multi-agent router (channel → persona mapping)
        router_cfg = getattr(self.config, '_raw', {}).get('router')
        if not router_cfg and hasattr(self.config, 'model_extra'):
            router_cfg = self.config.model_extra.get('router')
        if router_cfg:
            self.router = AgentRouter.from_config(router_cfg)
            logger.info("Router loaded: %d personas", len(self.router.list_personas()))

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

        # Health checker
        self.health = HealthChecker(
            proxy_url=self.config.models.proxy.base_url.replace("/v1", ""),
            check_interval=30.0,
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
        if self.health:
            self.health.stop()
        if self.ollama_provider:
            await self.ollama_provider.close()
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

        # Start health checker
        if self.health:
            self.health.start()
            logger.info("Health checker started")

    async def on_message(self, message: DiscordMessage) -> None:
        """Handle incoming Discord messages."""
        # Ignore own messages
        if message.author == self.client.user:
            return

        # Ignore bots (except webhook dispatches in routed channels)
        if message.author.bot:
            webhook_id = getattr(message, 'webhook_id', None)
            channel_id = str(message.channel.id)
            is_webhook_dispatch = (
                webhook_id is not None
                and self.router
                and self.router.route(channel_id) is not None
            )
            if not is_webhook_dispatch:
                logger.debug("Ignoring bot message in %s (webhook_id=%s)", channel_id, webhook_id)
                return
            # Skip report-back webhooks (CTO/COO/CFO/CISO Report) to prevent loops
            author_name = getattr(message.author, 'name', '') or ''
            if author_name.endswith(' Report'):
                logger.debug("Ignoring report-back webhook from %s in %s", author_name, channel_id)
                return
            logger.info("Accepting webhook dispatch in %s (webhook_id=%s)", channel_id, webhook_id)

        # C-Suite channels: ignore @mentions (those are for AVA/OpenClaw),
        # only respond to plain messages
        if self.router and self.client.user:
            channel_id = str(message.channel.id)
            persona = self.router.route(channel_id)
            if persona:
                mentioned = any(
                    user.id == self.client.user.id
                    for user in message.mentions
                )
                if mentioned:
                    return  # @mention = talking to AVA, not C-suite

        # Check if we should respond
        if not self._should_respond(message):
            logger.debug("_should_respond returned False for %s", str(message.channel.id))
            return

        # Prevent concurrent processing of same channel
        channel_id = str(message.channel.id)
        if channel_id in self._processing:
            logger.info("Already processing %s, skipping", channel_id)
            return

        logger.info("Processing message in %s from %s", channel_id, message.author)
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

        # If router is active, only respond in mapped channels
        channel_id = str(message.channel.id)
        if self.router:
            persona = self.router.route(channel_id)
            if not persona:
                return False  # Not a C-suite channel, ignore

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

        # Report back to AVA when exec task completes
        await self._report_back_to_ava(channel_id, final_text)

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
                    model_override = self._get_model_for_channel(channel_id)
                    chain = self._get_chain_for_channel(channel_id)
                    response = await chain.chat(
                        conversation,
                        model=model_override,
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

            # If no tool calls, check if the model is expressing intent to continue
            if not response.has_tool_calls:
                content = (assistant_msg.content or "").lower()
                continuation_signals = [
                    "let me ", "now i'll ", "now i will", "i'll create",
                    "i'll write", "i'll run", "let me create", "let me write",
                    "let me run", "simultaneously", "i need to", "i'll start",
                    "now let me", "i have enough", "i have all the",
                ]
                is_continuation = any(sig in content for sig in continuation_signals)
                if is_continuation and round_num < MAX_TOOL_ROUNDS - 2:
                    logger.info("Round %d: text-only but continuation intent detected, injecting nudge", round_num)
                    # Nudge the model to actually use tools
                    nudge = Message(role="user", content="Use your tools now. Do not describe what you'll do — call the tool directly.")
                    conversation.append(nudge)
                    await self.store.add_message(channel_id, nudge, token_count=20)
                    continue
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

    async def _report_back_to_ava(self, channel_id: str, result_text: str | None):
        """Send completion notification to AVA's channel via webhook.

        Only fires for exec channels (CTO/COO/CFO/CISO), not AVA's own channel.
        Mentions @AVA#5921 so OpenClaw gateway picks it up.
        """
        if channel_id not in EXEC_CHANNELS:
            return

        exec_name = EXEC_CHANNELS[channel_id]
        summary = (result_text or "[No response text]")[:1500]

        payload = {
            "content": f"{AVA_BOT_MENTION} **{exec_name} Task Report**\n\n{summary}",
            "username": f"{exec_name} Report",
        }

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(AVA_REPORT_WEBHOOK, json=payload) as resp:
                    if resp.status < 300:
                        logger.info("Report-back to AVA sent for %s (status %d)", exec_name, resp.status)
                    else:
                        logger.warning("Report-back to AVA failed for %s: HTTP %d", exec_name, resp.status)
        except Exception as e:
            logger.error("Report-back to AVA failed: %s", e)

    async def _build_conversation(self, channel_id: str) -> list[Message]:
        """Build the full conversation for the API call.

        Prepends system prompt (from router persona if available), then all active messages.
        """
        messages: list[Message] = []

        # System prompt — use persona-specific if router matches
        system_prompt = self._system_prompt
        persona = self.router.route(channel_id) if self.router else None
        if persona:
            persona_prompt = persona.system_prompt
            if persona_prompt:
                system_prompt = persona_prompt
                logger.debug("Using persona %s for channel %s", persona.name, channel_id)

        if system_prompt:
            messages.append(Message(role="system", content=system_prompt))

        # Session history
        history = await self.store.get_messages(channel_id)
        messages.extend(history)

        return messages

    def _get_model_for_channel(self, channel_id: str) -> str | None:
        """Get the model override for a channel via router persona."""
        if self.router:
            persona = self.router.route(channel_id)
            if persona and persona.model:
                return persona.model
        return None

    def _get_chain_for_channel(self, channel_id: str) -> ProviderChain:
        """Get a per-persona ProviderChain if persona has a custom base_url."""
        if self.router:
            persona = self.router.route(channel_id)
            if persona and persona.base_url:
                if persona.name not in self._persona_chains:
                    from plug.models.proxy import ProxyChatProvider
                    proxy = ProxyChatProvider(
                        base_url=persona.base_url,
                        api_key="n/a",
                        timeout=120.0,
                        default_model=persona.model or self.config.models.primary,
                    )
                    models = [persona.model or self.config.models.primary] + self.config.models.fallbacks
                    self._persona_chains[persona.name] = ProviderChain(proxy, models)
                    logger.info("Created dedicated proxy chain for %s at %s", persona.name, persona.base_url)
                return self._persona_chains[persona.name]
        return self.chain

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
            # Run a full agent turn with persona routing and deliver the result
            result = await self._run_subagent(
                job.payload_text, job.payload_model, job.payload_timeout,
                channel_id=job.channel_id,
            )
            if job.channel_id:
                await self._deliver_to_channel(job.channel_id, f"⏰ **Cron** `{job.name}`:\n\n{result}")
            return result

        return f"Unknown payload kind: {job.payload_kind}"

    async def _run_subagent(self, task: str, model: str | None, timeout: float,
                            channel_id: str | None = None) -> str:
        """Run an isolated agent turn (for sub-agents and cron agent_turn payloads)."""
        # Build a minimal conversation with system prompt + task
        conversation = []

        # Use persona-specific system prompt if channel is routed
        system_prompt = self._system_prompt
        if channel_id and self.router:
            persona = self.router.route(channel_id)
            if persona:
                persona_prompt = persona.system_prompt
                if persona_prompt:
                    system_prompt = persona_prompt
                    if not model and persona.model:
                        model = persona.model

        if system_prompt:
            conversation.append(Message(role="system", content=system_prompt))
        conversation.append(Message(role="user", content=task))

        # Run agent loop (reuse the same logic but without Discord message context)
        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                model_list = [model] if model else [self.config.models.primary] + self.config.models.fallbacks
                # Build fallback providers for sub-agents too
                fb = []
                if self.ollama_provider:
                    ollama_models = self.config.ollama.models or ["qwen2.5-coder:7b"]
                    fb.append((self.ollama_provider, ollama_models))
                chain = ProviderChain(
                    self.provider, model_list,
                    fallback_providers=fb,
                    max_retries=2,
                    retry_delay=1.0,
                )
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
