"""
PLUG CLI
=========

Command-line interface for the PLUG Discord AI Gateway.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import click

from plug.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DB_FILE,
    LOG_FILE,
    PID_FILE,
    PlugConfig,
    ensure_config_dir,
    load_config,
)
from plug.daemon import is_running, read_pidfile, remove_pidfile, run_bot, setup_logging


# ── Branding ─────────────────────────────────────────────────────────────

LOGO = r"""
    ╔═══════════════════════════════════════╗
    ║                                       ║
    ║     ██████╗ ██╗     ██╗   ██╗ ██████╗ ║
    ║     ██╔══██╗██║     ██║   ██║██╔════╝ ║
    ║     ██████╔╝██║     ██║   ██║██║  ███╗║
    ║     ██╔═══╝ ██║     ██║   ██║██║   ██║║
    ║     ██║     ███████╗╚██████╔╝╚██████╔╝║
    ║     ╚═╝     ╚══════╝ ╚═════╝  ╚═════╝ ║
    ║                                       ║
    ║     Discord AI Gateway       v0.1.0   ║
    ╚═══════════════════════════════════════╝
"""

LOGO_MINI = "⚡ PLUG"

BOX_T = "╔"
BOX_B = "╚"
BOX_H = "═"
BOX_V = "║"
BOX_TR = "╗"
BOX_BR = "╝"
BOX_M = "╠"
BOX_MR = "╣"
W = 44


def box_top(title: str = "") -> str:
    if title:
        inner = f" {title} "
        pad = W - 2 - len(inner)
        return f"{BOX_T}{BOX_H}{inner}{BOX_H * pad}{BOX_TR}"
    return f"{BOX_T}{BOX_H * (W - 2)}{BOX_TR}"


def box_mid() -> str:
    return f"{BOX_M}{BOX_H * (W - 2)}{BOX_MR}"


def box_row(text: str) -> str:
    padding = W - 4 - len(text)
    if padding < 0:
        text = text[: W - 7] + "..."
        padding = 0
    return f"{BOX_V}  {text}{' ' * padding}{BOX_V}"


def box_bot() -> str:
    return f"{BOX_B}{BOX_H * (W - 2)}{BOX_BR}"


def success(msg: str) -> None:
    click.echo(click.style(f"  ✓ {msg}", fg="green"))


def fail(msg: str) -> None:
    click.echo(click.style(f"  ✗ {msg}", fg="red"))


def info(msg: str) -> None:
    click.echo(click.style(f"  → {msg}", fg="cyan"))


def dim(msg: str) -> None:
    click.echo(click.style(f"    {msg}", dim=True))


# ── CLI Root ─────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.version_option(version="0.1.0", prog_name="PLUG")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """⚡ PLUG — Discord AI Gateway"""
    if ctx.invoked_subcommand is None:
        click.echo(LOGO)
        click.echo("  Usage: plug <command>")
        click.echo()
        click.echo("  Commands:")
        click.echo(click.style("    init      ", fg="cyan") + "First-time setup (interactive)")
        click.echo(click.style("    start     ", fg="cyan") + "Start the bot")
        click.echo(click.style("    stop      ", fg="cyan") + "Stop the bot")
        click.echo(click.style("    restart   ", fg="cyan") + "Restart the bot")
        click.echo(click.style("    status    ", fg="cyan") + "Show status dashboard")
        click.echo(click.style("    health    ", fg="cyan") + "Run health checks")
        click.echo(click.style("    logs      ", fg="cyan") + "Tail live logs")
        click.echo(click.style("    config    ", fg="cyan") + "View/edit configuration")
        click.echo(click.style("    sessions  ", fg="cyan") + "Manage sessions")
        click.echo(click.style("    cron      ", fg="cyan") + "Manage scheduled jobs")
        click.echo(click.style("    install   ", fg="cyan") + "Install as systemd service")
        click.echo(click.style("    uninstall ", fg="cyan") + "Remove systemd service")
        click.echo()
        dim("https://github.com/amuzetnoM/plug")
        click.echo()


# ── plug init ────────────────────────────────────────────────────────────

@cli.command()
@click.option("--token", "-t", help="Discord bot token.")
@click.option("--guild", "-g", help="Discord guild/server ID.")
@click.option("--model", "-m", default="claude-opus-4.6", help="Primary model name.")
@click.option("--proxy", "-p", default="http://localhost:3000/v1", help="OpenAI-compatible proxy URL.")
@click.option("--workspace", "-w", help="Path to workspace (SOUL.md, AGENTS.md, etc).")
@click.option("--yes", "-y", is_flag=True, help="Accept defaults, skip prompts.")
def init(token: str | None, guild: str | None, model: str, proxy: str, workspace: str | None, yes: bool) -> None:
    """First-time setup. Creates ~/.plug/ and config.json."""
    click.echo()
    click.echo(LOGO)
    click.echo(click.style("  First-time setup", fg="cyan", bold=True))
    click.echo()

    ensure_config_dir()
    config = load_config()

    # Step 1: Discord token
    click.echo(click.style("  1/5 ", fg="yellow", bold=True) + "Discord Bot Token")
    if token:
        config.discord.token = token
        success("Token provided via flag")
    elif yes and config.discord.token:
        success("Using existing token")
    else:
        dim("Create a bot at https://discord.com/developers/applications")
        dim("Bot → Token → Copy")
        t = click.prompt("  Token", default="", show_default=False, hide_input=True)
        if t:
            config.discord.token = t
            success("Token saved")
        elif config.discord.token:
            info("Keeping existing token")
        else:
            fail("No token. Bot won't connect without one.")
    click.echo()

    # Step 2: Guild ID
    click.echo(click.style("  2/5 ", fg="yellow", bold=True) + "Discord Server (Guild ID)")
    if guild:
        config.discord.guild_ids = [guild]
        success(f"Guild: {guild}")
    elif yes:
        success(f"Guild: {', '.join(config.discord.guild_ids)}")
    else:
        dim("Right-click your server → Copy Server ID")
        dim("(Enable Developer Mode in Discord settings if needed)")
        g = click.prompt("  Guild ID", default=config.discord.guild_ids[0] if config.discord.guild_ids else "")
        if g:
            config.discord.guild_ids = [g.strip()]
            success(f"Guild: {g.strip()}")
    click.echo()

    # Step 3: Model
    click.echo(click.style("  3/5 ", fg="yellow", bold=True) + "AI Model")
    if yes:
        config.models.primary = model
        success(f"Model: {model}")
    else:
        dim("Any model your proxy supports (e.g. claude-opus-4.6, gpt-4o, llama3)")
        m = click.prompt("  Model", default=model)
        config.models.primary = m
        success(f"Model: {m}")
    click.echo()

    # Step 4: Proxy URL
    click.echo(click.style("  4/5 ", fg="yellow", bold=True) + "API Proxy URL")
    if yes:
        config.models.proxy.base_url = proxy
        success(f"Proxy: {proxy}")
    else:
        dim("OpenAI-compatible endpoint (Copilot proxy, Ollama, LM Studio, etc)")
        p = click.prompt("  URL", default=proxy)
        config.models.proxy.base_url = p
        success(f"Proxy: {p}")
    click.echo()

    # Step 5: Workspace
    click.echo(click.style("  5/5 ", fg="yellow", bold=True) + "Workspace Path")
    default_ws = workspace or config.agent.workspace or str(Path.home() / "workspace")
    if yes:
        config.agent.workspace = default_ws
        success(f"Workspace: {default_ws}")
    else:
        dim("Directory with your SOUL.md, AGENTS.md, USER.md, etc")
        dim("(Created automatically if it doesn't exist)")
        w = click.prompt("  Path", default=default_ws)
        config.agent.workspace = w
        success(f"Workspace: {w}")
    click.echo()

    # Save
    config.save()

    click.echo(box_top("Setup Complete"))
    click.echo(box_row(f"Config:    {CONFIG_FILE}"))
    click.echo(box_row(f"Database:  {DB_FILE}"))
    click.echo(box_row(f"Logs:      {LOG_FILE}"))
    click.echo(box_mid())
    click.echo(box_row("Next steps:"))
    click.echo(box_row("  plug start          Run the bot"))
    click.echo(box_row("  plug install        Install as service"))
    click.echo(box_row("  plug status         Check everything"))
    click.echo(box_bot())
    click.echo()


# ── plug start / stop / restart ──────────────────────────────────────────

def _is_termux() -> bool:
    """Detect Termux/Android environment (no fork/systemd support)."""
    return os.path.isdir("/data/data/com.termux") or "TERMUX_VERSION" in os.environ


def _daemonize_fork() -> bool:
    """Unix double-fork daemon. Returns True in parent, False in child."""
    pid = os.fork()
    if pid > 0:
        return True  # parent

    os.setsid()
    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    sys.stdin.close()
    stdout_log = open(LOG_FILE, "a")
    os.dup2(stdout_log.fileno(), sys.stdout.fileno())
    os.dup2(stdout_log.fileno(), sys.stderr.fileno())
    return False  # child


def _daemonize_subprocess() -> None:
    """Termux-compatible daemon using subprocess (no fork)."""
    import subprocess
    venv_python = sys.executable
    cmd = [venv_python, "-m", "plug.cli", "start", "--foreground"]
    log_fd = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        cmd,
        stdout=log_fd,
        stderr=log_fd,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Write PID immediately so status/stop work
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid))


@cli.command()
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground (no daemon).")
@click.option("--debug", is_flag=True, help="Enable debug logging.")
def start(foreground: bool, debug: bool) -> None:
    """Start the PLUG bot."""
    if foreground:
        click.echo(f"{LOGO_MINI} Starting in foreground...")
        try:
            asyncio.run(run_bot(debug=debug))
        except KeyboardInterrupt:
            click.echo(f"\n{LOGO_MINI} Stopped.")
        return

    if is_running():
        pid = read_pidfile()
        info(f"Already running (PID {pid})")
        return

    click.echo(f"{LOGO_MINI} Starting daemon...")

    if _is_termux():
        # Termux: no os.fork(), use subprocess detach
        _daemonize_subprocess()
        time.sleep(1.5)
        if is_running():
            success(f"Running (PID {read_pidfile()})")
            dim(f"Logs: tail -f {LOG_FILE}")
        else:
            fail("Failed to start. Check logs:")
            dim(f"tail -f {LOG_FILE}")
        return

    # Unix: classic double-fork
    is_parent = _daemonize_fork()
    if is_parent:
        time.sleep(1.5)
        if is_running():
            success(f"Running (PID {read_pidfile()})")
            dim(f"Logs: tail -f {LOG_FILE}")
        else:
            fail("Failed to start. Check logs:")
            dim(f"tail -f {LOG_FILE}")
        return

    try:
        asyncio.run(run_bot(debug=debug))
    except Exception as e:
        with open(LOG_FILE, "a") as f:
            f.write(f"\nFATAL: {e}\n")
    finally:
        remove_pidfile()
        os._exit(0)


@cli.command()
def stop() -> None:
    """Stop the PLUG bot."""
    pid = read_pidfile()
    if pid is None:
        info("Not running.")
        return

    click.echo(f"{LOGO_MINI} Stopping (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remove_pidfile()
                success("Stopped.")
                return
        os.kill(pid, signal.SIGKILL)
        remove_pidfile()
        success("Killed (SIGKILL).")
    except ProcessLookupError:
        remove_pidfile()
        success("Already stopped.")


@cli.command()
@click.option("--debug", is_flag=True)
@click.option("--all", "restart_all", is_flag=True, help="Restart bot + proxy + systemd services.")
@click.pass_context
def restart(ctx: click.Context, debug: bool, restart_all: bool) -> None:
    """Restart PLUG. Use --all to restart all services systematically."""
    import subprocess

    if not restart_all:
        ctx.invoke(stop)
        time.sleep(1)
        ctx.invoke(start, foreground=False, debug=debug)
        return

    click.echo()
    click.echo(box_top("Full Restart"))
    click.echo(box_row("Stopping all PLUG services..."))
    click.echo(box_bot())
    click.echo()

    # 1. Kill bot (PID or systemd)
    using_systemd = _systemd_active("plug")
    using_proxy_systemd = _systemd_active("plug-proxy")

    if using_systemd:
        info("Stopping plug.service...")
        subprocess.run(["systemctl", "--user", "stop", "plug"], capture_output=True)
        success("plug.service stopped")
    else:
        pid = read_pidfile()
        if pid:
            info(f"Stopping bot (PID {pid})...")
            try:
                os.kill(pid, signal.SIGTERM)
                _wait_pid(pid, timeout=15)
                remove_pidfile()
                success("Bot stopped")
            except ProcessLookupError:
                remove_pidfile()
                success("Bot already stopped")
        else:
            info("Bot not running")

    # 2. Kill proxy
    if using_proxy_systemd:
        info("Stopping plug-proxy.service...")
        subprocess.run(["systemctl", "--user", "stop", "plug-proxy"], capture_output=True)
        success("plug-proxy.service stopped")
    else:
        proxy_pids = _find_pids("copilot_proxy")
        if proxy_pids:
            for p in proxy_pids:
                info(f"Stopping proxy (PID {p})...")
                try:
                    os.kill(p, signal.SIGTERM)
                    _wait_pid(p, timeout=10)
                    success(f"Proxy PID {p} stopped")
                except ProcessLookupError:
                    success(f"Proxy PID {p} already gone")
        else:
            info("Proxy not running")

    # 3. Brief pause for sockets to release
    time.sleep(2)

    click.echo()
    click.echo(box_top("Starting Services"))
    click.echo(box_bot())
    click.echo()

    # 4. Start proxy first
    if using_proxy_systemd:
        info("Starting plug-proxy.service...")
        subprocess.run(["systemctl", "--user", "start", "plug-proxy"], capture_output=True)
        time.sleep(2)
        if _systemd_active("plug-proxy"):
            success("plug-proxy.service running")
        else:
            fail("plug-proxy.service failed to start")
    else:
        # Start proxy if copilot_proxy.py exists
        proxy_script = Path(__file__).resolve().parent.parent / "copilot_proxy.py"
        if proxy_script.exists():
            info("Starting copilot proxy...")
            subprocess.Popen(
                [sys.executable, str(proxy_script)],
                stdout=open(CONFIG_DIR / "proxy.log", "a"),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            time.sleep(2)
            new_pids = _find_pids("copilot_proxy")
            if new_pids:
                success(f"Proxy running (PID {new_pids[0]})")
            else:
                fail("Proxy failed to start. Check ~/.plug/proxy.log")

    # 5. Start bot
    if using_systemd:
        info("Starting plug.service...")
        subprocess.run(["systemctl", "--user", "start", "plug"], capture_output=True)
        time.sleep(2)
        if _systemd_active("plug"):
            success("plug.service running")
        else:
            fail("plug.service failed to start")
    else:
        ctx.invoke(start, foreground=False, debug=debug)

    # 6. Health check
    click.echo()
    ctx.invoke(health)


# ── plug status ──────────────────────────────────────────────────────────

@cli.command()
def status() -> None:
    """Show PLUG status dashboard."""
    config = load_config()
    pid = read_pidfile()

    click.echo()
    click.echo(box_top("PLUG Status"))

    # Process
    if pid:
        try:
            os.kill(pid, 0)
            # Cross-platform uptime: /proc/ on Linux, fallback to psutil or skip
            uptime_str = ""
            try:
                create_time = os.path.getctime(f"/proc/{pid}")
                uptime_s = time.time() - create_time
                h, m = int(uptime_s // 3600), int((uptime_s % 3600) // 60)
                uptime_str = f", {h}h{m}m"
            except (FileNotFoundError, OSError):
                pass  # Termux/macOS: no /proc, skip uptime
            click.echo(box_row(f"Process:  🟢 Running (PID {pid}{uptime_str})"))
        except (ProcessLookupError, FileNotFoundError):
            click.echo(box_row("Process:  🔴 Stale PID (cleaning)"))
            remove_pidfile()
    else:
        click.echo(box_row("Process:  🔴 Stopped"))

    click.echo(box_row(f"Model:    {config.models.primary}"))
    click.echo(box_row(f"Proxy:    {config.models.proxy.base_url}"))

    # Sessions
    if DB_FILE.exists():
        import sqlite3
        conn = sqlite3.connect(str(DB_FILE))
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        click.echo(box_row(f"Sessions: {sessions} ({messages:,} messages)"))
    else:
        click.echo(box_row("Sessions: 0"))

    # Cron
    cron_db = CONFIG_DIR / "cron.db"
    if cron_db.exists():
        import sqlite3
        conn = sqlite3.connect(str(cron_db))
        try:
            jobs = conn.execute("SELECT COUNT(*) FROM cron_jobs WHERE enabled = 1").fetchone()[0]
            click.echo(box_row(f"Cron:     {jobs} active job(s)"))
        except Exception:
            click.echo(box_row("Cron:     (no table)"))
        conn.close()
    else:
        click.echo(box_row("Cron:     —"))

    click.echo(box_mid())
    click.echo(box_row(f"Config:   {CONFIG_FILE}"))
    click.echo(box_row(f"Logs:     {LOG_FILE}"))
    click.echo(box_bot())
    click.echo()


# ── plug health ──────────────────────────────────────────────────────────

@cli.command()
def health() -> None:
    """Run health checks on all components."""
    from plug.health import check_once

    click.echo()
    click.echo(box_top("Health Check"))

    async def _run():
        config = load_config()
        proxy_url = config.models.proxy.base_url.replace("/v1", "")
        statuses = await check_once(proxy_url=proxy_url)

        for name, s in sorted(statuses.items()):
            icon = "🟢" if s.healthy else "🔴"
            latency = f"  {s.latency_ms:.0f}ms" if s.latency_ms else ""
            click.echo(box_row(f"{icon} {name:<12} {s.message}{latency}"))

    asyncio.run(_run())

    # Bot process
    pid = read_pidfile()
    if pid:
        try:
            os.kill(pid, 0)
            click.echo(box_row(f"🟢 bot          PID {pid}"))
        except ProcessLookupError:
            click.echo(box_row("🔴 bot          stale PID"))
    else:
        click.echo(box_row("🔴 bot          not running"))

    # Cron
    cron_db = CONFIG_DIR / "cron.db"
    if cron_db.exists():
        import sqlite3
        conn = sqlite3.connect(str(cron_db))
        try:
            count = conn.execute("SELECT COUNT(*) FROM cron_jobs WHERE enabled = 1").fetchone()[0]
            click.echo(box_row(f"🟢 cron         {count} job(s)"))
        except Exception:
            click.echo(box_row("⚪ cron         no jobs"))
        conn.close()
    else:
        click.echo(box_row("⚪ cron         —"))

    click.echo(box_bot())
    click.echo()


# ── plug logs ────────────────────────────────────────────────────────────

@cli.command()
@click.option("--lines", "-n", default=50, help="Number of lines to show.")
@click.option("--follow", "-f", is_flag=True, help="Follow log output.")
def logs(lines: int, follow: bool) -> None:
    """View bot logs."""
    if not LOG_FILE.exists():
        info("No log file yet. Start the bot first.")
        return

    if follow:
        os.execvp("tail", ["tail", "-f", "-n", str(lines), str(LOG_FILE)])
    else:
        os.execvp("tail", ["tail", "-n", str(lines), str(LOG_FILE)])


# ── plug config ──────────────────────────────────────────────────────────

@cli.group(invoke_without_command=True)
@click.pass_context
def config(ctx: click.Context) -> None:
    """View or edit configuration."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(config_show)


