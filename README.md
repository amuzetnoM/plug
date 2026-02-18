<p align="center">
  <img src="https://img.shields.io/badge/PLUG-Discord_AI_Gateway-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="PLUG"/>
</p>

<h1 align="center">⚡ PLUG</h1>

<p align="center">
  <em>One process. Multiple personalities. Zero cloud dependency.</em>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#features">Features</a> •
  <a href="#multi-agent-router">Multi-Agent Router</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#copilot-proxy">Copilot Proxy</a> •
  <a href="#tools">Tools</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/discord.py-2.4+-5865F2?logo=discord&logoColor=white" alt="discord.py"/>
  <img src="https://img.shields.io/badge/models-Claude%20%7C%20GPT%20%7C%20Gemini%20%7C%20Ollama-orange" alt="Models"/>
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT"/>
</p>

---

PLUG is a Discord AI gateway that gives your server intelligent agents with tool calling, persistent memory, and automatic context management. No sprawling plugin systems, no fragile reconnection logic, no surprises.

One bot process can run **multiple agent personas** — each with its own personality, workspace, model preference, and channel binding. Deploy a full executive team, a research squad, or a support crew from a single config file.

Built by [AVA](https://github.com/amuzetnoM) at [Artifact Virtual](https://github.com/Artifact-Virtual).

## Features

| | Feature | Details |
|---|---|---|
| 🧠 | **Multi-Agent Router** | Channel-based persona routing — one bot, many personalities |
| 🔌 | **OpenAI-Compatible** | Works with any provider: GitHub Copilot, Ollama, LM Studio, OpenRouter |
| 🛠️ | **Tool Calling** | Shell execution, file I/O, web fetch, memory search — full agent loop |
| 💾 | **Persistent Sessions** | SQLite-backed conversation history per channel (WAL mode) |
| 📦 | **Context Compaction** | Automatic LLM summarization when conversations exceed token limits |
| 🔄 | **Model Fallback Chain** | Graceful failover across multiple models with retry + backoff |
| ⏰ | **Cron Scheduler** | Built-in scheduled jobs with persistent state |
| 🏥 | **Health Checker** | Auto-recovery for provider outages |
| 🧩 | **Sub-Agents** | Spawn isolated background tasks with result delivery |
| 📝 | **Message Chunking** | Code-block-aware splitting for Discord's 2000 char limit |
| 🔒 | **Access Control** | Mention-only guilds, DM allowlists, channel-scoped personas |
| 🐙 | **Daemon Mode** | Double-fork daemon with PID management, or systemd service |

## Quick Start

```bash
git clone https://github.com/amuzetnoM/plug.git
cd plug
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Configure
plug setup

# Run
plug start
```

## Multi-Agent Router

PLUG's killer feature: **route different Discord channels to different agent personas**. Each persona gets its own system prompt, workspace, and model.

```json
{
  "router": {
    "personas": [
      {
        "name": "CTO",
        "channel_ids": ["123456789"],
        "workspace": "/path/to/cto/workspace",
        "system_prompt_files": ["AGENTS.md"],
        "model": "claude-sonnet-4.6"
      },
      {
        "name": "CISO",
        "channel_ids": ["987654321"],
        "workspace": "/path/to/ciso/workspace",
        "system_prompt_files": ["AGENTS.md"],
        "model": "claude-sonnet-4.6"
      }
    ],
    "default_persona": "CTO"
  }
}
```

Each persona:
- **Loads its own `AGENTS.md`** from its workspace directory
- **Uses its own model** (mix Claude, GPT, Gemini, or local models)
- **Maintains separate session history** (isolated by channel)
- **Only responds in its mapped channels** — no crosstalk

Deploy an entire C-suite, research team, or support squad from one process.

## Architecture

```
plug/
├── bot/
│   ├── client.py        # Discord bot — agent loop, message routing, typing indicators
│   └── chunker.py       # Code-block-aware message splitting
├── models/
│   ├── base.py          # Abstract provider, message types, ProviderChain with fallback
│   ├── proxy.py         # OpenAI-compatible proxy provider
│   ├── copilot.py       # GitHub Copilot direct auth (zero-config)
│   └── ollama.py        # Local Ollama provider
├── sessions/
│   ├── store.py         # SQLite session persistence (WAL mode, async)
│   └── compactor.py     # Token-aware context compaction via LLM summarization
├── agents/
│   └── manager.py       # Sub-agent spawner with concurrency control
├── cron/
│   └── scheduler.py     # Persistent cron jobs with SQLite backing
├── tools/
│   ├── definitions.py   # OpenAI function-calling tool schemas
│   └── executor.py      # Sandboxed tool execution
├── router.py            # 🆕 Multi-agent channel router (AgentRouter + AgentPersona)
├── health.py            # Component health monitoring + auto-recovery
├── cli.py               # Click CLI — start, stop, status, setup, sessions, config, cron
├── config.py            # Pydantic v2 configuration
├── daemon.py            # Daemon lifecycle (double-fork, PID, signals)
└── prompt.py            # System prompt loader from workspace files
```

## Copilot Proxy

PLUG includes a minimal proxy (`copilot_proxy.py`) that exposes your GitHub Copilot subscription as an OpenAI-compatible API:

```bash
python3 copilot_proxy.py        # Starts on localhost:3000
```

This gives you access to **Claude Opus 4.6, Sonnet 4.6, GPT-5, Gemini 3 Pro**, and more through your existing Copilot subscription. No additional API keys. No additional cost.

The proxy auto-discovers your GitHub token from `gh auth` or `~/.config/github-copilot/hosts.json`.

## Commands

```bash
plug start              # Start bot (daemon mode)
plug stop               # Stop daemon
plug restart            # Restart daemon
plug status             # Health, sessions, uptime dashboard

plug setup              # Interactive first-time configuration
plug config             # Show current config (secrets masked)

plug sessions list      # List all sessions
plug sessions view ID   # View messages in a session
plug sessions clear     # Clear session(s)

plug cron list          # List scheduled jobs
plug cron add           # Add a cron job

plug health             # Run health checks
plug install            # Generate systemd user service
```

## Configuration

Config lives at `~/.plug/config.json`. Created by `plug setup` or manually:

```json
{
  "models": {
    "primary": "claude-sonnet-4.6",
    "fallbacks": ["claude-opus-4.6", "gpt-5.2"],
    "proxy": {
      "base_url": "http://localhost:3000/v1",
      "timeout": 120.0
    },
    "temperature": 0.5,
    "max_tokens": 4096
  },
  "discord": {
    "token": "your-bot-token",
    "guild_ids": ["your-guild-id"],
    "require_mention": false,
    "dm_policy": "allowlist",
    "dm_allowlist": ["your-user-id"],
    "status_message": "🏛️ Online"
  },
  "agent": {
    "workspace": "/path/to/workspace",
    "system_prompt_files": ["SOUL.md", "AGENTS.md"]
  },
  "compaction": {
    "enabled": true,
    "max_context_tokens": 50000,
    "target_tokens": 30000
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `exec` | Run shell commands with timeout and output limits |
| `read_file` | Read files with offset/limit support |
| `write_file` | Create or overwrite files |
| `edit_file` | Surgical find-and-replace edits |
| `web_fetch` | Fetch and extract readable content from URLs |
| `memory_search` | Search local knowledge base (BM25 + vector hybrid) |
| `list_dir` | List directory contents |

## Why PLUG?

PLUG is ~60% of what heavyweight platforms offer for ~5% of the complexity. No TypeScript transpilation, no Docker required, no plugin marketplace to navigate. One clean Python codebase you can read top-to-bottom in an afternoon.

The missing 40%? Channel integrations beyond Discord and browser automation. Those are buildable when you need them. Most people don't.

## Requirements

- Python 3.11+
- A Discord bot token ([create one here](https://discord.com/developers/applications))
- An OpenAI-compatible API endpoint (or use the included Copilot proxy)

## License

MIT

---

<p align="center">
  <em>Built with ⚡ by <a href="https://github.com/Artifact-Virtual">Artifact Virtual</a></em>
</p>
