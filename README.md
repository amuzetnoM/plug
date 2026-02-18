# ⚡ Plug

<div align="center">

![Status](https://img.shields.io/badge/status-production-brightgreen)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![Discord](https://img.shields.io/badge/discord-bot-5865F2)
![License](https://img.shields.io/badge/license-MIT-green)

**Multi-agent Discord AI gateway with channel-routed personas.**

*One bot. Many minds. Zero conflicts.*

</div>

---

## What Is This?

Plug is a Discord bot that runs multiple AI personas through a single gateway. Each Discord channel maps to a different agent with its own identity, system prompt, model, and workspace.

**Production deployment:** Artifact Virtual's C-Suite — 5 AI executives coordinated through Discord.

```
#ava-command  →  AVA (Coordinator)     Claude Opus 4.6
#cto          →  CTO (Engineering)     Claude Sonnet 4.6
#coo          →  COO (Operations)      Claude Sonnet 4.6
#cfo          →  CFO (Finance)         Claude Sonnet 4.6
#ciso         →  CISO (Security)       Claude Sonnet 4.6
```

## Architecture

```
Discord Gateway
      │
      ▼
┌──────────────┐     ┌──────────────┐
│  Plug Client │────▶│ AgentRouter  │
│              │     │              │
│  on_message  │     │ channel_id → │
│  on_ready    │     │   persona    │
└──────┬───────┘     └──────┬───────┘
       │                    │
       ▼                    ▼
┌──────────────┐     ┌──────────────┐
│   Provider   │     │   Session    │
│    Chain     │     │    Store     │
│              │     │              │
│ Copilot Proxy│     │   SQLite     │
│ → Ollama     │     │ per-channel  │
└──────┬───────┘     └──────────────┘
       │
       ▼
┌──────────────┐     ┌──────────────┐
│    Tools     │     │     Cron     │
│              │     │  Scheduler   │
│  exec        │     │              │
│  read_file   │     │ Standing     │
│  write_file  │     │ Orders       │
│  web_search  │     │ agent_turn   │
└──────────────┘     └──────────────┘
```

## Key Features

### 🔀 AgentRouter — Multi-Persona Routing
One Discord bot token, multiple AI personalities. Each channel routes to a different agent persona with isolated:
- System prompts (from workspace `AGENTS.md`)
- Model selection (Opus for coordinators, Sonnet for workers)
- Session history (SQLite, per-channel)
- Tool access (shared executor)

### ⏰ Cron Scheduler — Autonomous Standing Orders
SQLite-backed cron system with `agent_turn` payloads. Executives run periodic health checks, status reports, and audits autonomously — no human trigger needed.

### 🔧 Tool Execution — Real Work
Agents have `exec`, `read_file`, `write_file`, `web_search`. They run shell commands, read codebases, write reports. Multi-round tool loops (up to 15 rounds per request).

### 📨 Webhook Dispatch — Task Distribution
Accepts webhook messages as task dispatches. AVA (OpenClaw) sends structured tasks via webhooks, Plug routes them to the right persona.

### 🏥 Health Checker — Self-Monitoring
Periodic health checks with automatic recovery. Watchdog timer restarts crashed services.

### 🧠 Session Compaction — Memory Management
Automatic conversation summarization when sessions exceed token limits.

## Setup

```bash
# Clone
git clone https://github.com/amuzetnoM/plug.git
cd plug

# Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Configure
cp config.example.json ~/.plug/config.json
# Edit config.json with your Discord bot token, model endpoints, router personas

# Run
plug start

# Or with systemd (production)
systemctl --user enable plug-csuite
systemctl --user start plug-csuite
```

## Configuration

```json
{
  "discord": { "token": "..." },
  "models": {
    "provider": "openai",
    "endpoint": "http://localhost:3000/v1",
    "default_model": "claude-sonnet-4.6"
  },
  "router": {
    "personas": [
      {
        "name": "CTO",
        "channel_id": "123456789",
        "model": "claude-sonnet-4.6",
        "workspace": "/path/to/cto/workspace",
        "system_prompt_files": ["AGENTS.md"]
      }
    ]
  }
}
```

## Project Structure

```
plug/
├── plug/
│   ├── bot/
│   │   ├── client.py       # Discord client + message handling + tool loop
│   │   └── chunker.py      # Discord message chunking (2000 char limit)
│   ├── models/
│   │   ├── base.py         # ProviderChain (OpenAI → Ollama fallback)
│   │   └── proxy.py        # Copilot proxy integration
│   ├── router.py           # AgentRouter — channel → persona mapping
│   ├── sessions/
│   │   ├── store.py        # SQLite session store
│   │   └── compactor.py    # Session summarization
│   ├── tools/
│   │   ├── definitions.py  # Tool schemas (OpenAI function-calling format)
│   │   └── executor.py     # Tool execution engine
│   ├── cron/
│   │   └── scheduler.py    # SQLite-backed cron with agent_turn support
│   ├── health.py           # Health checker
│   ├── config.py           # Configuration management
│   ├── cli.py              # CLI entry point
│   └── daemon.py           # Daemon mode
├── copilot_proxy.py        # GitHub Copilot → OpenAI-compatible proxy
└── README.md
```

## Copilot Proxy

Plug includes a proxy that converts GitHub Copilot's API into an OpenAI-compatible endpoint. 42 models (Claude, GPT, Gemini) at zero additional API cost.

```bash
python3 copilot_proxy.py  # Serves on localhost:3000
```

## Production Deployment

Artifact Virtual runs Plug as a systemd service with three-layer reliability:

1. **systemd** — `Restart=always`, auto-start on boot
2. **Watchdog timer** — checks every 2 minutes, restarts if dead
3. **Health checker** — in-process monitoring every 30 seconds

```bash
# Services
systemctl --user status copilot-proxy   # Model provider
systemctl --user status plug-csuite     # C-Suite gateway
systemctl --user status csuite-watchdog # Watchdog timer
```

---

<div align="center">

Built for [Artifact Virtual](https://github.com/Artifact-Virtual) 🏛️

</div>
