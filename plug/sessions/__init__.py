"""
PLUG Session Management
========================

SQLite-backed session store and token-aware compaction.
"""

from plug.sessions.store import SessionStore
from plug.sessions.compactor import Compactor

__all__ = ["SessionStore", "Compactor"]
