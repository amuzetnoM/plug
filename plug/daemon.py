"""
PLUG Daemon Runner
===================

Runs the bot as a daemon with PID file, logging, and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from plug.config import LOG_FILE, PID_FILE, load_config


logger = logging.getLogger(__name__)


def setup_logging(*, debug: bool = False, log_file: bool = True) -> None:
    """Configure structured logging to stdout and optionally to file."""
    level = logging.DEBUG if debug else logging.INFO

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]

    if log_file:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )

    # Quiet noisy libraries
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def write_pidfile() -> None:
    """Write the current PID to the PID file."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    logger.debug("PID %d written to %s", os.getpid(), PID_FILE)


def remove_pidfile() -> None:
    """Remove the PID file."""
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def read_pidfile() -> int | None:
    """Read the PID from the PID file. Returns None if not running."""
    if not PID_FILE.exists():
        return None

    try:
        pid = int(PID_FILE.read_text().strip())
        # Check if process is actually alive
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        # PID file is stale
        remove_pidfile()
        return None


def is_running() -> bool:
    """Check if a PLUG instance is already running."""
    return read_pidfile() is not None


async def run_bot(*, debug: bool = False) -> None:
    """Run the PLUG bot (blocking)."""
    from plug.bot.client import PlugBot

    setup_logging(debug=debug)
    config = load_config()

    if is_running():
        pid = read_pidfile()
        logger.error("PLUG is already running (PID %d). Stop it first.", pid)
        sys.exit(1)

    write_pidfile()

    try:
        bot = PlugBot(config)
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Interrupted.")
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        raise
    finally:
        remove_pidfile()
