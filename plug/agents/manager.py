"""
PLUG Sub-Agent Manager — spawn isolated LLM sessions for background tasks.

Each sub-agent gets:
  - Its own session (isolated conversation history)
  - A task prompt
  - Optional model override
  - A timeout
  - Result delivery back to the parent channel

Sub-agents run as async tasks. They share the same tool executor
but have independent conversation state. Results are posted to
the originating Discord channel when complete.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Awaitable

log = logging.getLogger("plug.agents")


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class SubAgent:
    id: str
    task: str
    channel_id: str                     # where to deliver result
    model: Optional[str] = None         # model override
    timeout: float = 300.0              # seconds
    status: AgentStatus = AgentStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    label: Optional[str] = None         # human-readable label
    
    @property
    def elapsed(self) -> Optional[float]:
        if self.started_at:
            end = self.finished_at or time.time()
            return end - self.started_at
        return None
    
    @property
    def summary(self) -> str:
        status = self.status.value
        elapsed = f" ({self.elapsed:.1f}s)" if self.elapsed else ""
        name = self.label or self.id[:8]
        return f"[{name}] {status}{elapsed}"


class AgentManager:
    """
    Manages sub-agent lifecycle. Spawns isolated LLM runs,
    tracks them, and delivers results.
    
    The `run_fn` callback receives (task, model, timeout) and returns
    the agent's text response. It should run a full agent loop
    (system prompt + task + tool calls) in isolation.
    
    The `deliver_fn` callback receives (channel_id, message) and posts
    the result to Discord.
    """
    
    def __init__(
        self,
        run_fn: Callable[[str, Optional[str], float], Awaitable[str]],
        deliver_fn: Callable[[str, str], Awaitable[None]],
        max_concurrent: int = 5,
    ):
        self.run_fn = run_fn
        self.deliver_fn = deliver_fn
        self.max_concurrent = max_concurrent
        self._agents: dict[str, SubAgent] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
    
    async def spawn(
        self,
        task: str,
        channel_id: str,
        model: Optional[str] = None,
        timeout: float = 300.0,
        label: Optional[str] = None,
    ) -> SubAgent:
        """Spawn a new sub-agent. Returns immediately with the agent handle."""
        agent = SubAgent(
            id=str(uuid.uuid4()),
            task=task,
            channel_id=channel_id,
            model=model,
            timeout=timeout,
            label=label,
        )
        self._agents[agent.id] = agent
        self._tasks[agent.id] = asyncio.create_task(self._run(agent))
        log.info(f"Sub-agent spawned: {agent.summary} — {task[:80]}")
        return agent
    
    async def _run(self, agent: SubAgent):
        """Execute the sub-agent with concurrency control."""
        async with self._semaphore:
            agent.status = AgentStatus.RUNNING
            agent.started_at = time.time()
            
            try:
                result = await asyncio.wait_for(
                    self.run_fn(agent.task, agent.model, agent.timeout),
                    timeout=agent.timeout,
                )
                agent.status = AgentStatus.COMPLETED
                agent.result = result
                log.info(f"Sub-agent completed: {agent.summary}")
                
                # Deliver result to channel
                header = f"**Sub-agent** `{agent.label or agent.id[:8]}` **completed** ({agent.elapsed:.1f}s):\n\n"
                await self.deliver_fn(agent.channel_id, header + (result or "(no output)"))
                
            except asyncio.TimeoutError:
                agent.status = AgentStatus.TIMEOUT
                agent.error = f"Timed out after {agent.timeout}s"
                log.warning(f"Sub-agent timed out: {agent.summary}")
                await self.deliver_fn(
                    agent.channel_id,
                    f"**Sub-agent** `{agent.label or agent.id[:8]}` **timed out** after {agent.timeout:.0f}s."
                )
            except asyncio.CancelledError:
                agent.status = AgentStatus.CANCELLED
                log.info(f"Sub-agent cancelled: {agent.summary}")
            except Exception as e:
                agent.status = AgentStatus.FAILED
                agent.error = str(e)
                log.error(f"Sub-agent failed: {agent.summary}: {e}")
                await self.deliver_fn(
                    agent.channel_id,
                    f"**Sub-agent** `{agent.label or agent.id[:8]}` **failed**: {e}"
                )
            finally:
                agent.finished_at = time.time()
    
    def get(self, agent_id: str) -> Optional[SubAgent]:
        return self._agents.get(agent_id)
    
    def list_agents(self, channel_id: Optional[str] = None) -> list[SubAgent]:
        agents = list(self._agents.values())
        if channel_id:
            agents = [a for a in agents if a.channel_id == channel_id]
        return sorted(agents, key=lambda a: a.created_at, reverse=True)
    
    def active_count(self) -> int:
        return sum(1 for a in self._agents.values() if a.status == AgentStatus.RUNNING)
    
    async def cancel(self, agent_id: str) -> bool:
        task = self._tasks.get(agent_id)
        if task and not task.done():
            task.cancel()
            return True
        return False
    
    async def cancel_all(self):
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
    
    def cleanup(self, max_age: float = 3600.0):
        """Remove finished agents older than max_age seconds."""
        now = time.time()
        to_remove = [
            aid for aid, a in self._agents.items()
            if a.finished_at and (now - a.finished_at) > max_age
        ]
        for aid in to_remove:
            del self._agents[aid]
            self._tasks.pop(aid, None)
        if to_remove:
            log.debug(f"Cleaned up {len(to_remove)} finished sub-agents")
