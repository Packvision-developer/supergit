# 🔭 SuperGit v2.0

> **AI-powered Git commit assistant.** SuperGit monitors your file saves, analyzes your code changes using Groq's Llama-3.3-70b-versatile model, and proposes professional [Conventional Commits](https://conventionalcommits.org) — without interrupting your flow.

---

## Features

| Feature                | Description                                                                 |
| ---------------------- | --------------------------------------------------------------------------- |
| 🕵️ **Smart Watcher**   | Monitors your repo in the background with a 750 ms debounce                 |
| 🤖 **Map-Reduce AI**   | Analyzes up to 90 files in parallel (max 5 Groq calls at once)              |
| 🔒 **Privacy First**   | Blocks `.env`, private keys, certs, and binaries from ever reaching the API |
| 🌿 **Review Branch**   | Creates `supergit-review/<ts>` for human validation before merging          |
| ⚡ **Circuit Breaker** | Falls back to a generic message after 3 API failures                        |
| 📋 **Audit Log**       | Full log of every AI prompt/response for accountability                     |

---

## Prerequisites

- Python ≥ 3.11
- git ≥ 2.x
- A free [Groq API key](https://console.groq.com)

---

## Installation

```bash
git clone https://github.com/you/supergit
cd supergit
pip install -e .
```

> **Tip**: Use a virtual environment: `python -m venv .venv && source .venv/bin/activate`

On the first run, SuperGit will:

1. Verify `git` is installed (offer Homebrew install on macOS)
2. Verify you're in a git repository (offer `git init`)
3. Prompt for your Groq API key and save it securely to `~/.supergit/.env`

---

## Quick Start

```bash
# 1. Start the background watcher (ia-off by default)
supergit start

# 2. ... write code, save files ...

# 3. When ready to commit:
supergit commit
```

---

## Commands

### `supergit start [--mode ia-off|ia-on]`

Start the background file watcher.

- **`ia-off`** _(default)_: Diffs are saved to SQLite. AI analysis runs only when you call `supergit commit`.
- **`ia-on`**: Each file save immediately triggers a Groq MAP analysis, so `supergit status` shows live AI explanations of what you're building.

```bash
supergit start
supergit start --mode ia-on
```

### `supergit new <url>`

Initializes a brand new local repository (if not already one), links it to the provided remote URL as `origin`, stages all current files, creates an initial commit, and pushes everything to the `main` branch.

```bash
supergit new https://github.com/user/my-project.git
```

### `supergit stop`

Stop the background watcher.

### `supergit status`

Show watcher state and all pending (uncommitted) changes:

```
  Status  : RUNNING
  PID     : 12345
  Mode    : ia-off
  Repo    : /Users/you/my-project
```

Followed by a table of pending files with AI analysis (if `ia-on`).

### `supergit commit`

The main command. It:

1. Creates a `supergit-review/<timestamp>` branch
2. Runs the Map-Reduce pipeline (MAP each file → REDUCE to one commit)
3. Presents the proposed Conventional Commit message
4. Offers an interactive menu:

```
  [M] Merge — squash into your working branch, delete review branch
  [E] Edit  — open $EDITOR to tweak the message
  [V] View  — show the diff of a specific file
  [A] Abort — stay on review branch for manual handling
```

### `supergit view <file>`

Show the colorized diff for a specific pending file:

```bash
supergit view src/auth.py
```

Also accessible as `[V]` inside the interactive commit screen.

### `supergit config`

View current configuration and optionally update your API key or clear pending events.

### `supergit logs [--n N]`

Show the last N AI interactions from the audit log (default: 20).

---

## Directory Layout

```
~/.supergit/
├── .env          # GROQ_API_KEY (chmod 600)
├── config        # mode=ia-off, repo_root=...
├── events.db     # SQLite WAL database
└── logs/
    ├── watcher.log   # Watcher stdout/stderr
    └── errors.log    # Circuit breaker errors
```

---

## Security

| Guarantee                | Implementation                                      |
| ------------------------ | --------------------------------------------------- |
| No push to remote        | `git push` is never called                          |
| No binaries to AI        | Binary sniff (null bytes) + extension blocklist     |
| Credential files blocked | SENSITIVE_DENYLIST: `.env`, `*.pem`, `id_rsa`, etc. |
| No shell injection       | All `subprocess` calls use list form, `shell=False` |
| Prompt injection guard   | All diff content wrapped in `<DIFF_DATA>` tags      |
| Key at rest              | `~/.supergit/.env` with `chmod 600`                 |

---

## Architecture

```
Developer saves file
        │
        ▼
   [watchdog]  ←──── debounce 750ms ────┐
        │                                │ (reset on each save)
        ▼
   [filters.py]  ──── denied? ──────► skip
        │
        ▼
   [database.py]  ◄── git diff → Base64
        │
        ├──── ia-off ─────────────────► supergit commit
        │
        └──── ia-on (async MAP) ──────► store ia_analysis
                                             │
                              supergit commit
                                             │
                                    [ai_engine.py]
                                    MAP (≤5 parallel, Semaphore)
                                    ↓ (hunk split if > ~12K tokens)
                                    REDUCE → Conventional Commit
                                             │
                                    [cli.py] Interactive TUI
                                    [M] Merge / [E] Edit / [V] View / [A] Abort
```

---

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## License

# MIT
