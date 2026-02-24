"""
Session Store
==============

SQLite-backed conversation persistence.
Each Discord channel/DM gets its own session, keyed by channel_id.
Messages are stored individually for efficient compaction.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from plug.models.base import Message, ToolCall

logger = logging.getLogger(__name__)


class SessionStore:
    """Persistent session storage using SQLite."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Open the database and ensure tables exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        logger.info("Session store opened: %s", self.db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                channel_id  TEXT PRIMARY KEY,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS messages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id    TEXT NOT NULL,
                role          TEXT NOT NULL,
                content       TEXT,
                tool_calls    TEXT,
                tool_call_id  TEXT,
                name          TEXT,
                timestamp     REAL NOT NULL,
                token_count   INTEGER DEFAULT 0,
                compacted     INTEGER DEFAULT 0,
                FOREIGN KEY (channel_id) REFERENCES sessions(channel_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_channel
                ON messages(channel_id, id);

            CREATE INDEX IF NOT EXISTS idx_messages_compacted
                ON messages(channel_id, compacted);
        """)
        await self._db.commit()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Session store not opened. Call open() first.")
        return self._db

    # ── Session management ───────────────────────────────────────────────

    async def ensure_session(self, channel_id: str) -> None:
        """Create session if it doesn't exist."""
        now = time.time()
        await self.db.execute(
            """INSERT OR IGNORE INTO sessions (channel_id, created_at, updated_at)
               VALUES (?, ?, ?)""",
            (channel_id, now, now),
        )
        await self.db.commit()

    async def touch_session(self, channel_id: str) -> None:
        """Update the session's last-modified timestamp."""
        await self.db.execute(
            "UPDATE sessions SET updated_at = ? WHERE channel_id = ?",
            (time.time(), channel_id),
        )
        await self.db.commit()

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions with message counts."""
        cursor = await self.db.execute("""
            SELECT s.channel_id, s.created_at, s.updated_at,
                   COUNT(m.id) as message_count,
                   SUM(COALESCE(m.token_count, 0)) as total_tokens
            FROM sessions s
            LEFT JOIN messages m ON m.channel_id = s.channel_id
            GROUP BY s.channel_id
            ORDER BY s.updated_at DESC
        """)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def delete_session(self, channel_id: str) -> bool:
        """Delete a session and all its messages."""
        cursor = await self.db.execute(
            "DELETE FROM sessions WHERE channel_id = ?",
            (channel_id,),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def clear_messages(self, channel_id: str) -> int:
        """Delete all messages for a channel without deleting the session.

        Useful for starting a fresh conversation in the same channel.
        Returns the number of messages deleted.
        """
        cursor = await self.db.execute(
            "DELETE FROM messages WHERE channel_id = ?",
            (channel_id,),
        )
        await self.db.commit()
        return cursor.rowcount

    async def clear_all(self) -> int:
        """Delete all sessions. Returns count deleted."""
        cursor = await self.db.execute("DELETE FROM sessions")
        await self.db.commit()
        return cursor.rowcount

    # ── Message storage ──────────────────────────────────────────────────

    async def add_message(
        self,
        channel_id: str,
        message: Message,
        token_count: int = 0,
    ) -> int:
        """Store a message. Returns the message row ID."""
        await self.ensure_session(channel_id)

        tool_calls_json = None
        if message.tool_calls:
            tool_calls_json = json.dumps([
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in message.tool_calls
            ])

        cursor = await self.db.execute(
            """INSERT INTO messages
               (channel_id, role, content, tool_calls, tool_call_id, name, timestamp, token_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                channel_id,
                message.role,
                message.content,
                tool_calls_json,
                message.tool_call_id,
                message.name,
                time.time(),
                token_count,
            ),
        )
        await self.db.commit()
        await self.touch_session(channel_id)
        return cursor.lastrowid

    async def get_messages(
        self,
        channel_id: str,
        *,
        include_compacted: bool = False,
    ) -> list[Message]:
        """Get all messages for a channel, in order.

        By default, excludes compacted (summarized) original messages.
        """
        if include_compacted:
            where = "WHERE channel_id = ?"
            params = (channel_id,)
        else:
            where = "WHERE channel_id = ? AND compacted = 0"
            params = (channel_id,)

        cursor = await self.db.execute(
            f"""SELECT role, content, tool_calls, tool_call_id, name
                FROM messages
                {where}
                ORDER BY id ASC""",
            params,
        )
        rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    async def get_token_count(self, channel_id: str) -> int:
        """Get total token count for active (non-compacted) messages."""
        cursor = await self.db.execute(
            """SELECT COALESCE(SUM(token_count), 0)
               FROM messages
               WHERE channel_id = ? AND compacted = 0""",
            (channel_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def mark_compacted(
        self, channel_id: str, up_to_id: int
    ) -> int:
        """Mark messages as compacted (replaced by summary).

        Returns count of messages marked.
        """
        cursor = await self.db.execute(
            """UPDATE messages
               SET compacted = 1
               WHERE channel_id = ? AND id <= ? AND compacted = 0
                 AND role != 'system'""",
            (channel_id, up_to_id),
        )
        await self.db.commit()
        return cursor.rowcount

    async def get_message_ids(self, channel_id: str) -> list[int]:
        """Get the row IDs of active messages in a channel."""
        cursor = await self.db.execute(
            """SELECT id FROM messages
               WHERE channel_id = ? AND compacted = 0
               ORDER BY id ASC""",
            (channel_id,),
        )
        return [row[0] for row in await cursor.fetchall()]

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_message(row) -> Message:
        """Convert a database row to a Message."""
        tool_calls = None
        if row["tool_calls"]:
            raw = json.loads(row["tool_calls"])
            tool_calls = [
                ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                for tc in raw
            ]

        return Message(
            role=row["role"],
            content=row["content"],
            tool_calls=tool_calls,
            tool_call_id=row["tool_call_id"],
            name=row["name"],
        )

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *exc):
        await self.close()
