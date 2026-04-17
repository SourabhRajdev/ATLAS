"""Shell command safety — proper parsing, allowlist, capability check.

Replaces the substring blocklist that could be trivially bypassed.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


# Read-only / informational binaries — run without confirmation
ALLOWLIST = {
    "ls", "pwd", "whoami", "date", "cal", "uptime", "df", "du",
    "which", "where", "type", "file", "wc", "head", "tail", "cat",
    "echo", "env", "printenv", "uname", "hostname", "id",
    "ps", "top", "free", "vmstat",
    "ping", "dig", "nslookup", "ifconfig",
    "git", "python3", "python", "node", "npm", "pip", "pip3",
    "find", "grep", "rg", "ag", "awk", "sed", "sort", "uniq", "tr", "cut",
    "jq", "yq", "tee", "open",
    "mkdir", "touch", "cp", "mv",  # write but reversible via undo log
    "head", "tail",
    # Mac-specific
    "osascript", "shortcuts", "pmset", "system_profiler", "defaults",
    "mdfind", "mdls", "say",
}

# Binaries that always require confirmation
ALWAYS_CONFIRM = {
    "rm", "rmdir", "sudo", "su", "chmod", "chown", "dd", "mkfs",
    "kill", "killall", "shutdown", "reboot", "diskutil",
    "launchctl", "systemctl",
}

# Binaries that are never allowed
NEVER = {
    "mkfs", ":(){:|:&};:",
}

# Shell metacharacters that escalate to confirm
METACHARS = {";", "&&", "||", "|", "`", "$(", ">", ">>", "<", "<("}


@dataclass
class ShellDecision:
    verdict: str  # "allow" | "confirm" | "block"
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"

    @property
    def needs_confirmation(self) -> bool:
        return self.verdict == "confirm"

    @property
    def blocked(self) -> bool:
        return self.verdict == "block"

    @classmethod
    def allow(cls) -> "ShellDecision":
        return cls("allow")

    @classmethod
    def confirm(cls, reason: str) -> "ShellDecision":
        return cls("confirm", reason)

    @classmethod
    def block(cls, reason: str) -> "ShellDecision":
        return cls("block", reason)


# Path roots Atlas may freely operate on. Anything else → confirm.
def _allowed_roots() -> list[Path]:
    home = Path.home()
    return [
        home,               # everything under home — user explicitly granted full access
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/Applications"),
        Path("/usr/local"),
        Path("/opt/homebrew"),
    ]


def _path_under_allowed(p: Path) -> bool:
    p = p.expanduser().resolve()
    for root in _allowed_roots():
        try:
            p.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def evaluate(command: str) -> ShellDecision:
    """Evaluate a shell command. Returns ShellDecision."""
    if not command or not command.strip():
        return ShellDecision.block("empty command")

    # Parse with shlex — reject unparseable
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as e:
        return ShellDecision.block(f"unparseable: {e}")

    if not tokens:
        return ShellDecision.block("no tokens")

    # Metacharacters present? Always confirm — even if first binary is allowlisted
    # because chained commands can hide destructive ops.
    for ch in METACHARS:
        if ch in command:
            return ShellDecision.confirm(f"shell metacharacter '{ch}' — chained commands need approval")

    binary = Path(tokens[0]).name

    if binary in NEVER:
        return ShellDecision.block(f"binary forbidden: {binary}")

    if binary in ALWAYS_CONFIRM:
        return ShellDecision.confirm(f"requires approval: {binary}")

    if binary not in ALLOWLIST:
        return ShellDecision.confirm(f"binary not in allowlist: {binary}")

    # Path arguments must be under allowed roots
    for arg in tokens[1:]:
        if arg.startswith("/") or arg.startswith("~") or arg.startswith("./") or arg.startswith("../"):
            try:
                p = Path(arg).expanduser()
                if p.is_absolute() or arg.startswith("~"):
                    if not _path_under_allowed(p):
                        return ShellDecision.confirm(f"path outside allowed roots: {p}")
            except (OSError, ValueError):
                continue

    return ShellDecision.allow()