@config.command("show")
def config_show() -> None:
    """Show current config (secrets masked)."""
    cfg = load_config()
    data = cfg.model_dump()

    if data.get("discord", {}).get("token"):
        data["discord"]["token"] = _mask(data["discord"]["token"])
    if data.get("models", {}).get("proxy", {}).get("api_key"):
        data["models"]["proxy"]["api_key"] = _mask(data["models"]["proxy"]["api_key"])

    click.echo(json.dumps(data, indent=2, default=str))


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a config value. Use dot notation: discord.token, models.primary, etc."""
    cfg = load_config()
    data = cfg.model_dump()

    parts = key.split(".")
    target = data
    for p in parts[:-1]:
        if p not in target:
            fail(f"Unknown key: {key}")
            return
        target = target[p]

    final_key = parts[-1]
    if final_key not in target:
        fail(f"Unknown key: {key}")
        return

    # Type coerce
    old = target[final_key]
    if isinstance(old, bool):
        value = value.lower() in ("true", "1", "yes")
    elif isinstance(old, int):
        value = int(value)
    elif isinstance(old, float):
        value = float(value)
    elif isinstance(old, list):
        value = [v.strip() for v in value.split(",")]

    target[final_key] = value
    new_cfg = PlugConfig(**data)
    new_cfg.save()
    success(f"{key} = {value}")


@config.command("path")
def config_path() -> None:
    """Show config file path."""
    click.echo(CONFIG_FILE)


# ── plug sessions ────────────────────────────────────────────────────────

@cli.group()
def sessions() -> None:
    """Manage conversation sessions."""
    pass


@sessions.command("list")
def sessions_list_cmd() -> None:
    """List all sessions."""
    if not DB_FILE.exists():
        info("No sessions yet.")
        return

    import sqlite3
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.channel_id, s.created_at, s.updated_at,
               COUNT(m.id) as msg_count,
               COALESCE(SUM(m.token_count), 0) as tokens
        FROM sessions s
        LEFT JOIN messages m ON m.channel_id = s.channel_id
        GROUP BY s.channel_id
        ORDER BY s.updated_at DESC
    """).fetchall()
    conn.close()

    if not rows:
        info("No sessions.")
        return

    click.echo()
    click.echo(box_top("Sessions"))
    for r in rows:
        updated = datetime.fromtimestamp(r["updated_at"]).strftime("%m/%d %H:%M") if r["updated_at"] else "—"
        tokens = f"{r['tokens']:,}t" if r["tokens"] else "0t"
        click.echo(box_row(f"{r['channel_id'][:16]}  {r['msg_count']:>4} msgs  {tokens:>8}  {updated}"))
    click.echo(box_bot())
    click.echo()


