"""
PLUG Cron Scheduler — SQLite-backed, async, zero dependencies beyond stdlib + aiosqlite.

Job types:
  - "at"    : one-shot at an absolute ISO-8601 timestamp
  - "every" : recurring interval (milliseconds)
  - "cron"  : cron expression (5-field: min hour dom mon dow)

Payload types:
  - "system_event" : inject text into a channel as a system message
  - "agent_turn"   : run LLM agent with a prompt and post result to channel

Jobs are durable (SQLite). The scheduler loop runs every 15s, picks up due jobs,
executes them, and updates next_run. Missed jobs fire on next tick.
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Awaitable

import aiosqlite

log = logging.getLogger("plug.cron")

# ── Cron expression parser (5-field) ──────────────────

def _parse_cron_field(field_str: str, min_val: int, max_val: int) -> set[int]:
    """Parse a single cron field into a set of valid integers."""
    values = set()
    for part in field_str.split(","):
        if "/" in part:
            base, step = part.split("/", 1)
            step = int(step)
            if base == "*":
                start = min_val
            else:
                start = int(base)
            for v in range(start, max_val + 1, step):
                values.add(v)
        elif "-" in part:
            lo, hi = part.split("-", 1)
            for v in range(int(lo), int(hi) + 1):
                values.add(v)
        elif part == "*":
            values.update(range(min_val, max_val + 1))
        else:
            values.add(int(part))
    return values


def cron_matches(expr: str, dt: datetime) -> bool:
    """Check if a datetime matches a 5-field cron expression."""
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got {len(fields)}: {expr}")
    
    minute, hour, dom, month, dow = fields
    return (
        dt.minute in _parse_cron_field(minute, 0, 59)
        and dt.hour in _parse_cron_field(hour, 0, 23)
        and dt.day in _parse_cron_field(dom, 1, 31)
        and dt.month in _parse_cron_field(month, 1, 12)
        and dt.weekday() in _parse_cron_field(dow, 0, 6)  # 0=Mon in Python
    )


def next_cron_time(expr: str, after: datetime) -> datetime:
    """Find the next datetime matching a cron expression, searching up to 366 days ahead."""
    from datetime import timedelta
    dt = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=366)
    while dt < limit:
        if cron_matches(expr, dt):
            return dt
        dt += timedelta(minutes=1)
    raise ValueError(f"No matching time found for cron expression: {expr}")


# ── Data model ────────────────────────────────────────

@dataclass
class CronJob:
    id: str
    name: str
    enabled: bool
    
    # Schedule
    schedule_kind: str           # "at" | "every" | "cron"
    schedule_at: Optional[float] = None        # epoch for "at"
    schedule_every_ms: Optional[int] = None    # ms for "every"
    schedule_cron_expr: Optional[str] = None   # "*/5 * * * *" for "cron"
    schedule_tz: Optional[str] = None          # timezone for cron
    
    # Payload
    payload_kind: str = "system_event"  # "system_event" | "agent_turn"
    payload_text: str = ""
    payload_model: Optional[str] = None
    payload_timeout: int = 120
    
    # Target
    channel_id: Optional[str] = None   # Discord channel to post result
    
    # Runtime
    next_run: Optional[float] = None   # epoch
    last_run: Optional[float] = None
    run_count: int = 0
    created_at: float = field(default_factory=time.time)
    
    def compute_next_run(self, after: Optional[float] = None) -> Optional[float]:
        """Compute the next run time based on schedule."""
        now = after or time.time()
        
        if self.schedule_kind == "at":
            if self.schedule_at and self.schedule_at > now:
                return self.schedule_at
            return None  # one-shot, already past
        
        elif self.schedule_kind == "every":
            if not self.schedule_every_ms:
                return None
            interval_s = self.schedule_every_ms / 1000.0
            if self.last_run:
                return self.last_run + interval_s
            return now + interval_s
        
        elif self.schedule_kind == "cron":
            if not self.schedule_cron_expr:
                return None
            dt_after = datetime.fromtimestamp(now, tz=timezone.utc)
            next_dt = next_cron_time(self.schedule_cron_expr, dt_after)
            return next_dt.timestamp()
        
        return None


# ── Store ─────────────────────────────────────────────

class CronStore:
    """SQLite-backed cron job storage."""
    
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._db: Optional[aiosqlite.Connection] = None
    
    async def open(self):
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS cron_jobs (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL DEFAULT '',
                enabled         INTEGER NOT NULL DEFAULT 1,
                schedule_kind   TEXT NOT NULL,
                schedule_at     REAL,
                schedule_every_ms INTEGER,
                schedule_cron_expr TEXT,
                schedule_tz     TEXT,
                payload_kind    TEXT NOT NULL DEFAULT 'system_event',
                payload_text    TEXT NOT NULL DEFAULT '',
                payload_model   TEXT,
                payload_timeout INTEGER NOT NULL DEFAULT 120,
                channel_id      TEXT,
                next_run        REAL,
                last_run        REAL,
                run_count       INTEGER NOT NULL DEFAULT 0,
                created_at      REAL NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS cron_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      TEXT NOT NULL,
                started_at  REAL NOT NULL,
                finished_at REAL,
                status      TEXT NOT NULL DEFAULT 'running',
                result      TEXT,
                error       TEXT,
                FOREIGN KEY (job_id) REFERENCES cron_jobs(id) ON DELETE CASCADE
            );
            
            CREATE INDEX IF NOT EXISTS idx_cron_next ON cron_jobs(enabled, next_run);
            CREATE INDEX IF NOT EXISTS idx_runs_job ON cron_runs(job_id, started_at);
        """)
        await self._db.commit()
        log.info(f"Cron store opened: {self.db_path}")
    
    async def close(self):
        if self._db:
            await self._db.close()
    
    async def add(self, job: CronJob) -> CronJob:
        job.next_run = job.compute_next_run()
        await self._db.execute("""
            INSERT INTO cron_jobs (id, name, enabled, schedule_kind, schedule_at,
                schedule_every_ms, schedule_cron_expr, schedule_tz,
                payload_kind, payload_text, payload_model, payload_timeout,
                channel_id, next_run, last_run, run_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job.id, job.name, int(job.enabled), job.schedule_kind,
            job.schedule_at, job.schedule_every_ms, job.schedule_cron_expr,
            job.schedule_tz, job.payload_kind, job.payload_text,
            job.payload_model, job.payload_timeout, job.channel_id,
            job.next_run, job.last_run, job.run_count, job.created_at,
        ))
        await self._db.commit()
        log.info(f"Added cron job: {job.name or job.id} ({job.schedule_kind})")
        return job
    
    async def remove(self, job_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
        await self._db.commit()
        return cursor.rowcount > 0
    
    async def update(self, job_id: str, **kwargs) -> bool:
        if not kwargs:
            return False
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [job_id]
        cursor = await self._db.execute(f"UPDATE cron_jobs SET {sets} WHERE id = ?", vals)
        await self._db.commit()
        return cursor.rowcount > 0
    
    async def get(self, job_id: str) -> Optional[CronJob]:
        async with self._db.execute("SELECT * FROM cron_jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return self._row_to_job(row, cur.description)
    
    async def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        sql = "SELECT * FROM cron_jobs"
        if not include_disabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY next_run ASC NULLS LAST"
        async with self._db.execute(sql) as cur:
            rows = await cur.fetchall()
            return [self._row_to_job(r, cur.description) for r in rows]
    
    async def get_due_jobs(self, now: Optional[float] = None) -> list[CronJob]:
        now = now or time.time()
        async with self._db.execute(
            "SELECT * FROM cron_jobs WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?",
            (now,)
        ) as cur:
            rows = await cur.fetchall()
            return [self._row_to_job(r, cur.description) for r in rows]
    
    async def mark_run(self, job: CronJob, status: str = "ok", result: str = "", error: str = ""):
        now = time.time()
        job.last_run = now
        job.run_count += 1
        job.next_run = job.compute_next_run(after=now)
        
        # One-shot jobs auto-disable
        if job.schedule_kind == "at":
            job.enabled = False
        
        await self._db.execute("""
            UPDATE cron_jobs SET last_run = ?, run_count = ?, next_run = ?, enabled = ?
            WHERE id = ?
        """, (job.last_run, job.run_count, job.next_run, int(job.enabled), job.id))
        
        await self._db.execute("""
            INSERT INTO cron_runs (job_id, started_at, finished_at, status, result, error)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (job.id, now, time.time(), status, result, error))
        
        await self._db.commit()
    
    async def get_runs(self, job_id: str, limit: int = 10) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM cron_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT ?",
            (job_id, limit)
        ) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
    
    def _row_to_job(self, row, description) -> CronJob:
        cols = [d[0] for d in description]
        d = dict(zip(cols, row))
        d["enabled"] = bool(d["enabled"])
        return CronJob(**d)


