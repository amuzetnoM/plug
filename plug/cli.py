"""
PLUG CLI
=========

Command-line interface for the PLUG Discord AI Gateway.

Usage:
    plug bot       — Run the Discord bot directly
    plug daemon    — Run as a background daemon
    plug status    — Show health and status
    plug setup     — Interactive configuration
    plug config    — Show current config
    plug sessions  — Manage sessions
    plug install-service — Generate systemd user service
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


@click.group()
@click.version_option(version="0.1.0", prog_name="PLUG")
def cli() -> None:
    """PLUG — Discord AI Gateway."""
    pass


# ── plug bot ─────────────────────────────────────────────────────────────

@cli.command()
@click.option("--debug", is_flag=True, help="Enable debug logging.")
def bot(debug: bool) -> None:
    """Run the Discord bot directly (foreground)."""
    try:
        asyncio.run(run_bot(debug=debug))
    except KeyboardInterrupt:
        click.echo("\nStopped.")


# ── plug daemon ──────────────────────────────────────────────────────────

@cli.group(invoke_without_command=True)
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Manage the PLUG daemon."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(daemon_start)


@daemon.command("start")
@click.option("--debug", is_flag=True, help="Enable debug logging.")
def daemon_start(debug: bool) -> None:
    """Start the bot as a background daemon."""
    if is_running():
        pid = read_pidfile()
        click.echo(f"PLUG is already running (PID {pid}).")
        return

    click.echo("Starting PLUG daemon...")

    # Fork to background
    pid = os.fork()
    if pid > 0:
        # Parent — wait briefly then check
        time.sleep(1)
        if is_running():
            click.echo(f"PLUG daemon started (PID {read_pidfile()}).")
        else:
            click.echo("Daemon may have failed to start. Check logs:")
            click.echo(f"  tail -f {LOG_FILE}")
        return

    # Child — become session leader
    os.setsid()

    # Second fork
    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    # Daemon process
    sys.stdin.close()
    stdout_log = open(LOG_FILE, "a")
    os.dup2(stdout_log.fileno(), sys.stdout.fileno())
    os.dup2(stdout_log.fileno(), sys.stderr.fileno())

    try:
        asyncio.run(run_bot(debug=debug))
    except Exception as e:
        with open(LOG_FILE, "a") as f:
            f.write(f"\nFATAL: {e}\n")
    finally:
        remove_pidfile()
        os._exit(0)


@daemon.command("stop")
def daemon_stop() -> None:
    """Stop the running daemon."""
    pid = read_pidfile()
    if pid is None:
        click.echo("PLUG is not running.")
        return

    click.echo(f"Stopping PLUG daemon (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait for it to die
        for _ in range(30):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remove_pidfile()
                click.echo("Stopped.")
                return
        click.echo("Process didn't stop gracefully, sending SIGKILL...")
        os.kill(pid, signal.SIGKILL)
        remove_pidfile()
        click.echo("Killed.")
    except ProcessLookupError:
        remove_pidfile()
        click.echo("Process already gone.")


@daemon.command("restart")
@click.option("--debug", is_flag=True)
@click.pass_context
def daemon_restart(ctx: click.Context, debug: bool) -> None:
    """Restart the daemon."""
    ctx.invoke(daemon_stop)
    time.sleep(1)
    ctx.invoke(daemon_start, debug=debug)


# ── plug status ──────────────────────────────────────────────────────────

@cli.command()
def status() -> None:
    """Show PLUG status and health info."""
    pid = read_pidfile()
    config = load_config()

    click.echo("╔══════════════════════════════════╗")
    click.echo("║       PLUG Status                ║")
    click.echo("╠══════════════════════════════════╣")

    # Running status
    if pid:
        click.echo(f"║  Status:  🟢 Running (PID {pid})")
        # Get uptime from /proc
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            # Rough uptime from process start
            create_time = os.path.getctime(f"/proc/{pid}")
            uptime_s = time.time() - create_time
            hours = int(uptime_s // 3600)
            mins = int((uptime_s % 3600) // 60)
            click.echo(f"║  Uptime:  {hours}h {mins}m")
        except Exception:
            click.echo("║  Uptime:  unknown")
    else:
        click.echo("║  Status:  🔴 Stopped")

    click.echo(f"║  Model:   {config.models.primary}")
    click.echo(f"║  Proxy:   {config.models.proxy.base_url}")
    click.echo(f"║  Config:  {CONFIG_FILE}")
    click.echo(f"║  DB:      {DB_FILE}")
    click.echo(f"║  Log:     {LOG_FILE}")

    # Session count
    if DB_FILE.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(DB_FILE))
            count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            conn.close()
            click.echo(f"║  Sessions: {count} ({msg_count} messages)")
        except Exception:
            click.echo("║  Sessions: (db error)")
    else:
        click.echo("║  Sessions: 0")

    click.echo("╚══════════════════════════════════╝")


# ── plug setup ───────────────────────────────────────────────────────────

@cli.command()
def setup() -> None:
    """Interactive configuration setup."""
    click.echo("PLUG Setup")
    click.echo("=" * 40)

    config = load_config()

    # Discord token
    token = click.prompt(
        "Discord bot token",
        default=_mask(config.discord.token) if config.discord.token else "",
        show_default=True,
    )
    if token and not token.startswith("***"):
        config.discord.token = token

    # Guild IDs
    guilds = click.prompt(
        "Guild IDs (comma-separated)",
        default=",".join(config.discord.guild_ids),
        show_default=True,
    )
    config.discord.guild_ids = [g.strip() for g in guilds.split(",") if g.strip()]

    # Model
    config.models.primary = click.prompt(
        "Primary model",
        default=config.models.primary,
        show_default=True,
    )

    # Proxy URL
    config.models.proxy.base_url = click.prompt(
        "Proxy base URL",
        default=config.models.proxy.base_url,
        show_default=True,
    )

    # DM allowlist
    dm_list = click.prompt(
        "DM allowlist user IDs (comma-separated)",
        default=",".join(config.discord.dm_allowlist),
        show_default=True,
    )
    config.discord.dm_allowlist = [u.strip() for u in dm_list.split(",") if u.strip()]

    # Require mention
    config.discord.require_mention = click.confirm(
        "Require @mention in guilds?",
        default=config.discord.require_mention,
    )

    # Save
    config.save()
    click.echo(f"\n✅ Config saved to {CONFIG_FILE}")


# ── plug config ──────────────────────────────────────────────────────────

@cli.command("config")
def show_config() -> None:
    """Show current configuration (secrets masked)."""
    config = load_config()
    data = config.model_dump()

    # Mask secrets
    if data.get("discord", {}).get("token"):
        data["discord"]["token"] = _mask(data["discord"]["token"])
    if data.get("models", {}).get("proxy", {}).get("api_key"):
        data["models"]["proxy"]["api_key"] = _mask(data["models"]["proxy"]["api_key"])

    click.echo(json.dumps(data, indent=2, default=str))


# ── plug sessions ────────────────────────────────────────────────────────

@cli.group()
def sessions() -> None:
    """Manage conversation sessions."""
    pass


@sessions.command("list")
def sessions_list() -> None:
    """List all sessions."""
    if not DB_FILE.exists():
        click.echo("No sessions database found.")
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
        click.echo("No sessions.")
        return

    click.echo(f"{'Channel ID':<22} {'Messages':>8} {'Tokens':>8} {'Last Active'}")
    click.echo("-" * 65)
    for r in rows:
        updated = datetime.fromtimestamp(r["updated_at"]).strftime("%Y-%m-%d %H:%M")
        click.echo(f"{r['channel_id']:<22} {r['msg_count']:>8} {r['tokens']:>8} {updated}")


@sessions.command("view")
@click.argument("channel_id")
@click.option("--limit", "-n", default=20, help="Number of messages to show.")
def sessions_view(channel_id: str, limit: int) -> None:
    """View messages in a session."""
    if not DB_FILE.exists():
        click.echo("No sessions database found.")
        return

    import sqlite3
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT role, content, name, timestamp FROM messages
           WHERE channel_id = ? ORDER BY id DESC LIMIT ?""",
        (channel_id, limit),
    ).fetchall()
    conn.close()

    if not rows:
        click.echo(f"No messages in session {channel_id}.")
        return

    for r in reversed(rows):
        ts = datetime.fromtimestamp(r["timestamp"]).strftime("%H:%M:%S")
        role = r["role"].upper()
        name = f" ({r['name']})" if r["name"] else ""
        content = (r["content"] or "")[:200]
        click.echo(f"[{ts}] {role}{name}: {content}")