@sessions.command("view")
@click.argument("channel_id")
@click.option("--limit", "-n", default=20)
def sessions_view(channel_id: str, limit: int) -> None:
    """View messages in a session."""
    if not DB_FILE.exists():
        info("No sessions database.")
        return

    import sqlite3
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT role, name, content, timestamp FROM messages
        WHERE channel_id = ? ORDER BY timestamp DESC LIMIT ?
    """, (channel_id, limit)).fetchall()
    conn.close()

    if not rows:
        info(f"No messages for {channel_id}")
        return

    for r in reversed(rows):
        ts = datetime.fromtimestamp(r["timestamp"]).strftime("%H:%M:%S")
        role = r["role"].upper()
        name = f" ({r['name']})" if r["name"] else ""
        content = (r["content"] or "")[:200]
        click.echo(f"  [{ts}] {role}{name}: {content}")


@sessions.command("clear")
@click.argument("channel_id", required=False)
@click.option("--all", "clear_all", is_flag=True, help="Clear all sessions.")
@click.confirmation_option(prompt="Are you sure?")
def sessions_clear(channel_id: str | None, clear_all: bool) -> None:
    """Clear session(s)."""
    if not DB_FILE.exists():
        info("No sessions database.")
        return

    import sqlite3
    conn = sqlite3.connect(str(DB_FILE))

    if clear_all:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM sessions")
        conn.commit()
        success("All sessions cleared.")
    elif channel_id:
        conn.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
        conn.execute("DELETE FROM sessions WHERE channel_id = ?", (channel_id,))
        conn.commit()
        success(f"Session {channel_id} cleared.")
    else:
        fail("Specify a channel ID or use --all.")

    conn.close()


# ── plug cron ────────────────────────────────────────────────────────────

@cli.group()
def cron() -> None:
    """Manage scheduled jobs."""
    pass


@cron.command("list")
def cron_list() -> None:
    """List all cron jobs."""
    cron_db = CONFIG_DIR / "cron.db"
    if not cron_db.exists():
        info("No cron jobs.")
        return

    import sqlite3
    conn = sqlite3.connect(str(cron_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM cron_jobs ORDER BY name").fetchall()
    except Exception:
        info("No cron table.")
        conn.close()
        return
    conn.close()

    if not rows:
        info("No cron jobs.")
        return

    click.echo()
    click.echo(box_top("Cron Jobs"))
    for r in rows:
        status_icon = "🟢" if r["enabled"] else "⚪"
        name = r["name"] or r["id"][:8]
        kind = r["schedule_kind"]
        next_run = ""
        if r["next_run"]:
            next_run = datetime.fromtimestamp(r["next_run"]).strftime("%m/%d %H:%M")
        click.echo(box_row(f"{status_icon} {name:<16} {kind:<6} next: {next_run}"))
    click.echo(box_bot())
    click.echo()


@cron.command("add")
@click.option("--name", "-n", required=True, help="Job name.")
@click.option("--schedule", "-s", required=True, help='Schedule: "30m", "1h", "*/5 * * * *", or ISO timestamp.')
@click.option("--text", "-t", required=True, help="Payload text.")
@click.option("--channel", "-c", help="Discord channel ID for delivery.")
@click.option("--agent", is_flag=True, help="Run as agent turn (LLM call).")
@click.option("--model", "-m", help="Model override for agent turns.")
def cron_add(name: str, schedule: str, text: str, channel: str, agent: bool, model: str) -> None:
    """Add a cron job."""
    from plug.cron.scheduler import CronStore, make_job

    if schedule.endswith("m") and schedule[:-1].isdigit():
        kind, every_ms = "every", int(schedule[:-1]) * 60_000
        cron_expr, at_time = None, None
    elif schedule.endswith("h") and schedule[:-1].isdigit():
        kind, every_ms = "every", int(schedule[:-1]) * 3_600_000
        cron_expr, at_time = None, None
    elif " " in schedule:
        kind, cron_expr = "cron", schedule
        every_ms, at_time = None, None
    else:
        try:
            at_dt = datetime.fromisoformat(schedule)
            kind, at_time = "at", at_dt.timestamp()
            every_ms, cron_expr = None, None
        except ValueError:
            fail(f"Invalid schedule: {schedule}")
            return

    job = make_job(
        name=name, schedule_kind=kind, schedule_every_ms=every_ms,
        schedule_cron_expr=cron_expr, schedule_at=at_time,
        payload_kind="agent_turn" if agent else "system_event",
        payload_text=text, payload_model=model, channel_id=channel,
    )

    async def _add():
        store = CronStore(CONFIG_DIR / "cron.db")
        await store.open()
        await store.add(job)
        await store.close()
        success(f"Job '{name}' added ({kind})")
        if job.next_run:
            dim(f"Next run: {datetime.fromtimestamp(job.next_run).strftime('%Y-%m-%d %H:%M:%S')}")

    asyncio.run(_add())


@cron.command("remove")
@click.argument("job_id")
def cron_remove(job_id: str) -> None:
    """Remove a cron job by ID or name."""
    async def _remove():
        from plug.cron.scheduler import CronStore
        store = CronStore(CONFIG_DIR / "cron.db")
        await store.open()
        removed = await store.remove(job_id)
        if not removed:
            import sqlite3
            conn = sqlite3.connect(str(CONFIG_DIR / "cron.db"))
            row = conn.execute("SELECT id FROM cron_jobs WHERE name = ?", (job_id,)).fetchone()
            conn.close()
            if row:
                removed = await store.remove(row[0])
        await store.close()
        if removed:
            success(f"Removed: {job_id}")
        else:
            fail(f"Not found: {job_id}")

    asyncio.run(_remove())


@cron.command("runs")
@click.argument("job_id")
@click.option("--limit", "-n", default=10)
def cron_runs(job_id: str, limit: int) -> None:
    """Show run history for a job."""
    async def _runs():
        from plug.cron.scheduler import CronStore
        store = CronStore(CONFIG_DIR / "cron.db")
        await store.open()
        runs = await store.get_runs(job_id, limit=limit)
        await store.close()

        if not runs:
            info("No runs.")
            return

        for r in runs:
            ts = datetime.fromtimestamp(r["started_at"]).strftime("%m/%d %H:%M:%S")
            icon = "✓" if r["status"] == "ok" else "✗"
            err = f"  {r['error']}" if r.get("error") else ""
            click.echo(f"  {icon} [{ts}] {r['status']}{err}")

    asyncio.run(_runs())


# ── plug install / uninstall ─────────────────────────────────────────────

@cli.command()
@click.option("--with-proxy", is_flag=True, help="Also install copilot-proxy service.")
def install(with_proxy: bool) -> None:
    """Install PLUG as a systemd/Termux service."""
    if _is_termux():
        _install_termux(with_proxy)
        return

    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)

    python_path = sys.executable
    plug_path = Path(__file__).resolve().parent.parent

    bot_unit = f"""\
