"""Consequence classification — every tool call gets a severity score.

This is Python code, not prompts. The LLM cannot influence these decisions.
Classification is done at the tool+params level, before execution.
"""

from __future__ import annotations

import re
from enum import IntEnum
from pathlib import Path

from atlas.core.shell_policy import ALWAYS_CONFIRM, evaluate as shell_evaluate


class Consequence(IntEnum):
    BENIGN = 0    # read-only, no state change whatsoever
    LOW = 1       # local state change, trivially reversible
    MEDIUM = 2    # file writes, network reads, UI input
    HIGH = 3      # file deletion, email send, external API writes
    CRITICAL = 4  # irreversible system-level destruction


# Sensitive filesystem paths — writes here escalate to HIGH
_SENSITIVE_PATHS = [
    "/etc", "/usr", "/System", "/Library", "/private/etc",
    "/sbin", "/bin", "/var",
]

# Paths that are HIGH even for reads (credentials, SSH keys, etc.)
_CREDENTIAL_PATHS = [
    ".ssh", ".gnupg", ".aws", ".netrc", "id_rsa", "id_ed25519",
    ".env", "credentials", "secrets", "keystore", "keychain",
]

# Shell commands that are clearly read-only — BENIGN consequence
_SHELL_READONLY_BINARIES = {
    "ls", "pwd", "whoami", "date", "cal", "uptime", "df", "du",
    "which", "file", "wc", "head", "tail", "cat", "echo", "env",
    "printenv", "uname", "hostname", "id", "ps", "top", "git",
    "grep", "rg", "ag", "awk", "sed", "sort", "uniq", "tr", "cut",
    "jq", "yq", "find", "mdfind", "mdls", "system_profiler",
}

# Shell commands that are catastrophically destructive — CRITICAL
_SHELL_CATASTROPHIC_PATTERNS = [
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+[/~]", re.IGNORECASE),  # rm -rf /  or rm -rf ~
    re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r?\s+[/~]", re.IGNORECASE),  # rm -fr /
    re.compile(r":\s*\(\s*\)\s*\{", re.IGNORECASE),                        # fork bomb
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\b.+\bof=/dev/(disk|sd|rd)", re.IGNORECASE),
    re.compile(r"\bshred\b.+(/dev/|/etc/|/boot/)", re.IGNORECASE),
    re.compile(r"wget\s+.+\|\s*(ba)?sh\b"),                                # wget | sh
    re.compile(r"curl\s+.+\|\s*(ba)?sh\b"),                                # curl | sh
]


def classify(tool_name: str, params: dict) -> Consequence:
    """Return the consequence severity of executing this tool with these params."""
    classifiers = _CLASSIFIERS.get(tool_name)
    if classifiers is None:
        # Playwright / browser tools not explicitly listed → HIGH (touch browser state)
        if "playwright" in tool_name.lower() or tool_name.startswith("browser_"):
            return Consequence.HIGH
        # Unknown tool — conservative default
        return Consequence.MEDIUM
    for fn in classifiers:
        result = fn(params)
        if result is not None:
            return result
    return Consequence.BENIGN  # explicit BENIGN means all checks passed


def _classify_shell(params: dict) -> Consequence | None:
    command = params.get("command", "")
    if not command:
        return Consequence.BENIGN

    # Check catastrophic patterns first
    for pattern in _SHELL_CATASTROPHIC_PATTERNS:
        if pattern.search(command):
            return Consequence.CRITICAL

    # Delegate to shell_policy for binary-level verdict
    decision = shell_evaluate(command)
    if decision.blocked:
        return Consequence.CRITICAL
    if decision.needs_confirmation:
        return Consequence.HIGH

    # It passed shell_policy — classify by binary
    import shlex
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return Consequence.HIGH
    if not tokens:
        return Consequence.BENIGN

    binary = Path(tokens[0]).name
    if binary in _SHELL_READONLY_BINARIES:
        return Consequence.BENIGN

    return Consequence.MEDIUM  # allowed binary, not read-only


