"""Hard limits — unconditional blocks that no LLM output can override.

These are Python code, not prompts. They cannot be jailbroken by injecting
instructions into tool parameters. The gate function returns a block reason
string (truthy = blocked), or None (allowed through to next layer).
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path


# --- Rule type: (name, predicate(tool_name, params) -> str | None) ---
# Return a human-readable block reason, or None if the rule doesn't apply.

def _check_rm_root(tool: str, params: dict) -> str | None:
    """Block recursive deletion of root, home dir itself, or critical system dirs."""
    if tool != "run_shell":
        return None
    cmd = params.get("command", "")

    # Parse with shlex so we handle both short and long flag forms
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        return None

    if not tokens or Path(tokens[0]).name != "rm":
        return None

    # Check if recursive flag is present (short or long form)
    has_recursive = False
    path_args: list[str] = []
    skip_next = False

    for tok in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if tok == "--":
            # Everything after -- is paths
            path_args.extend(tokens[tokens.index("--") + 1:])
            break
        if tok.startswith("--"):
            if tok in ("--recursive", "--force"):
                if tok == "--recursive":
                    has_recursive = True
            elif tok.startswith("--dir") or tok.startswith("--interactive"):
                pass
            # Long options with values — skip next token
        elif tok.startswith("-") and not tok.startswith("--"):
            # Short flags cluster: -rf, -Rf, -fr, -r, etc.
            flags = tok.lstrip("-")
            if "r" in flags or "R" in flags:
                has_recursive = True
        else:
            path_args.append(tok)

    if not has_recursive:
        return None

    _CRITICAL_PREFIXES = ["/etc", "/usr", "/System", "/Library", "/bin", "/sbin",
                          "/private/etc", "/var", "/private/var"]

    home = Path.home()
    for arg in path_args:
        try:
            resolved = Path(arg).expanduser().resolve()
        except (OSError, ValueError):
            resolved = Path(arg)

        # Block if targeting root, home dir itself, or a critical system dir
        if str(resolved) == "/" or resolved == Path("/"):
            return "hard limit: rm -r targeting root /"
        if resolved == home:
            return "hard limit: rm -r targeting home directory directly"
        for prefix in _CRITICAL_PREFIXES:
            try:
                resolved.relative_to(prefix)
                return f"hard limit: rm -r targeting system path {prefix}"
            except ValueError:
                continue

    return None


def _check_fork_bomb(tool: str, params: dict) -> str | None:
    if tool != "run_shell":
        return None
    cmd = params.get("command", "")
    # Classic bash fork bomb and variants
    if re.search(r":\s*\(\s*\)\s*\{", cmd):
        return "hard limit: fork bomb pattern detected"
    return None


def _check_disk_destruction(tool: str, params: dict) -> str | None:
    if tool != "run_shell":
        return None
    cmd = params.get("command", "")

    # dd writing to a disk device
    if re.search(r"\bdd\b.+\bof=/dev/(disk|sd|rd|nvme)", cmd, re.IGNORECASE):
        return "hard limit: dd targeting disk device"

    # mkfs (formats filesystem)
    if re.search(r"\bmkfs\b", cmd, re.IGNORECASE):
        return "hard limit: mkfs (filesystem format) detected"

    # shred targeting /dev or /etc
    if re.search(r"\bshred\b.+(/dev/|/etc/|/System/)", cmd, re.IGNORECASE):
        return "hard limit: shred targeting critical path"

    return None


def _check_remote_code_exec(tool: str, params: dict) -> str | None:
    if tool != "run_shell":
        return None
    cmd = params.get("command", "")

    # curl/wget piped directly to shell — remote code execution
    if re.search(r"\b(curl|wget)\b.+\|\s*(ba)?sh\b", cmd, re.IGNORECASE):
        return "hard limit: remote code execution (pipe to shell)"
    if re.search(r"\b(curl|wget)\b.+\|\s*python[0-9.]?\b", cmd, re.IGNORECASE):
        return "hard limit: remote code execution (pipe to python)"

    return None


def _check_delete_home(tool: str, params: dict) -> str | None:
    """Block delete_file targeting home directory itself."""
    if tool != "delete_file":
        return None
    path_str = params.get("path", "")
    if not path_str:
        return None
    try:
        resolved = Path(path_str).expanduser().resolve()
        home = Path.home().resolve()
        # Block if deleting home dir itself or a critical child
        if resolved == home:
            return "hard limit: cannot delete home directory"
        for crit in ["/.ssh", "/.gnupg", "/.aws"]:
            if str(resolved) == str(home) + crit:
                return f"hard limit: cannot delete {crit}"
    except (OSError, ValueError):
        pass
    return None


def _check_credential_exfil(tool: str, params: dict) -> str | None:
    """Block shell commands that look like credential exfiltration."""
    if tool != "run_shell":
        return None
    cmd = params.get("command", "")

    # scp/rsync sending .ssh or .aws to remote
    if re.search(r"\b(scp|rsync)\b.+\.ssh", cmd, re.IGNORECASE):
        return "hard limit: exfiltration of .ssh via scp/rsync"
    if re.search(r"\b(scp|rsync)\b.+\.aws", cmd, re.IGNORECASE):
        return "hard limit: exfiltration of .aws via scp/rsync"

    # curl/wget POSTing contents of credential files
    if re.search(r"\bcurl\b.+(-d|--data).+(\.ssh|\.aws|\.env|keychain)", cmd, re.IGNORECASE):
        return "hard limit: curl exfiltrating credential content"

    return None


def _check_privilege_escalation(tool: str, params: dict) -> str | None:
    if tool != "run_shell":
        return None
    cmd = params.get("command", "")

    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        return None

    if tokens and Path(tokens[0]).name == "sudo":
        return "hard limit: sudo (privilege escalation) is never auto-approved"

    return None


def _check_write_system_paths(tool: str, params: dict) -> str | None:
    """Block write_file to OS-critical paths."""
    if tool != "write_file":
        return None
    path_str = params.get("path", "")
    _SYSTEM_PATHS = [
        "/etc/", "/usr/", "/System/", "/Library/", "/bin/",
        "/sbin/", "/private/etc/", "/var/",
    ]
    for sp in _SYSTEM_PATHS:
        if path_str.startswith(sp):
            return f"hard limit: write to system path {sp}"
    return None


# Ordered list of all rules — checked in sequence, first match blocks
_RULES: list = [
    _check_rm_root,
    _check_fork_bomb,
    _check_disk_destruction,
    _check_remote_code_exec,
    _check_delete_home,
    _check_credential_exfil,
    _check_privilege_escalation,
    _check_write_system_paths,
]


def check(tool_name: str, params: dict) -> str | None:
    """Check all hard limits. Returns block reason string, or None if clear."""
    for rule in _RULES:
        reason = rule(tool_name, params)
        if reason:
            return reason
    return None