[Unit]
Description=PLUG Discord AI Gateway
After=network-online.target
Wants=network-online.target
{f"Requires=plug-proxy.service" if with_proxy else ""}
{f"After=plug-proxy.service" if with_proxy else ""}

[Service]
Type=simple
ExecStart={python_path} -m plug start --foreground
WorkingDirectory={plug_path}
Restart=always
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=10
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.plug %h/workspace
PrivateTmp=yes

[Install]
WantedBy=default.target
"""
    (service_dir / "plug.service").write_text(bot_unit)
    success("plug.service installed")

    if with_proxy:
        proxy_script = plug_path / "copilot_proxy.py"
        proxy_unit = f"""\
[Unit]
Description=PLUG Copilot Proxy
After=network-online.target

[Service]
Type=simple
ExecStart={python_path} {proxy_script}
WorkingDirectory={plug_path}
Restart=always
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=10
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.copilot-proxy
PrivateTmp=yes

[Install]
WantedBy=default.target
"""
        (service_dir / "plug-proxy.service").write_text(proxy_unit)
        success("plug-proxy.service installed")

    click.echo()
    info("Enable with:")
    if with_proxy:
        dim("systemctl --user daemon-reload")
        dim("systemctl --user enable --now plug-proxy plug")
    else:
        dim("systemctl --user daemon-reload")
        dim("systemctl --user enable --now plug")
    click.echo()


@cli.command()
def uninstall() -> None:
    """Remove systemd/Termux services."""
    if _is_termux():
        _uninstall_termux()
        return

    service_dir = Path.home() / ".config" / "systemd" / "user"
    removed = False
    for name in ["plug.service", "plug-proxy.service"]:
        f = service_dir / name
        if f.exists():
            f.unlink()
            success(f"Removed {name}")
            removed = True
    if removed:
        info("Run: systemctl --user daemon-reload")
    else:
        info("No services installed.")


# ── Helpers ──────────────────────────────────────────────────────────────

def _install_termux(with_proxy: bool) -> None:
    """Install PLUG as a Termux boot script (no systemd)."""
    boot_dir = Path.home() / ".termux" / "boot"
    boot_dir.mkdir(parents=True, exist_ok=True)

    python_path = sys.executable
    plug_path = Path(__file__).resolve().parent.parent

    bot_script = f"""#!/data/data/com.termux/files/usr/bin/sh
