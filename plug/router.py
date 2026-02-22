"""
PLUG Multi-Agent Router — route Discord channels to different agent personas.

Each channel maps to an agent config with its own:
  - System prompt (AGENTS.md)
  - Workspace directory
  - Model preference
  - Session isolation

One bot process, multiple personalities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("plug.router")


@dataclass
class AgentPersona:
    """A named agent persona bound to specific Discord channels."""
    name: str
    channel_ids: list[str]
    workspace: str
    system_prompt_files: list[str] = field(default_factory=lambda: ["AGENTS.md"])
    model: Optional[str] = None  # None = use default
    base_url: Optional[str] = None  # None = use default proxy
    temperature: float = 0.5
    max_tokens: int = 4096
    require_mention: Optional[bool] = None  # None = use global config default

    @property
    def system_prompt(self) -> str:
        """Load system prompt from workspace files."""
        parts = []
        ws = Path(self.workspace)
        for fname in self.system_prompt_files:
            fpath = ws / fname
            if fpath.exists():
                parts.append(fpath.read_text().strip())
            else:
                log.warning(f"Persona {self.name}: prompt file not found: {fpath}")
        return "\n\n---\n\n".join(parts) if parts else f"You are {self.name}."


class AgentRouter:
    """Routes channel IDs to agent personas."""

    def __init__(self, personas: list[AgentPersona], default: Optional[AgentPersona] = None):
        self._channel_map: dict[str, AgentPersona] = {}
        self._personas: dict[str, AgentPersona] = {}
        self.default = default

        for p in personas:
            self._personas[p.name] = p
            for ch_id in p.channel_ids:
                self._channel_map[ch_id] = p
                log.info(f"Router: #{ch_id} → {p.name}")

    def route(self, channel_id: str) -> Optional[AgentPersona]:
        """Get the persona for a given channel. Returns default if no match."""
        return self._channel_map.get(channel_id, self.default)

    def get_persona(self, name: str) -> Optional[AgentPersona]:
        return self._personas.get(name)

    def list_personas(self) -> list[AgentPersona]:
        return list(self._personas.values())

    @classmethod
    def from_config(cls, config: dict) -> AgentRouter:
        """
        Build router from config dict:
        {
            "personas": [
                {
                    "name": "CTO",
                    "channel_ids": ["1473617109685637192"],
                    "workspace": "/path/to/cto/workspace",
                    "system_prompt_files": ["AGENTS.md"],
                    "model": "claude-sonnet-4"
                },
                ...
            ],
            "default_persona": "AVA"
        }
        """
        personas = [AgentPersona(**p) for p in config.get("personas", [])]
        default_name = config.get("default_persona")
        default = None
        if default_name:
            default = next((p for p in personas if p.name == default_name), None)
        return cls(personas, default)
