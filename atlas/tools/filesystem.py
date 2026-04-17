"""Filesystem tools — read, write, list, search files. Hardened for edge cases."""

from __future__ import annotations

import os
from pathlib import Path

from atlas.core.models import Tier
from atlas.tools.registry import ToolRegistry

# Safety: never touch these paths
BLOCKED_PATHS = {
    "/etc", "/var", "/usr", "/bin", "/sbin", "/System",
    "/Library", "/private", "/dev", "/proc",
}

MAX_FILE_READ = 5 * 1024 * 1024   # 5 MB
MAX_SEARCH_MATCHES = 50
MAX_DIR_ENTRIES = 200


def _is_path_allowed(p: Path) -> tuple[bool, str]:
    """Check if a path is safe to operate on."""
    resolved = p.resolve()
    home = Path.home().resolve()

    # Allow under home, /tmp, /Applications
    allowed_roots = [str(home), "/tmp", "/private/tmp", "/Applications"]
    if not any(str(resolved).startswith(r) for r in allowed_roots):
        return False, f"Access denied: {resolved} is outside allowed paths"

    # Block dotfiles that contain secrets
    name = resolved.name.lower()
    if name in (".env", ".env.local", ".env.production", "credentials.json",
                ".aws", ".ssh", ".gnupg", ".netrc"):
        return False, f"Access denied: {name} may contain secrets"

    return True, ""


def register(registry: ToolRegistry) -> None:

    @registry.register(
        name="read_file",
        description="Read the contents of a file. Returns the text content.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path"},
                "max_lines": {"type": "integer", "description": "Max lines to read (0 = all)"},
            },
            "required": ["path"],
        },
        tier=Tier.AUTO,
    )
    def read_file(path: str, max_lines: int = 0) -> str:
        p = Path(path).expanduser().resolve()

        allowed, reason = _is_path_allowed(p)
        if not allowed:
            return reason

        if not p.exists():
            return f"Error: {p} does not exist"
        if not p.is_file():
            return f"Error: {p} is not a file"
        if p.stat().st_size > MAX_FILE_READ:
            return f"Error: file too large ({_human_size(p.stat().st_size)}). Use max_lines parameter."

        try:
            text = p.read_text(errors="replace")
        except PermissionError:
            return f"Error: permission denied reading {p}"

        if max_lines > 0:
            lines = text.splitlines(keepends=True)
            text = "".join(lines[:max_lines])
            if len(lines) > max_lines:
                text += f"\n... ({len(lines) - max_lines} more lines)"
        return text

    @registry.register(
        name="write_file",
        description="Write content to a file. Creates parent directories if needed. Requires confirmation.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path"},
                "content": {"type": "string", "description": "Content to write"},
                "append": {"type": "boolean", "description": "Append instead of overwrite"},
            },
            "required": ["path", "content"],
        },
        tier=Tier.NOTIFY,
        destructive=False,
    )
    def write_file(path: str, content: str, append: bool = False) -> str:
        p = Path(path).expanduser().resolve()

        allowed, reason = _is_path_allowed(p)
        if not allowed:
            return reason

        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        try:
            with p.open(mode) as f:
                f.write(content)
            action = "Appended" if append else "Written"
            return f"{action} {len(content)} chars to {p}"
        except PermissionError:
            return f"Error: permission denied writing to {p}"

    @registry.register(
        name="list_directory",
        description="List files and directories in a path with types and sizes.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: home)"},
                "pattern": {"type": "string", "description": "Glob pattern filter (e.g. '*.py')"},
                "recursive": {"type": "boolean", "description": "Search recursively"},
            },
            "required": [],
        },
        tier=Tier.AUTO,
    )
    def list_directory(path: str = "~", pattern: str = "*", recursive: bool = False) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: {p} does not exist"
        if not p.is_dir():
            return f"Error: {p} is not a directory"

        try:
            if recursive:
                entries = sorted(p.rglob(pattern))[:MAX_DIR_ENTRIES]
            else:
                entries = sorted(p.glob(pattern))[:MAX_DIR_ENTRIES]
        except PermissionError:
            return f"Error: permission denied listing {p}"

        lines = []
        for e in entries:
            try:
                if e.is_dir():
                    lines.append(f"[dir]  {e.relative_to(p)}/")
                else:
                    size = _human_size(e.stat().st_size)
                    lines.append(f"[file] {e.relative_to(p)}  ({size})")
            except (OSError, ValueError):
                continue

        if not lines:
            return "Empty directory or no matches"
        result = "\n".join(lines)
        if len(entries) >= MAX_DIR_ENTRIES:
            result += f"\n... (truncated at {MAX_DIR_ENTRIES} entries)"
        return result

    @registry.register(
        name="search_files",
        description="Search for a text pattern across files in a directory. Like grep.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text to search for (case-insensitive)"},
                "path": {"type": "string", "description": "Directory to search in"},
                "glob": {"type": "string", "description": "File glob filter (e.g. '*.py')"},
            },
            "required": ["pattern"],
        },
        tier=Tier.AUTO,
    )
    def search_files(pattern: str, path: str = ".", glob: str = "*") -> str:
        if not pattern.strip():
            return "Error: empty search pattern"

        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: {p} does not exist"

        results = []
        files_checked = 0
        for fpath in p.rglob(glob):
            if not fpath.is_file() or fpath.stat().st_size > 1_000_000:
                continue
            files_checked += 1
            if files_checked > 1000:
                results.append("... (stopped after checking 1000 files)")
                break
            try:
                text = fpath.read_text(errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if pattern.lower() in line.lower():
                        results.append(f"{fpath.relative_to(p)}:{i}: {line.strip()[:200]}")
                        if len(results) >= MAX_SEARCH_MATCHES:
                            return "\n".join(results) + f"\n... (truncated at {MAX_SEARCH_MATCHES} matches)"
            except (PermissionError, OSError):
                continue

        if not results:
            return f"No matches for '{pattern}' in {files_checked} files"
        return "\n".join(results)

    @registry.register(
        name="delete_file",
        description="Delete a file or empty directory. Always requires confirmation.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to delete"},
            },
            "required": ["path"],
        },
        tier=Tier.CONFIRM,
        destructive=True,
    )
    def delete_file(path: str) -> str:
        p = Path(path).expanduser().resolve()

        allowed, reason = _is_path_allowed(p)
        if not allowed:
            return reason

        if not p.exists():
            return f"Error: {p} does not exist"
        if p.is_file():
            p.unlink()
            return f"Deleted file: {p}"
        if p.is_dir():
            if any(p.iterdir()):
                return f"Error: directory {p} is not empty. Won't delete."
            p.rmdir()
            return f"Deleted empty directory: {p}"
        return f"Error: {p} is not a regular file or directory"


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"