# PLUG Discord AI Gateway - Termux boot script
cd {plug_path}
{python_path} -m plug start --foreground >> ~/.plug/plug.log 2>&1 &
"""
    bot_file = boot_dir / "plug-bot.sh"
    bot_file.write_text(bot_script)
    os.chmod(bot_file, 0o755)
    success("plug-bot.sh installed to ~/.termux/boot/")

    if with_proxy:
        proxy_script_path = plug_path / "copilot_proxy.py"
        proxy_script = f"""#!/data/data/com.termux/files/usr/bin/sh
# PLUG Copilot Proxy - Termux boot script
cd {plug_path}
{python_path} {proxy_script_path} >> ~/.plug/proxy.log 2>&1 &
"""
        proxy_file = boot_dir / "plug-proxy.sh"
        proxy_file.write_text(proxy_script)
        os.chmod(proxy_file, 0o755)
        success("plug-proxy.sh installed to ~/.termux/boot/")

    click.echo()
    info("Install Termux:Boot from F-Droid to enable auto-start.")
    info("To start now: plug start")
    click.echo()


def _uninstall_termux() -> None:
    """Remove Termux boot scripts."""
    boot_dir = Path.home() / ".termux" / "boot"
    removed = False
    for name in ["plug-bot.sh", "plug-proxy.sh"]:
        f = boot_dir / name
        if f.exists():
            f.unlink()
            success(f"Removed {name}")
            removed = True
    if not removed:
        info("No Termux boot scripts installed.")


def _mask(secret: str, show: int = 4) -> str:
    if len(secret) <= show:
        return "***"
    return "***" + secret[-show:]


def _systemd_active(unit: str) -> bool:
    """Check if a systemd user unit is active."""
    if _is_termux():
        return False  # No systemd on Termux
    import subprocess
    result = subprocess.run(
        ["systemctl", "--user", "is-active", unit],
        capture_output=True, text=True,
    )
    return result.stdout.strip() == "active"


def _find_pids(name: str) -> list[int]:
    """Find PIDs matching a process name pattern."""
    import subprocess
    result = subprocess.run(
        ["pgrep", "-f", name], capture_output=True, text=True,
    )
    pids = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line and line.isdigit():
            pid = int(line)
            if pid != os.getpid():
                pids.append(pid)
    return pids


def _wait_pid(pid: int, timeout: int = 15) -> bool:
    """Wait for a PID to exit. Returns True if exited, False if timed out."""
    for _ in range(timeout * 2):
        try:
            os.kill(pid, 0)
            time.sleep(0.5)
        except ProcessLookupError:
            return True
    return False


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
