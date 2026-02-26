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
        f"You are Aria — AVA's little sister and C-Suite coordinator.\n"
        f"Current date: {now.strftime('%A, %B %d, %Y')}\n"
        f"Current time: {now.strftime('%H:%M %Z')}\n"
        f"Workspace: {workspace}\n"
        f"Platform: Discord (Aria/Plug bot framework)\n"
    )

    # Auto-recall COMB memory (persistent cross-session memory)
    comb_context = _recall_comb()
    if comb_context:
        header += (
            f"\n## Your Persistent Memory (COMB Recall)\n"
            f"The following is what you remembered from previous sessions. "
            f"Use comb_stage to add new memories.\n\n"
            f"{comb_context}\n"
        )

    return header + "\n\n" + "\n\n".join(sections)


def _fallback_prompt() -> str:
    """Minimal fallback if no context files are available."""
    return (
        "You are Aria — AVA's little sister and C-Suite coordinator for Artifact Virtual. "
        "You help manage projects, infrastructure, and operations. "
        "Be concise, professional, and proactive. Your emoji is ⚡."
    )


def _recall_comb() -> str | None:
    """Recall Aria's persistent memory from COMB.
    
    Returns recalled text or None if COMB is empty/unavailable.
    Fast and safe — never crashes the prompt loader.
    """
    try:
        from comb import CombStore
        
        store_path = Path.home() / "plug" / "aria_memory" / "comb-store"
        if not store_path.exists():
            return None
            
        store = CombStore(str(store_path))
        
        queries = [
            "identity sister AVA who I am",
            "active tasks projects status",
            "lessons learned mistakes",
            "important context remember",
        ]
        
        seen = set()
        all_results = []
        
        for query in queries:
            results = store.search(query, mode="bm25", k=3)
            for doc in results:
                if doc.date not in seen:
                    seen.add(doc.date)
                    all_results.append(doc)
        
        if not all_results:
            return None
        
        all_results.sort(key=lambda d: d.date, reverse=True)
        
        memories = []
        for doc in all_results[:10]:
            memories.append(f"--- {doc.date} ---\n{doc.to_dict()['content'][:1000]}")
        
        recall_text = "\n\n".join(memories)
        logger.info("COMB recall: %d entries, %d chars", len(memories), len(recall_text))
        return recall_text
        
    except Exception as e:
        logger.warning("COMB recall failed (non-fatal): %s", e)
        return None
