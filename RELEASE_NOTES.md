# Plug v0.2.0 — Release Notes

## What's New

### Android/Termux Support
- `plug start` now works on Termux (no `os.fork()` — uses subprocess detach)
- `plug install` creates Termux boot scripts instead of systemd units
- `plug uninstall` cleans up Termux boot scripts
- `plug status` gracefully handles missing `/proc` (macOS/Termux)
- Termux auto-detected via `TERMUX_VERSION` env or `/data/data/com.termux`

### Cross-Platform Install Script
- New `install.sh` — works on Linux, macOS, and Android/Termux
- Detects platform, installs dependencies, sets up virtualenv

### Configuration
- `config.example.json` — annotated template for first-time setup
- `plug init` wizard sets sane defaults (model, proxy URL, workspace)

### Stability
- `MAX_TOOL_ROUNDS` increased from 15 → 45 for agent tasks
- Discord reaction support (`discord_react` tool)
- Discord file/image sending (`discord_send` tool)
- Orphaned tool_calls fix — no more hanging agent rounds
- COMB persistent memory across sessions
- Rate-limit resilience for API calls

## Install

```bash
# Linux / macOS
git clone https://github.com/amuzetnoM/plug.git
cd plug && ./install.sh

# Termux (Android)
pkg install python git
git clone https://github.com/amuzetnoM/plug.git
cd plug && bash install.sh

# Then
plug init    # interactive setup
plug start   # launch
```

## Requirements
- Python ≥ 3.11
- Discord bot token
- LLM proxy endpoint (OpenAI-compatible)
