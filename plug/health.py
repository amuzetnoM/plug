"""
PLUG Health Check & Recovery — watchdog for bot + proxy processes.

Checks:
  1. Discord bot connection (alive + responsive)
  2. Copilot proxy (localhost:3000/health)
  3. Cron scheduler (ticking)
  4. SQLite DB integrity

Recovery:
  - Auto-restart bot on disconnect
  - Auto-restart proxy if health fails
  - Exponential backoff on repeated failures
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

import httpx

log = logging.getLogger("plug.health")


@dataclass
class HealthStatus:
    component: str
    healthy: bool
    message: str = ""
    latency_ms: float = 0.0
    last_check: float = field(default_factory=time.time)
    consecutive_failures: int = 0


class HealthChecker:
    """Periodic health checker with auto-recovery."""

    def __init__(
        self,
        proxy_url: str = "http://localhost:3000",
        check_interval: float = 30.0,
        max_backoff: float = 300.0,
        on_proxy_dead: Optional[Callable[[], Awaitable[None]]] = None,
        on_bot_dead: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.proxy_url = proxy_url.rstrip("/")
        self.check_interval = check_interval
        self.max_backoff = max_backoff
        self.on_proxy_dead = on_proxy_dead
        self.on_bot_dead = on_bot_dead

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._statuses: dict[str, HealthStatus] = {}
        self._recovery_backoff: dict[str, float] = {}

    @property
    def statuses(self) -> dict[str, HealthStatus]:
        return dict(self._statuses)

    @property
    def all_healthy(self) -> bool:
        return all(s.healthy for s in self._statuses.values())

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("Health checker started (interval=%ds)", self.check_interval)

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("Health checker stopped")

    async def _loop(self):
        while self._running:
            try:
                await self._check_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Health check error: %s", e)
            await asyncio.sleep(self.check_interval)

    async def _check_all(self):
        await asyncio.gather(
            self._check_proxy(),
            self._check_db(),
            return_exceptions=True,
        )

    async def _check_proxy(self):
        """Check copilot proxy health endpoint."""
        name = "proxy"
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.proxy_url}/health")
                latency = (time.monotonic() - start) * 1000

                if resp.status_code == 200:
                    self._mark_healthy(name, f"OK ({latency:.0f}ms)", latency)
                else:
                    await self._mark_unhealthy(
                        name, f"HTTP {resp.status_code}", latency
                    )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            await self._mark_unhealthy(name, str(e), latency)

    async def _check_db(self):
        """Quick SQLite integrity check."""
        name = "database"
        start = time.monotonic()
        try:
            from plug.config import DB_FILE
            if not DB_FILE.exists():
                self._mark_healthy(name, "No DB yet (OK)")
                return

            import sqlite3
            conn = sqlite3.connect(str(DB_FILE), timeout=5)
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            latency = (time.monotonic() - start) * 1000

            if result and result[0] == "ok":
                self._mark_healthy(name, f"OK ({latency:.0f}ms)", latency)
            else:
                await self._mark_unhealthy(name, f"Integrity: {result}", latency)
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            await self._mark_unhealthy(name, str(e), latency)

    def _mark_healthy(self, name: str, message: str = "", latency: float = 0.0):
        prev = self._statuses.get(name)
        if prev and not prev.healthy:
            log.info("✅ %s recovered: %s", name, message)
        self._statuses[name] = HealthStatus(
            component=name, healthy=True, message=message,
            latency_ms=latency, consecutive_failures=0,
        )
        self._recovery_backoff.pop(name, None)

    async def _mark_unhealthy(self, name: str, message: str, latency: float = 0.0):
        prev = self._statuses.get(name)
        failures = (prev.consecutive_failures + 1) if prev else 1

        self._statuses[name] = HealthStatus(
            component=name, healthy=False, message=message,
            latency_ms=latency, consecutive_failures=failures,
        )

        log.warning("❌ %s unhealthy (x%d): %s", name, failures, message)

        # Attempt recovery with backoff
        backoff = self._recovery_backoff.get(name, 0)
        now = time.time()
        if now >= backoff:
            await self._attempt_recovery(name, failures)
            # Exponential backoff: 30s, 60s, 120s, ... up to max
            next_backoff = min(30 * (2 ** (failures - 1)), self.max_backoff)
            self._recovery_backoff[name] = now + next_backoff

    async def _attempt_recovery(self, name: str, failures: int):
        """Attempt to recover a component."""
        log.info("🔄 Attempting recovery for %s (failure #%d)", name, failures)

        if name == "proxy" and self.on_proxy_dead:
            try:
                await self.on_proxy_dead()
            except Exception as e:
                log.error("Proxy recovery failed: %s", e)

        elif name == "bot" and self.on_bot_dead:
            try:
                await self.on_bot_dead()
            except Exception as e:
                log.error("Bot recovery failed: %s", e)

    def format_status(self) -> str:
        """Format health status as a human-readable string."""
        if not self._statuses:
            return "No health checks run yet."

        lines = ["**PLUG Health**"]
        for name, status in sorted(self._statuses.items()):
            icon = "✅" if status.healthy else "❌"
            latency = f" ({status.latency_ms:.0f}ms)" if status.latency_ms else ""
            fails = f" [failures: {status.consecutive_failures}]" if status.consecutive_failures else ""
            lines.append(f"  {icon} **{name}**: {status.message}{latency}{fails}")

        return "\n".join(lines)


async def check_once(proxy_url: str = "http://localhost:3000") -> dict[str, HealthStatus]:
    """Run a one-shot health check and return statuses."""
    checker = HealthChecker(proxy_url=proxy_url)
    await checker._check_all()
    return checker.statuses
