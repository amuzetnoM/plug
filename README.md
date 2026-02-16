# PLUG
> Discord AI Gateway

A simpler, more robust alternative to platforms like [OpenClaw](https://github.com/openclaw/openclaw). One Discord bot, one config file, one process. No sprawling plugin system, no fragile reconnection logic, no surprises.

PLUG gives your Discord server an AI assistant with tool calling, persistent memory, and automatic context management. No cloud dependencies, no vendor lock-in. Just a clean Python codebase you can actually read.

## Features

- **OpenAI-compatible** — Works with any provider: GitHub Copilot, Ollama, LM Studio, OpenRouter, or your own proxy
- **Tool calling** — Execute shell commands, read/write files, search the web, query local memory
- **Persistent sessions** — SQLite-backed conversation history per channel
- **Context compaction** — Automatic summarization when conversations exceed token limits
- **Model fallback chain** — Graceful failover across multiple models
- **Message chunking** — Code-block-aware splitting for Discord's 2000 char limit
- **Mention-only mode** — Respond only when @mentioned in servers, open in DMs
- **DM allowlist** — Control who can talk to the bot privately
- **Daemon mode** — Double-fork daemon with PID management, or run as systemd service

## Quick Start

```bash
git clone https://github.com/amuzetnoM/plug.git
cd plug
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Configure
plug setup

# Run
plug bot
```

## Architecture

```
plug/
├── bot/
│   ├── client.py      # Discord bot with agent loop (LLM → tools → response)
│   └── chunker.py     # Code-block-aware message splitting
├── models/
│   ├── base.py        # Abstract provider, message types, fallback chain
│   ├── proxy.py       # OpenAI-compatible proxy provider
│   └── copilot.py     # GitHub Copilot direct auth (optional)
├── sessions/
│   ├── store.py       # SQLite session persistence (WAL mode)
│   └── compactor.py   # Token-aware context compaction via LLM summarization
├── tools/
│   ├── definitions.py # OpenAI function-calling tool schemas
│   └── executor.py    # Sandboxed tool execution
├── cli.py             # Click CLI (bot, daemon, status, setup, sessions, config)
├── config.py          # Pydantic configuration
├── daemon.py          # Daemon lifecycle (double-fork, PID, signals)
└── prompt.py          # System prompt loader from workspace files
```

## Copilot Proxy

PLUG includes a minimal Python-based proxy (`copilot_proxy.py`) that exposes your GitHub Copilot subscription as an OpenAI-compatible API:

```bash
# First time: authenticate with GitHub
python3 copilot_proxy.py auth

# Start the proxy (runs on localhost:3000)
python3 copilot_proxy.py
```

This gives you access to Claude, GPT, Gemini, and other models through your existing Copilot subscription. No additional API keys needed.

## Commands

```
plug bot              # Run Discord bot (foreground)
plug daemon start     # Run as background daemon
plug daemon stop      # Stop daemon
plug daemon restart   # Restart daemon
plug status           # Show health, sessions, uptime
plug setup            # Interactive configuration
plug config           # Show current config (secrets masked)
plug sessions list    # List all sessions
plug sessions view    # View messages in a session
plug sessions clear   # Clear session(s)
plug install-service  # Generate systemd user service
```

## Configuration

Config lives at `~/.plug/config.json`. Created by `plug setup` or manually:

```json
{
  "models": {
    "primary": "claude-opus-4.6",
    "fallbacks": ["gpt-5.2", "gemini-3-pro"],
    "proxy": {
      "base_url": "http://localhost:3000/v1",
      "timeout": 120.0
    }
  },
  "discord": {
    "token": "your-bot-token",
    "guild_ids": ["your-guild-id"],
    "require_mention": true,
    "dm_policy": "allowlist",
    "dm_allowlist": ["your-user-id"]
  }
}
```

## Tools

The bot can use tools during conversations:

| Tool | Description |
|------|-------------|
| `exec` | Run shell commands with timeout and output limits |
| `read_file` | Read files with offset/limit support |
| `write_file` | Create or overwrite files |
| `edit_file` | Surgical find-and-replace edits |
| `web_fetch` | Fetch and extract readable content from URLs |
| `memory_search` | Search local knowledge base (BM25 + vector hybrid) |
| `list_dir` | List directory contents |

## Requirements

- Python 3.11+
- A Discord bot token
- An OpenAI-compatible API endpoint

## License

MIT