@sessions.command("clear")
@click.argument("channel_id", required=False)
@click.option("--all", "clear_all", is_flag=True, help="Clear all sessions.")
@click.confirmation_option(prompt="Are you sure?")
def sessions_clear(channel_id: str | None, clear_all: bool) -> None:
    """Clear session(s)."""
    if not DB_FILE.exists():
        click.echo("No sessions database found.")
        return

    import sqlite3
    conn = sqlite3.connect(str(DB_FILE))

    if clear_all:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM sessions")
        conn.commit()
        click.echo("All sessions cleared.")
    elif channel_id:
        conn.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
        conn.execute("DELETE FROM sessions WHERE channel_id = ?", (channel_id,))
        conn.commit()
        click.echo(f"Session {channel_id} cleared.")
    else:
        click.echo("Specify a channel ID or use --all.")

    conn.close()


# ── plug health ──────────────────────────────────────────────────────────

@cli.command("health")
def health() -> None:
    """Run a one-shot health check."""
    from plug.health import check_once

    async def _check():
        config = load_config()
        proxy_url = config.models.proxy.base_url.replace("/v1", "")
        statuses = await check_once(proxy_url=proxy_url)

        click.echo("PLUG Health Check")
        click.echo("=" * 40)
        for name, status in sorted(statuses.items()):
            icon = "✅" if status.healthy else "❌"
            latency = f" ({status.latency_ms:.0f}ms)" if status.latency_ms else ""
            click.echo(f"  {icon} {name}: {status.message}{latency}")

        # Check bot process
        pid = read_pidfile()
        if pid:
            try:
                import os
                os.kill(pid, 0)
                click.echo(f"  ✅ bot: running (PID {pid})")
            except ProcessLookupError:
                click.echo(f"  ❌ bot: PID {pid} not found (stale pidfile)")
        else:
            click.echo("  ❌ bot: not running")

        # Check cron
        cron_db = CONFIG_DIR / "cron.db"
        if cron_db.exists():
            import sqlite3
            conn = sqlite3.connect(str(cron_db))
            count = conn.execute("SELECT COUNT(*) FROM cron_jobs WHERE enabled = 1").fetchone()[0]
            conn.close()
            click.echo(f"  ✅ cron: {count} active job(s)")
        else:
            click.echo("  ⚪ cron: no jobs")

    asyncio.run(_check())


