"""
cli.py — SuperGit Command-Line Interface

Commands:
  supergit start   [--mode ia-off|ia-on]
  supergit stop
  supergit status
  supergit commit
  supergit config
  supergit logs    [--n N]
  supergit +       <file>      (alias: supergit view <file>)

First-run wizard checks:
  1. git installed  → offer Homebrew install on macOS
  2. CWD is a git repo → offer git init
  3. GROQ_API_KEY in ~/.supergit/.env → prompt and save
"""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.columns import Columns
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .ai_engine import AIEngine
from .daemon import daemon_status, read_pid, start_daemon, stop_daemon
from .database import DatabaseManager, run_sync
from .filters import should_process

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUPERGIT_DIR = Path.home() / ".supergit"
ENV_FILE = SUPERGIT_DIR / ".env"
console = Console()

app = typer.Typer(
    name="supergit",
    help="🔭 SuperGit — AI-powered Git commit assistant",
    add_completion=False,
    rich_markup_mode="rich",
)

# ---------------------------------------------------------------------------
# Helpers — setup & validation
# ---------------------------------------------------------------------------

def _load_env() -> None:
    """Load ~/.supergit/.env into os.environ if it exists."""
    if ENV_FILE.is_file():
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=False)


def _get_api_key() -> str | None:
    """Return GROQ_API_KEY from env, or None."""
    _load_env()
    return os.environ.get("GROQ_API_KEY") or os.environ.get("SUPERGIT_GROQ_KEY")