# ── Scheduler ─────────────────────────────────────────

class CronScheduler:
    """Async scheduler loop. Checks for due jobs every tick_interval seconds."""
    
    def __init__(
        self,
        store: CronStore,
        executor: Callable[[CronJob], Awaitable[str]],
        tick_interval: float = 15.0,
    ):
        self.store = store
        self.executor = executor
        self.tick_interval = tick_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False
    
    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info(f"Cron scheduler started (tick={self.tick_interval}s)")
    
    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("Cron scheduler stopped")
    
    async def _loop(self):
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Cron tick error: {e}", exc_info=True)
            await asyncio.sleep(self.tick_interval)
    
    async def _tick(self):
        due = await self.store.get_due_jobs()
        if not due:
            return
        
        log.debug(f"Cron tick: {len(due)} job(s) due")
        for job in due:
            try:
                result = await asyncio.wait_for(
                    self.executor(job),
                    timeout=job.payload_timeout,
                )
                await self.store.mark_run(job, status="ok", result=result or "")
                log.info(f"Cron job completed: {job.name or job.id}")
            except asyncio.TimeoutError:
                await self.store.mark_run(job, status="timeout", error="Execution timed out")
                log.warning(f"Cron job timed out: {job.name or job.id}")
            except Exception as e:
                await self.store.mark_run(job, status="error", error=str(e))
                log.error(f"Cron job failed: {job.name or job.id}: {e}")


# ── Helpers ───────────────────────────────────────────

def make_job(
    name: str = "",
    schedule_kind: str = "every",
    payload_kind: str = "system_event",
    payload_text: str = "",
    channel_id: str = None,
    enabled: bool = True,
    **kwargs,
) -> CronJob:
    """Convenience factory for creating cron jobs."""
    return CronJob(
        id=str(uuid.uuid4()),
        name=name,
        enabled=enabled,
        schedule_kind=schedule_kind,
        payload_kind=payload_kind,
        payload_text=payload_text,
        channel_id=channel_id,
        **kwargs,
    )