# ── plug cron ────────────────────────────────────────────────────────────

@cli.group()
def cron() -> None:
    """Manage cron jobs."""
    pass


@cron.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include disabled jobs.")
def cron_list(show_all: bool) -> None:
    """List cron jobs."""
    cron_db = CONFIG_DIR / "cron.db"
    if not cron_db.exists():
        click.echo("No cron jobs.")
        return

    import sqlite3
    conn = sqlite3.connect(str(cron_db))
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM cron_jobs"
    if not show_all:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY next_run ASC NULLS LAST"
    rows = conn.execute(sql).fetchall()
    conn.close()

    if not rows:
        click.echo("No cron jobs.")
        return

    for r in rows:
        status = "✅" if r["enabled"] else "❌"
        name = r["name"] or r["id"][:8]
        kind = r["schedule_kind"]
        next_run = ""
        if r["next_run"]:
            next_run = datetime.fromtimestamp(r["next_run"]).strftime("%Y-%m-%d %H:%M")
        runs = r["run_count"]
        click.echo(f"  {status} {name:<20} {kind:<6} next={next_run:<18} runs={runs}")


@cron.command("add")
@click.option("--name", "-n", required=True, help="Job name.")
@click.option("--schedule", "-s", required=True, help='Schedule: "30m", "1h", "*/5 * * * *", or ISO timestamp.')
@click.option("--text", "-t", required=True, help="Payload text (message or agent prompt).")
@click.option("--channel", "-c", help="Discord channel ID for delivery.")
@click.option("--agent", is_flag=True, help="Run as agent_turn instead of system_event.")
@click.option("--model", "-m", help="Model override for agent_turn.")
def cron_add(name: str, schedule: str, text: str, channel: str, agent: bool, model: str) -> None:
    """Add a cron job."""
    from plug.cron.scheduler import CronStore, make_job
    import uuid

    # Parse schedule
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
            from datetime import datetime as dt
            at_dt = dt.fromisoformat(schedule)
            kind, at_time = "at", at_dt.timestamp()
            every_ms, cron_expr = None, None
        except ValueError:
            click.echo(f"Invalid schedule: {schedule}")
            return

    job = make_job(
        name=name,
        schedule_kind=kind,
        schedule_every_ms=every_ms,
        schedule_cron_expr=cron_expr,
        schedule_at=at_time,
        payload_kind="agent_turn" if agent else "system_event",
        payload_text=text,
        payload_model=model,
        channel_id=channel,
    )

    async def _add():
        cron_db = CONFIG_DIR / "cron.db"
        store = CronStore(cron_db)
        await store.open()
        await store.add(job)
        await store.close()
        click.echo(f"✅ Added job: {name} ({kind})")
        if job.next_run:
            click.echo(f"   Next run: {datetime.fromtimestamp(job.next_run).strftime('%Y-%m-%d %H:%M:%S')}")

    asyncio.run(_add())


