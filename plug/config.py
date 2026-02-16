"""
PLUG Configuration
==================

Pydantic configuration for PLUG.
Reads from ~/.plug/config.json.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".plug"
CONFIG_FILE = CONFIG_DIR / "config.json"
DB_FILE = CONFIG_DIR / "sessions.db"
PID_FILE = CONFIG_DIR / "plug.pid"
LOG_FILE = CONFIG_DIR / "plug.log"


class ProxyConfig(BaseModel):
    base_url: str = "http://localhost:3000/v1"
    api_key: str = "n/a"
    timeout: float = 120.0


class ModelsConfig(BaseModel):
    primary: str = "claude-opus-4.6"
    fallbacks: list[str] = Field(default_factory=lambda: ["gpt-5.2", "gemini-3-pro"])
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    temperature: float = 0.7
    max_tokens: int = 4096


class DiscordConfig(BaseModel):
    token: str = ""
    guild_ids: list[str] = Field(default_factory=lambda: ["1326925607589642352"])
    bot_user_id: str = "1459121107641569291"
    require_mention: bool = True
    dm_policy: str = "allowlist"
    dm_allowlist: list[str] = Field(default_factory=lambda: ["193011943382974466"])
    status_message: str = "\U0001f52e PLUG Online"
    max_message_length: int = 2000
    reconnect_delay: float = 5.0
    max_reconnect_delay: float = 300.0


class AgentConfig(BaseModel):
    workspace: str = str(Path.home() / "workspace")
    system_prompt_files: list[str] = Field(default_factory=lambda: [
        "SOUL.md", "AGENTS.md", "USER.md", "IDENTITY.md", "TOOLS.md",
    ])
    exec_timeout: int = 30
    exec_max_output: int = 50_000


class CompactionConfig(BaseModel):
    enabled: bool = True
    max_context_tokens: int = 100_000
    target_tokens: int = 60_000
    summary_model: str = ""


class DaemonConfig(BaseModel):
    auto_restart: bool = True
    max_restarts: int = 5
    restart_window: int = 300


class PlugConfig(BaseModel):
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.model_dump(), indent=2, default=str))
        logger.info("Config saved to %s", CONFIG_FILE)

    @classmethod
    def load(cls) -> PlugConfig:
        if CONFIG_FILE.exists():
            try:
                return cls(**json.loads(CONFIG_FILE.read_text()))
            except Exception as e:
                logger.warning("Config parse error, using defaults: %s", e)
        return cls()


def load_config() -> PlugConfig:
    return PlugConfig.load()


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR
