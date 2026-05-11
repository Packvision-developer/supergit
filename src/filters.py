"""
filters.py — Security & Privacy Layer

Determines which files should be processed by SuperGit.
Blocks binary files, sensitive credentials, and .gitignore-matched paths.
"""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Sensitive file denylist — patterns that should NEVER be sent to any AI API
# ---------------------------------------------------------------------------
SENSITIVE_DENYLIST: list[str] = [
    # Environment / secrets
    ".env",
    ".env.*",
    "*.env",
    "secrets.*",
    "credentials.*",
    # Private keys & certificates
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.cer",
    "*.crt",
    "*.p8",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "id_ecdsa",
    "id_ecdsa.*",
    # Token / auth files
    "*.token",
    "*.secret",
    ".netrc",
    ".npmrc",
    ".pypirc",
    # Keystore / wallet files
    "*.jks",
    "*.keystore",
    # macOS secrets
    "*.keychain",
    # Terraform / cloud state (may contain creds)
    "terraform.tfstate",
    "terraform.tfstate.backup",
    "*.tfvars",
    # Database files (usually binary + sensitive)
    "*.db",
    "*.sqlite",
    "*.sqlite3",
]

# Extensions always treated as binary (never send to AI)
BINARY_EXTENSIONS: set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg",
    ".mp4", ".mp3", ".wav", ".ogg", ".avi", ".mov", ".mkv",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".out",
    ".wasm", ".class", ".pyc", ".pyd",
    ".ttf", ".otf", ".woff", ".woff2",
}

# Max bytes to read when sniffing for binary content
_BINARY_SNIFF_SIZE = 8_192


def is_binary_file(path: str | Path) -> bool:
    """Return True if the file appears to be binary (not text)."""
    p = Path(path)

    # Fast path: known binary extension
    if p.suffix.lower() in BINARY_EXTENSIONS:
        return True

    # Null-byte sniff
    try:
        with open(p, "rb") as f:
            chunk = f.read(_BINARY_SNIFF_SIZE)
        return b"\x00" in chunk
    except OSError:
        # If we can't read it, treat as binary to be safe
        return True


def is_denied(path: str | Path) -> tuple[bool, str]:
    """
    Check whether a file path matches any entry in SENSITIVE_DENYLIST.

    Returns:
        (True, reason)  if the file should be blocked
        (False, "")     if the file is allowed
    """
    p = Path(path)
    name = p.name

    for pattern in SENSITIVE_DENYLIST:
        if fnmatch.fnmatch(name, pattern):
            return True, f"matches denylist pattern '{pattern}'"

    return False, ""


def _read_gitignore_patterns(repo_root: str | Path) -> list[str]:
    """Parse .gitignore in repo_root and return a flat list of fnmatch patterns."""
    gitignore = Path(repo_root) / ".gitignore"
    patterns: list[str] = []
    if not gitignore.is_file():
        return patterns

    with open(gitignore, encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.strip()
            # Skip blank lines and comments
            if not line or line.startswith("#"):
                continue
            # Strip leading negation (we don't support re-includes for now)
            if line.startswith("!"):
                continue
            # Strip leading slash (root-relative)
            line = line.lstrip("/")
            patterns.append(line)

    return patterns


def _matches_gitignore(rel_path: str, patterns: list[str]) -> bool:
    """Return True if rel_path matches any gitignore pattern."""
    parts = Path(rel_path).parts
    for pattern in patterns:
        # Match against the filename alone
        if fnmatch.fnmatch(parts[-1], pattern):
            return True
        # Match against the full relative path
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        # Match against the full path with wildcard prefix (e.g. node_modules/)
        if fnmatch.fnmatch(rel_path, f"**/{pattern}"):
            return True
    return False


def should_process(path: str | Path, repo_root: str | Path) -> tuple[bool, str]:
    """
    Master check: determine whether SuperGit should process this file.

    Returns:
        (True, "ok")          — file is safe to process
        (False, reason_str)   — file should be skipped, with human-readable reason
    """
    p = Path(path).resolve()
    root = Path(repo_root).resolve()

    # 1. Binary check
    if is_binary_file(p):
        return False, "binary file"

    # 2. Denylist check
    denied, reason = is_denied(p)
    if denied:
        return False, f"security denylist: {reason}"

    # 3. .gitignore check
    try:
        rel = str(p.relative_to(root))
    except ValueError:
        # File is outside the repo root — skip it
        return False, "outside repo root"

    gitignore_patterns = _read_gitignore_patterns(root)
    if _matches_gitignore(rel, gitignore_patterns):
        return False, ".gitignore match"

    # 4. Hidden directories (e.g. .git itself)
    for part in p.parts:
        if part.startswith(".") and part not in (".", ".."):
            # Allow files like .github/workflows/*.yml but block .git internals
            if part == ".git":
                return False, ".git internals"

    return True, "ok"