@cron.command("remove")
@click.argument("job_id")
def cron_remove(job_id: str) -> None:
    """Remove a cron job by ID or name."""
    async def _remove():
        cron_db = CONFIG_DIR / "cron.db"
        from plug.cron.scheduler import CronStore
        store = CronStore(cron_db)
        await store.open()

        # Try by ID first, then by name
        removed = await store.remove(job_id)
        if not removed:
            import sqlite3
            conn = sqlite3.connect(str(cron_db))
            row = conn.execute("SELECT id FROM cron_jobs WHERE name = ?", (job_id,)).fetchone()
            conn.close()
            if row:
                removed = await store.remove(row[0])

        await store.close()
        if removed:
            click.echo(f"✅ Removed job: {job_id}")
        else:
            click.echo(f"Job not found: {job_id}")

    asyncio.run(_remove())


@cron.command("runs")
@click.argument("job_id")
@click.option("--limit", "-n", default=10)
def cron_runs(job_id: str, limit: int) -> None:
    """Show run history for a job."""
    async def _runs():
        cron_db = CONFIG_DIR / "cron.db"
        from plug.cron.scheduler import CronStore
        store = CronStore(cron_db)
        await store.open()
        runs = await store.get_runs(job_id, limit=limit)
        await store.close()

        if not runs:
            click.echo("No runs.")
            return

        for r in runs:
            ts = datetime.fromtimestamp(r["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
            status = r["status"]
            err = f" — {r['error']}" if r.get("error") else ""
            click.echo(f"  [{ts}] {status}{err}")

    asyncio.run(_runs())


# ── plug install-service ─────────────────────────────────────────────────

@cli.command("install-service")
@click.option("--with-proxy", is_flag=True, help="Also install copilot-proxy service.")
def install_service(with_proxy: bool) -> None:
    """Generate and install systemd user service(s)."""
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)

    python_path = sys.executable
    plug_path = Path(__file__).resolve().parent.parent

    # Main bot service
    bot_unit = f"""\
[Unit]
Description=PLUG Discord AI Gateway
After=network-online.target
Wants=network-online.target
{f"Requires=plug-proxy.service" if with_proxy else ""}
{f"After=plug-proxy.service" if with_proxy else ""}

[Service]
Type=simple
ExecStart={python_path} -m plug bot
WorkingDirectory={plug_path}
Restart=always
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=10
Environment=PYTHONUNBUFFERED=1

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.plug %h/workspace
PrivateTmp=yes

[Install]
WantedBy=default.target
"""
    bot_file = service_dir / "plug.service"
    bot_file.write_text(bot_unit)
    click.echo(f"✅ Bot service: {bot_file}")

    if with_proxy:
        proxy_script = plug_path / "copilot_proxy.py"
        proxy_unit = f"""\
[Unit]
Description=PLUG Copilot Proxy (OpenAI-compatible)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python_path} {proxy_script}
WorkingDirectory={plug_path}
Restart=always
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=10
Environment=PYTHONUNBUFFERED=1

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.copilot-proxy
PrivateTmp=yes

[Install]
WantedBy=default.target
"""
        proxy_file = service_dir / "plug-proxy.service"
        proxy_file.write_text(proxy_unit)
        click.echo(f"✅ Proxy service: {proxy_file}")

    click.echo("\nTo enable:")
    click.echo("  systemctl --user daemon-reload")
    if with_proxy:
        click.echo("  systemctl --user enable --now plug-proxy plug")
    else:
        click.echo("  systemctl --user enable --now plug")
    click.echo("\nTo check:")
    click.echo("  systemctl --user status plug")
    click.echo("  journalctl --user -u plug -f")
    if with_proxy:
        click.echo("  systemctl --user status plug-proxy")
        click.echo("  journalctl --user -u plug-proxy -f")


# ── Helpers ──────────────────────────────────────────────────────────────

def _mask(secret: str, show: int = 4) -> str:
    """Mask a secret string, showing only the last N characters."""
    if len(secret) <= show:
        return "***"
    return "***" + secret[-show:]


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
