"""
System Prompt Loader
=====================

Loads workspace context files (SOUL.md, AGENTS.md, USER.md, etc.)
and assembles the system prompt at session start.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def load_system_prompt(
    workspace: str,
    prompt_files: list[str],
) -> str:
    """Load and assemble the system prompt from workspace context files.

    Args:
        workspace: Path to the workspace root.
        prompt_files: List of filenames relative to workspace root.

    Returns:
        Combined system prompt string.
    """
    workspace_path = Path(workspace)
    sections: list[str] = []

    for filename in prompt_files:
        filepath = workspace_path / filename
        if not filepath.exists():
            logger.warning("System prompt file not found: %s", filepath)
            continue

        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
            if content.strip():
                sections.append(
                    f"<context file=\"{filename}\">\n{content.strip()}\n</context>"
                )
                logger.debug("Loaded system prompt file: %s (%d chars)", filename, len(content))
        except Exception as e:
            logger.warning("Failed to read %s: %s", filepath, e)

    if not sections:
        logger.warning("No system prompt files loaded!")
        return _fallback_prompt()

    # Add dynamic context
    now = datetime.now()
    header = (
        f"You are a helpful AI assistant.\n"
        f"Current date: {now.strftime('%A, %B %d, %Y')}\n"
        f"Current time: {now.strftime('%H:%M %Z')}\n"
        f"Workspace: {workspace}\n"
        f"Platform: Discord (PLUG bot framework)\n"
    )

    return header + "\n\n" + "\n\n".join(sections)


def _fallback_prompt() -> str:
    """Minimal fallback if no context files are available."""
    return (
        "You are a helpful AI assistant. "
        "You help manage projects, infrastructure, and operations. "
        "Be concise, professional, and proactive."
    )