def _save_api_key(key: str) -> None:
    """Persist GROQ_API_KEY to ~/.supergit/.env."""
    SUPERGIT_DIR.mkdir(parents=True, exist_ok=True)
    existing = ENV_FILE.read_text() if ENV_FILE.is_file() else ""
    lines = [l for l in existing.splitlines() if not l.startswith("GROQ_API_KEY")]
    lines.append(f"GROQ_API_KEY={key}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    # Restrict permissions
    ENV_FILE.chmod(0o600)


def _git_is_installed() -> bool:
    return shutil.which("git") is not None


def _brew_is_installed() -> bool:
    return shutil.which("brew") is not None


def _cwd_is_git_repo() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _get_repo_root() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        typer.echo("❌ Not inside a git repository.", err=True)
        raise typer.Exit(1)
    return result.stdout.strip()


def _current_branch() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "main"


def _ensure_setup(require_api_key: bool = True) -> str | None:
    """
    First-run wizard.
    Returns the GROQ_API_KEY if require_api_key=True, else None.
    Exits with helpful messages if prerequisites are missing.
    """
    # ── 1. Git installed? ───────────────────────────────────────────────
    if not _git_is_installed():
        console.print(
            Panel(
                "[bold red]git is not installed.[/bold red]\n"
                "SuperGit requires git to function.",
                title="⚠️  Missing Dependency",
            )
        )
        if sys.platform == "darwin" and _brew_is_installed():
            install = Confirm.ask("Install git via Homebrew now?", default=True)
            if install:
                subprocess.run(["brew", "install", "git"], check=True)
                console.print("[green]✓ git installed successfully.[/green]")
            else:
                raise typer.Exit(1)
        else:
            console.print(
                "Please install git: [link=https://git-scm.com]https://git-scm.com[/link]"
            )
            raise typer.Exit(1)

    # ── 2. Inside a git repo? ───────────────────────────────────────────
    if not _cwd_is_git_repo():
        console.print(
            Panel(
                "[yellow]The current directory is not a git repository.[/yellow]",
                title="⚠️  No Git Repo",
            )
        )
        init = Confirm.ask("Initialize a git repository here?", default=True)
        if init:
            subprocess.run(["git", "init"], check=True)
            console.print("[green]✓ git repository initialized.[/green]")
        else:
            raise typer.Exit(1)

    # ── 3. Git Identity Configured? ─────────────────────────────────────
    # Check if user.name and user.email are set globally or locally
    name_check = subprocess.run(["git", "config", "user.name"], capture_output=True)
    email_check = subprocess.run(["git", "config", "user.email"], capture_output=True)
    
    if name_check.returncode != 0 or email_check.returncode != 0:
        console.print(
            Panel(
                "[bold yellow]Your Git identity is not configured.[/bold yellow]\n"
                "Git needs to know who you are to create commits.",
                title="⚠️  Missing Git Config",
            )
        )
        name = Prompt.ask("Enter your full name (e.g., Jane Doe)")
        email = Prompt.ask("Enter your email address (e.g., jane@example.com)")
        if name.strip() and email.strip():
            subprocess.run(["git", "config", "--global", "user.name", name.strip()], check=True)
            subprocess.run(["git", "config", "--global", "user.email", email.strip()], check=True)
            console.print("[green]✓ Git identity configured globally.[/green]")
        else:
            console.print("[red]Name and email are required. Aborting.[/red]")
            raise typer.Exit(1)

    # ── 4. API Key ─────────────────────────────────────────────────────
    if not require_api_key:
        return None

    api_key = _get_api_key()
    if not api_key:
        console.print(
            Panel(
                "[bold yellow]No GROQ_API_KEY found.[/bold yellow]\n"
                "Get your free key at: [link=https://console.groq.com]https://console.groq.com[/link]",
                title="🔑 API Key Required",
            )
        )
        api_key = Prompt.ask("Paste your Groq API key", password=True)
        if not api_key.strip():
            console.print("[red]No key provided. Aborting.[/red]")
            raise typer.Exit(1)
        _save_api_key(api_key.strip())
        console.print(f"[green]✓ API key saved to {ENV_FILE}[/green]")
        api_key = api_key.strip()

    return api_key


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command("start")
def cmd_start(
    mode: str = typer.Option(
        "ia-off",
        "--mode",
        "-m",
        help="Watcher mode: [bold]ia-off[/bold] (default) or [bold]ia-on[/bold]",
        show_default=True,
    ),
) -> None:
    """[bold green]Start[/bold green] the SuperGit background watcher."""
    if mode not in ("ia-off", "ia-on"):
        console.print("[red]Invalid mode. Use ia-off or ia-on.[/red]")
        raise typer.Exit(1)

    api_key = _ensure_setup(require_api_key=(mode == "ia-on"))
    repo_root = _get_repo_root()

    status = daemon_status()
    if status["running"]:
        console.print(
            f"[yellow]Watcher already running (PID {status['pid']}).[/yellow]"
        )
        raise typer.Exit(0)

    try:
        pid = start_daemon(repo_root, mode=mode, api_key=api_key)
        console.print(
            Panel(
                f"[bold green]✓ SuperGit watcher started[/bold green]\n"
                f"  PID        : {pid}\n"
                f"  Repo       : {repo_root}\n"
                f"  Mode       : [cyan]{mode}[/cyan]\n"
                f"  Logs       : ~/.supergit/logs/watcher.log",
                title="🔭 SuperGit",
            )
        )
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command("new")
def cmd_new(
    url: str = typer.Argument(..., help="The remote Git repository URL (https/ssh)"),
) -> None:
    """[bold magenta]Initialize[/bold magenta] a new repo and upload to remote."""
    # 1. Ensure git is installed
    if not _git_is_installed():
        console.print("[red]❌ Git is not installed.[/red]")
        raise typer.Exit(1)

    # 2. Prevent accidental overwriting of an existing setup
    if _cwd_is_git_repo():
        console.print(
            "[yellow]⚠️  This directory is already a Git repository.[/yellow]"
        )
        confirm = Confirm.ask("Do you want to add/overwrite the 'origin' remote anyway?", default=False)
        if not confirm:
            raise typer.Exit(0)
    else:
        # 3. Run git init
        console.print("[cyan]⚙ Initializing new git repository...[/cyan]")
        try:
            subprocess.run(["git", "init"], check=True, capture_output=True)
            console.print("[green]✓ Git repository initialized.[/green]")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to init git: {e.stderr.decode()}[/red]")
            raise typer.Exit(1)

    # 4. Add origin
    console.print(f"[cyan]⚙ Linking remote origin to {url}...[/cyan]")
    # Attempt removal if it already exists
    subprocess.run(["git", "remote", "remove", "origin"], capture_output=True)
    try:
        subprocess.run(["git", "remote", "add", "origin", url], check=True, capture_output=True)
        console.print("[green]✓ Remote 'origin' added.[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to add remote: {e.stderr.decode()}[/red]")
        raise typer.Exit(1)

    # 5. Initial Commit logic
    console.print("[cyan]⚙ Staging and committing existing files...[/cyan]")
    try:
        subprocess.run(["git", "add", "."], check=True)
        # Check if there are staged changes to commit
        status = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if status.returncode != 0:
            commit_res = subprocess.run(
                ["git", "commit", "-m", "chore: initial commit via supergit"],
                capture_output=True,
                text=True
            )
            if commit_res.returncode == 0:
                console.print("[green]✓ Created initial commit.[/green]")
            else:
                console.print("\n[bold red]❌ Git commit failed.[/bold red]")
                error_text = commit_res.stderr.strip() or commit_res.stdout.strip()
                
                api_key = _get_api_key()
                if api_key and error_text:
                    console.print("\n[bold cyan]🤖 Consultando a la IA para diagnosticar el problema...[/bold cyan]")
                    async def _explain():
                        async with AIEngine(api_key) as engine:
                            return await engine.explain_git_error("git commit -m '...'", error_text)
                    
                    with console.status("[bold cyan]Analizando error...[/bold cyan]"):
                        explanation = run_sync(_explain())
                    
                    console.print(
                        Panel(
                            explanation,
                            title="💡 Solución propuesta por SuperGit AI",
                            border_style="yellow"
                        )
                    )
                else:
                    console.print(f"[dim]Commit failed: {error_text}[/dim]")
                raise typer.Exit(1)
        else:
            console.print("[yellow]No changes to commit.[/yellow]")
    except subprocess.CalledProcessError as e:
        console.print(f"[dim]Commit skip or failed: {e}[/dim]")

    # 6. Set branch name and push
    console.print("[cyan]⚙ Renaming branch to 'main' and pushing to origin...[/cyan]")
    try:
        subprocess.run(["git", "branch", "-M", "main"], check=True, capture_output=True)
        
        # 1st run: Allow interactive prompt if necessary (no capture)
        result = subprocess.run(["git", "push", "-u", "origin", "main"])
        
        if result.returncode == 0:
            console.print(
                Panel(
                    "[bold green]🎉 Project successfully uploaded to Git![/bold green]\n"
                    f"Branch: main\nRemote: {url}",
                    border_style="green"
                )
            )
        else:
            console.print("\n[bold red]❌ Git push failed.[/bold red]")
            
            # 2nd run: Capture the exact output quietly for the AI diagnostic
            capture_res = subprocess.run(["git", "push", "-u", "origin", "main"], capture_output=True, text=True)
            error_text = capture_res.stderr.strip() or capture_res.stdout.strip()
            
            if error_text:
                api_key = _get_api_key()
                if api_key:
                    console.print("\n[bold cyan]🤖 Consultando a la IA para diagnosticar el problema...[/bold cyan]")
                    
                    async def _explain():
                        async with AIEngine(api_key) as engine:
                            return await engine.explain_git_error("git push -u origin main", error_text)
                    
                    with console.status("[bold cyan]Analizando error...[/bold cyan]"):
                        explanation = run_sync(_explain())
                    
                    console.print(
                        Panel(
                            explanation,
                            title="💡 Solución propuesta por SuperGit AI",
                            border_style="yellow"
                        )
                    )
                else:
                    console.print("[dim](Configura tu GROQ_API_KEY para diagnóstico automático de errores AI)[/dim]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed during branch rename: {e}[/red]")


@app.command("stop")
def cmd_stop() -> None:
    """[bold red]Stop[/bold red] the SuperGit background watcher."""
    stopped = stop_daemon()
    if stopped:
        console.print("[green]✓ SuperGit watcher stopped.[/green]")
    else:
        console.print("[yellow]No watcher was running.[/yellow]")


@app.command("status")
def cmd_status() -> None:
    """Show watcher status and pending uncommitted events."""
    _ensure_setup(require_api_key=False)
    repo_root = _get_repo_root()
    status = daemon_status()

    # Watcher state panel
    state_color = "green" if status["running"] else "red"
    state_label = "RUNNING" if status["running"] else "STOPPED"
    console.print(
        Panel(
            f"  Status  : [{state_color}]{state_label}[/{state_color}]\n"
            f"  PID     : {status['pid'] or '—'}\n"
            f"  Mode    : [cyan]{status['mode']}[/cyan]\n"
            f"  Repo    : {status['repo_root'] or repo_root}",
            title="🔭 SuperGit Status",
        )
    )

    # Pending events table
    async def _fetch():
        async with DatabaseManager() as db:
            events = await db.get_pending_events(repo_root)
            tokens = await db.token_total(repo_root)
            return events, tokens

    events, tokens = run_sync(_fetch())

    if not events:
        console.print("[dim]No pending changes.[/dim]")
        return

    table = Table(title=f"Pending Changes ({len(events)} files, ~{tokens} tokens used)")
    table.add_column("ID", style="dim", width=6)
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Time", style="magenta")
    table.add_column("AI Analysis", style="white")

    for ev in events:
        ts = datetime.fromtimestamp(ev["timestamp"]).strftime("%H:%M:%S")
        analysis = ev.get("ia_analysis") or "[dim]pending[/dim]"
        if len(analysis) > 60:
            analysis = analysis[:57] + "..."
        table.add_row(str(ev["id"]), ev["filepath"], ts, analysis)

    console.print(table)


@app.command("commit")
def cmd_commit() -> None:
    """[bold blue]Analyse changes and create a reviewed commit.[/bold blue]"""
    api_key = _ensure_setup(require_api_key=True)
    assert api_key
    repo_root = _get_repo_root()
    original_branch = _current_branch()

    # ── Fetch pending events ──────────────────────────────────────────────
    async def _fetch():
        async with DatabaseManager() as db:
            return await db.get_pending_events(repo_root)

    events = run_sync(_fetch())

    if not events:
        console.print("[yellow]No pending changes to commit.[/yellow]")
        raise typer.Exit(0)

    console.print(f"\n[bold]Found [cyan]{len(events)}[/cyan] pending file change(s).[/bold]")

    # ── Create review branch ──────────────────────────────────────────────
    timestamp = int(time.time())
    review_branch = f"supergit-review/{timestamp}"

    try:
        subprocess.run(
            ["git", "checkout", "-b", review_branch],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Could not create review branch: {e.stderr.decode()}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓ Created branch [bold]{review_branch}[/bold][/green]")

    # ── Run Map-Reduce ────────────────────────────────────────────────────
    console.print("\n[bold yellow]⚙  Running AI analysis (Map-Reduce)…[/bold yellow]")

    async def _analyse():
        async with DatabaseManager() as db:
            async with AIEngine(api_key, db) as engine:
                return await engine.run_commit_analysis(events)

    with console.status("[bold cyan]Contacting Groq…[/bold cyan]"):
        commit_msg = run_sync(_analyse())

    # ── Show proposed commit message ──────────────────────────────────────
    console.print(
        Panel(
            escape(commit_msg),
            title="✨ Proposed Commit Message",
            border_style="cyan",
        )
    )

    # ── Interactive prompt ─────────────────────────────────────────────────
    while True:
        console.print(
            "\n[bold]What would you like to do?[/bold]\n"
            "  [bold green][M][/bold green] Merge (squash into [cyan]"
            f"{original_branch}[/cyan] and delete review branch)\n"
            "  [bold yellow][E][/bold yellow] Edit commit message\n"
            "  [bold blue][V][/bold blue] View diff of a specific file\n"
            "  [bold red][A][/bold red] Abort (stay on review branch)\n"
        )
        choice = Prompt.ask("Choice", choices=["m", "e", "v", "a", "M", "E", "V", "A"]).lower()

        if choice == "m":
            _do_merge(events, commit_msg, review_branch, original_branch, repo_root)
            break
        elif choice == "e":
            commit_msg = _do_edit(commit_msg)
            console.print(
                Panel(escape(commit_msg), title="✏️  Updated Message", border_style="yellow")
            )
        elif choice == "v":
            _do_view_diff(events)
        elif choice == "a":
            console.print(
                f"[yellow]Aborted. You are on branch [bold]{review_branch}[/bold].[/yellow]\n"
                "Use [cyan]git checkout <branch>[/cyan] to return."
            )
            break


def _do_merge(
    events: list[dict],
    commit_msg: str,
    review_branch: str,
    original_branch: str,
    repo_root: str,
) -> None:
    """Squash-merge review branch into original_branch, mark events committed."""
    # Stage all changed files
    for ev in events:
        filepath = ev["filepath"]
        ok, _ = should_process(filepath, repo_root)
        if ok:
            subprocess.run(["git", "add", "--", filepath], capture_output=True)

    # Commit on review branch
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", commit_msg],
        capture_output=True,
    )

    # Switch back to original
    subprocess.run(["git", "checkout", original_branch], check=True, capture_output=True)

    # Squash merge
    result = subprocess.run(
        ["git", "merge", "--squash", review_branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]Squash merge failed:\n{result.stderr}[/red]")
        return

    # Final commit
    subprocess.run(
        ["git", "commit", "-m", commit_msg],
        check=True,
        capture_output=True,
    )

    # Delete review branch
    subprocess.run(
        ["git", "branch", "-D", review_branch],
        capture_output=True,
    )

    # Mark events as committed in DB
    event_ids = [ev["id"] for ev in events]

    async def _mark():
        async with DatabaseManager() as db:
            await db.mark_committed(event_ids)

    run_sync(_mark())

    console.print(
        Panel(
            f"[bold green]✓ Commit merged into [cyan]{original_branch}[/cyan][/bold green]\n"
            f"  Message: {commit_msg.splitlines()[0]}\n"
            f"  Files  : {len(events)}\n"
            f"  Branch [dim]{review_branch}[/dim] deleted.",
            title="🎉 Done",
            border_style="green",
        )
    )


def _do_edit(current_msg: str) -> str:
    """Open $EDITOR with the current commit message and return the edited version."""
    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix="supergit_commit_",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(current_msg)
        tmp_path = f.name

    subprocess.run([editor, tmp_path])  # blocking; no shell=True

    with open(tmp_path, encoding="utf-8") as f:
        edited = f.read().strip()

    os.unlink(tmp_path)
    return edited or current_msg


def _do_view_diff(events: list[dict]) -> None:
    """Let the user pick a file and display its colorized diff."""
    file_map = {str(i + 1): ev for i, ev in enumerate(events)}

    console.print("\n[bold]Available files:[/bold]")
    for num, ev in file_map.items():
        console.print(f"  [cyan]{num}[/cyan]. {ev['filepath']}")

    choice = Prompt.ask("Enter file number (or press Enter to cancel)", default="")
    if not choice.strip():
        return

    ev = file_map.get(choice.strip())
    if not ev:
        console.print("[red]Invalid selection.[/red]")
        return

    diff_b64 = ev.get("diff_snippet")
    if not diff_b64:
        console.print("[yellow]No diff available for this file.[/yellow]")
        return

    diff_text = base64.b64decode(diff_b64).decode("utf-8", errors="replace")
    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title=f"📄 {ev['filepath']}", border_style="blue"))