def _classify_write_file(params: dict) -> Consequence | None:
    path_str = params.get("path", "")
    if not path_str:
        return Consequence.MEDIUM

    # Sensitive system paths → HIGH
    for sensitive in _SENSITIVE_PATHS:
        if path_str.startswith(sensitive):
            return Consequence.HIGH

    # Credential-like paths → HIGH
    path_lower = path_str.lower()
    for cred in _CREDENTIAL_PATHS:
        if cred in path_lower:
            return Consequence.HIGH

    return Consequence.MEDIUM


def _classify_read_file(params: dict) -> Consequence | None:
    path_str = params.get("path", "").lower()
    for cred in _CREDENTIAL_PATHS:
        if cred in path_str:
            return Consequence.HIGH  # reading credentials is high-consequence
    return Consequence.BENIGN


# Each entry: list of classifier functions, first non-None result wins
_CLASSIFIERS: dict[str, list] = {
    # Read-only / BENIGN
    "get_current_time":     [lambda p: Consequence.BENIGN],
    "get_system_info":      [lambda p: Consequence.BENIGN],
    "list_running_apps":    [lambda p: Consequence.BENIGN],
    "get_frontmost_app":    [lambda p: Consequence.BENIGN],
    "spotlight_search":     [lambda p: Consequence.BENIGN],
    "web_search":           [lambda p: Consequence.BENIGN],
    "list_directory":       [lambda p: Consequence.BENIGN],
    "search_files":         [lambda p: Consequence.BENIGN],
    "search_memory":        [lambda p: Consequence.BENIGN],
    "see_screen":           [lambda p: Consequence.BENIGN],
    "read_screen_text":     [lambda p: Consequence.BENIGN],
    "describe_screen":      [lambda p: Consequence.BENIGN],
    "github_get_prs":       [lambda p: Consequence.BENIGN],
    "github_get_issues":    [lambda p: Consequence.BENIGN],
    "github_get_commits":   [lambda p: Consequence.BENIGN],
    "github_search_repos":  [lambda p: Consequence.BENIGN],
    "github_get_user":      [lambda p: Consequence.BENIGN],
    "get_clipboard":        [lambda p: Consequence.BENIGN],

    # LOW — local, reversible
    "open_app":             [lambda p: Consequence.LOW],
    "open_url":             [lambda p: Consequence.LOW],
    "show_notification":    [lambda p: Consequence.LOW],
    "say_text":             [lambda p: Consequence.LOW],
    "control_volume":       [lambda p: Consequence.LOW],
    "control_brightness":   [lambda p: Consequence.LOW],
    "set_clipboard":        [lambda p: Consequence.LOW],
    "save_memory":          [lambda p: Consequence.LOW],

    # MEDIUM — file writes, UI injection, network writes
    "write_file":           [_classify_write_file],
    "type_text":            [lambda p: Consequence.MEDIUM],
    "press_keys":           [lambda p: Consequence.MEDIUM],
    "fetch_url":            [lambda p: Consequence.MEDIUM],
    "github_create_issue":  [lambda p: Consequence.MEDIUM],

    # Depends on content
    "read_file":            [_classify_read_file],
    "run_shell":            [_classify_shell],

    # HIGH — file deletion, external sends
    "delete_file":          [lambda p: Consequence.HIGH],

    # Browser tools — explicit entries; defaulting to MEDIUM is wrong for form fills
    "browser_screenshot":       [lambda p: Consequence.BENIGN],
    "browser_extract_text":     [lambda p: Consequence.LOW],
    "browser_open":             [lambda p: Consequence.LOW],
    "browser_open_url":         [lambda p: Consequence.LOW],
    "browser_click":            [lambda p: Consequence.MEDIUM],
    "browser_fill_form":        [lambda p: Consequence.HIGH],
    "browser_submit_form":      [lambda p: Consequence.HIGH],
    "list_chrome_profiles":     [lambda p: Consequence.BENIGN],
    # Catch-all for any playwright_* or browser_playwright_* tools
    # (registered dynamically by browser module)
}