# ── supergit + <file> ─────────────────────────────────────────────────────

@app.command("view")
def cmd_view(
    file: str = typer.Argument(..., help="Relative path of the file to view diff for"),
) -> None:
    """[bold blue]View[/bold blue] the pending diff for a specific file."""
    _ensure_setup(require_api_key=False)
    repo_root = _get_repo_root()

    async def _fetch():
        async with DatabaseManager() as db:
            return await db.get_pending_events(repo_root)

    events = run_sync(_fetch())
    matched = [ev for ev in events if ev["filepath"].endswith(file)]

    if not matched:
        console.print(f"[yellow]No pending diff found for file: {file}[/yellow]")
        raise typer.Exit(0)

    ev = matched[-1]  # most recent
    diff_b64 = ev.get("diff_snippet")
    if not diff_b64:
        console.print("[yellow]No diff stored for this file.[/yellow]")
        raise typer.Exit(0)

    diff_text = base64.b64decode(diff_b64).decode("utf-8", errors="replace")
    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title=f"📄 {ev['filepath']}", border_style="blue"))


# ── supergit config ───────────────────────────────────────────────────────

@app.command("config")
def cmd_config() -> None:
    """[bold]View or update[/bold] SuperGit configuration."""
    _load_env()
    console.print(
        Panel(
            f"  Config dir : {SUPERGIT_DIR}\n"
            f"  .env file  : {ENV_FILE}\n"
            f"  API key    : {'[green]set[/green]' if _get_api_key() else '[red]NOT SET[/red]'}\n"
            f"  DB path    : {SUPERGIT_DIR / 'events.db'}\n"
            f"  Logs       : {SUPERGIT_DIR / 'logs'}",
            title="⚙️  SuperGit Config",
        )
    )

    update = Confirm.ask("\nUpdate API key?", default=False)
    if update:
        new_key = Prompt.ask("New Groq API key", password=True)
        if new_key.strip():
            _save_api_key(new_key.strip())
            console.print("[green]✓ API key updated.[/green]")

    reset = Confirm.ask("Clear all pending events (does NOT undo git changes)?", default=False)
    if reset:
        confirmed = Confirm.ask("[red]Are you sure?[/red]", default=False)
        if confirmed:
            # Mark everything as committed
            async def _clear():
                repo_root = _get_repo_root()
                async with DatabaseManager() as db:
                    events = await db.get_pending_events(repo_root)
                    ids = [e["id"] for e in events]
                    if ids:
                        await db.mark_committed(ids)
            run_sync(_clear())
            console.print("[green]✓ Pending events cleared.[/green]")


# ── supergit logs ─────────────────────────────────────────────────────────

@app.command("logs")
def cmd_logs(
    n: int = typer.Option(20, "--n", help="Number of log entries to show"),
) -> None:
    """[bold]View[/bold] the AI interaction audit log."""
    async def _fetch():
        async with DatabaseManager() as db:
            return await db.get_logs(n)

    entries = run_sync(_fetch())

    if not entries:
        console.print("[dim]No audit log entries yet.[/dim]")
        return

    table = Table(title=f"AI Audit Log (last {n})")
    table.add_column("ID", style="dim", width=5)
    table.add_column("Time", style="magenta", width=10)
    table.add_column("File", style="cyan")
    table.add_column("Tokens", justify="right", style="yellow", width=8)
    table.add_column("Response (truncated)", style="white")
    table.add_column("Error", style="red")

    for entry in entries:
        ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%H:%M:%S")
        response = (entry.get("response") or "")[:50]
        error = entry.get("error") or ""
        table.add_row(
            str(entry["id"]),
            ts,
            entry.get("filepath") or "[dim]REDUCE[/dim]",
            str(entry.get("tokens_used") or 0),
            response,
            error[:40],
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
