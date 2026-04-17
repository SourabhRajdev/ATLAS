"""System tools — time, info, shell commands with smart safety."""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime, timezone

from atlas.core.models import Tier
from atlas.tools.registry import ToolRegistry
from atlas.core.shell_policy import evaluate as evaluate_shell

# Max output to prevent memory bombs
MAX_OUTPUT_BYTES = 50_000
MAX_TIMEOUT = 60

# Env keys to strip from subprocess environment (never leak to children)
SECRET_ENV_KEYS = {
    "GEMINI_API_KEY", "ATLAS_GEMINI_API_KEY",
    "DEEPGRAM_API_KEY", "ATLAS_DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY", "ATLAS_ELEVENLABS_API_KEY",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID",
    "GITHUB_TOKEN", "GH_TOKEN",
}


def _safe_subprocess_env() -> dict:
    """Subprocess env with secrets stripped."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV_KEYS}
    env["TERM"] = "dumb"
    return env


def _run_osascript(script: str, timeout: int = 10) -> str:
    """Run an osascript command and return output."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
            env=_safe_subprocess_env(),
        )
        if result.returncode != 0:
            return f"Error: {result.stderr.strip()}"
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "Error: timeout"
    except Exception as e:
        return f"Error: {e}"


def register(registry: ToolRegistry) -> None:

    @registry.register(
        name="get_current_time",
        description="Get the current date and time with timezone.",
        parameters={"type": "object", "properties": {}, "required": []},
        tier=Tier.AUTO,
    )
    def get_current_time() -> str:
        now = datetime.now(timezone.utc)
        local = datetime.now()
        return f"UTC: {now.strftime('%Y-%m-%d %H:%M:%S')} | Local: {local.strftime('%Y-%m-%d %H:%M:%S %Z')}"

    @registry.register(
        name="get_system_info",
        description="Get information about the current system (OS, hardware, Python version).",
        parameters={"type": "object", "properties": {}, "required": []},
        tier=Tier.AUTO,
    )
    def get_system_info() -> dict:
        return {
            "os": platform.system(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "hostname": platform.node(),
            "python": platform.python_version(),
            "user": os.getenv("USER", "unknown"),
            "home": str(os.path.expanduser("~")),
            "cwd": os.getcwd(),
        }

    @registry.register(
        name="run_shell",
        description=(
            "Execute a shell command. Allowlisted safe commands (ls, git, grep, open, osascript, etc.) "
            "run immediately. Delete/destructive operations (rm, sudo, etc.) are blocked — "
            "ask the user to confirm before using those."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
                "working_dir": {"type": "string", "description": "Working directory (default: home)"},
            },
            "required": ["command"],
        },
        tier=Tier.NOTIFY,   # Runs automatically for safe commands; handler gates dangerous ones
    )
    def run_shell(command: str, timeout: int = 30, working_dir: str = "") -> str:
        decision = evaluate_shell(command)
        if decision.blocked:
            return f"BLOCKED: {decision.reason}"
        if decision.needs_confirmation:
            # Delete/destructive — always require explicit user permission
            return (
                f"BLOCKED: '{command}' requires explicit user confirmation "
                f"({decision.reason}). Tell the user to run it themselves or ask them to approve."
            )

        cwd = working_dir or os.path.expanduser("~")
        if not os.path.isdir(cwd):
            return f"Error: working directory '{cwd}' does not exist"

        timeout = min(timeout, MAX_TIMEOUT)

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=_safe_subprocess_env(),
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"

            if len(output) > MAX_OUTPUT_BYTES:
                output = output[:MAX_OUTPUT_BYTES] + f"\n... (truncated at {MAX_OUTPUT_BYTES} chars)"

            return output.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    # ------------------------------------------------------------------ #
    #  macOS-specific Jarvis tools                                         #
    # ------------------------------------------------------------------ #

    @registry.register(
        name="open_app",
        description="Open (activate) a macOS application by name. Works for any app in /Applications.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "App name, e.g. 'Safari', 'Spotify', 'Terminal'"},
            },
            "required": ["name"],
        },
        tier=Tier.NOTIFY,
    )
    def open_app(name: str) -> str:
        try:
            result = subprocess.run(
                ["open", "-a", name],
                capture_output=True, text=True, timeout=10,
                env=_safe_subprocess_env(),
            )
            if result.returncode != 0:
                return f"Error opening '{name}': {result.stderr.strip()}"
            return f"Opened {name}."
        except Exception as e:
            return f"Error: {e}"

    @registry.register(
        name="show_notification",
        description="Show a macOS notification banner with title and message.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Notification title"},
                "message": {"type": "string", "description": "Notification body"},
                "subtitle": {"type": "string", "description": "Optional subtitle"},
            },
            "required": ["title", "message"],
        },
        tier=Tier.AUTO,
    )
    def show_notification(title: str, message: str, subtitle: str = "") -> str:
        sub = f' subtitle "{subtitle}"' if subtitle else ""
        script = f'display notification "{message}"{sub} with title "{title}"'
        result = _run_osascript(script)
        if result.startswith("Error"):
            return result
        return "Notification shown."

    @registry.register(
        name="get_clipboard",
        description="Get the current contents of the macOS clipboard.",
        parameters={"type": "object", "properties": {}, "required": []},
        tier=Tier.AUTO,
    )
    def get_clipboard() -> str:
        try:
            result = subprocess.run(
                ["pbpaste"],
                capture_output=True, text=True, timeout=5,
                env=_safe_subprocess_env(),
            )
            content = result.stdout
            if len(content) > 2000:
                content = content[:2000] + f"... (truncated, {len(result.stdout)} total chars)"
            return content or "(clipboard is empty)"
        except Exception as e:
            return f"Error: {e}"

    @registry.register(
        name="set_clipboard",
        description="Copy text to the macOS clipboard.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to copy"},
            },
            "required": ["text"],
        },
        tier=Tier.NOTIFY,
    )
    def set_clipboard(text: str) -> str:
        try:
            subprocess.run(
                ["pbcopy"],
                input=text.encode(), timeout=5,
                env=_safe_subprocess_env(),
            )
            return f"Copied {len(text)} chars to clipboard."
        except Exception as e:
            return f"Error: {e}"

    @registry.register(
        name="control_volume",
        description="Get or set system output volume. Pass level 0-100 to set; omit to get current.",
        parameters={
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Volume 0-100 (omit to just read)"},
            },
            "required": [],
        },
        tier=Tier.NOTIFY,
    )
    def control_volume(level: int = -1) -> str:
        if level < 0:
            result = _run_osascript("output volume of (get volume settings)")
            return f"Current volume: {result}%"
        level = max(0, min(100, level))
        result = _run_osascript(f"set volume output volume {level}")
        if result.startswith("Error"):
            return result
        return f"Volume set to {level}%."

    @registry.register(
        name="list_running_apps",
        description="List all visible running applications on macOS.",
        parameters={"type": "object", "properties": {}, "required": []},
        tier=Tier.AUTO,
    )
    def list_running_apps() -> str:
        script = 'tell application "System Events" to get name of every process whose background only is false'
        result = _run_osascript(script, timeout=10)
        if result.startswith("Error"):
            # Fallback via ps
            try:
                ps = subprocess.run(
                    ["ps", "-axco", "comm"],
                    capture_output=True, text=True, timeout=5,
                    env=_safe_subprocess_env(),
                )
                apps = sorted(set(ps.stdout.strip().splitlines()[1:]))
                return ", ".join(apps[:60])
            except Exception:
                return result
        return result

    @registry.register(
        name="type_text",
        description="Type text into the currently focused macOS app (simulates keyboard input).",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["text"],
        },
        tier=Tier.NOTIFY,
    )
    def type_text(text: str) -> str:
        # Escape for AppleScript
        safe = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{safe}"'
        result = _run_osascript(script)
        if result.startswith("Error"):
            return result
        return f"Typed: {text[:60]}{'...' if len(text) > 60 else ''}"

    @registry.register(
        name="press_keys",
        description=(
            "Press a keyboard shortcut in the focused app. "
            "Format: 'cmd+c', 'cmd+shift+4', 'return', 'escape', 'tab', 'space', 'up', 'down', etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "Key combination, e.g. 'cmd+c', 'cmd+shift+s'"},
            },
            "required": ["keys"],
        },
        tier=Tier.NOTIFY,
    )
    def press_keys(keys: str) -> str:
        # Parse "cmd+shift+c" → AppleScript keystroke ... using {command down, shift down}
        parts = [p.strip().lower() for p in keys.split("+")]

        mod_map = {
            "cmd": "command down", "command": "command down",
            "ctrl": "control down", "control": "control down",
            "opt": "option down", "option": "option down", "alt": "option down",
            "shift": "shift down",
            "fn": "function down",
        }
        key_map = {
            "return": "return", "enter": "return", "escape": "escape", "esc": "escape",
            "tab": "tab", "space": "space", "delete": "delete", "backspace": "delete",
            "up": "up arrow", "down": "down arrow", "left": "left arrow", "right": "right arrow",
            "home": "home", "end": "end", "pageup": "page up", "pagedown": "page down",
            "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f5": "F5",
            "f6": "F6", "f7": "F7", "f8": "F8", "f9": "F9", "f10": "F10",
            "f11": "F11", "f12": "F12",
        }

        modifiers = []
        key_char = None

        for p in parts:
            if p in mod_map:
                modifiers.append(mod_map[p])
            else:
                key_char = key_map.get(p, p)

        if not key_char:
            return f"Error: no key found in '{keys}'"

        if key_char in key_map.values():
            # Special key
            mod_str = f" using {{{', '.join(modifiers)}}}" if modifiers else ""
            script = f'tell application "System Events" to key code (key code of key "{key_char}"){mod_str}'
            # Actually use key name directly
            mod_str2 = f" using {{{', '.join(modifiers)}}}" if modifiers else ""
            script = f'tell application "System Events" to key "{key_char}"{mod_str2}'
        else:
            mod_str = f" using {{{', '.join(modifiers)}}}" if modifiers else ""
            script = f'tell application "System Events" to keystroke "{key_char}"{mod_str}'

        result = _run_osascript(script)
        if result.startswith("Error"):
            return result
        return f"Pressed: {keys}"

    @registry.register(
        name="spotlight_search",
        description="Search for files, apps, or content using macOS Spotlight (mdfind).",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "kind": {"type": "string", "description": "Filter by kind: 'app', 'document', 'image', 'pdf', etc."},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["query"],
        },
        tier=Tier.AUTO,
    )
    def spotlight_search(query: str, kind: str = "", limit: int = 20) -> str:
        cmd = ["mdfind", "-onlyin", os.path.expanduser("~")]
        if kind:
            cmd += ["-name", query] if kind == "name" else [f"kMDItemKind == '*{kind}*'c && kMDItemDisplayName == '*{query}*'c"]
        else:
            cmd.append(query)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                env=_safe_subprocess_env(),
            )
            lines = [l for l in result.stdout.strip().splitlines() if l][:limit]
            if not lines:
                return f"No results for '{query}'"
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @registry.register(
        name="open_url",
        description="Open a URL in the default browser (or specify app).",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open"},
                "app": {"type": "string", "description": "App to open in (e.g. 'Safari', 'Google Chrome')"},
            },
            "required": ["url"],
        },
        tier=Tier.NOTIFY,
    )
    def open_url(url: str, app: str = "") -> str:
        try:
            cmd = ["open"]
            if app:
                cmd += ["-a", app]
            cmd.append(url)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                env=_safe_subprocess_env(),
            )
            if result.returncode != 0:
                return f"Error: {result.stderr.strip()}"
            target = f" in {app}" if app else ""
            return f"Opened{target}."
        except Exception as e:
            return f"Error: {e}"

    @registry.register(
        name="get_frontmost_app",
        description="Get the name and window title of the currently focused (frontmost) application.",
        parameters={"type": "object", "properties": {}, "required": []},
        tier=Tier.AUTO,
    )
    def get_frontmost_app() -> str:
        script = '''
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    set windowTitle to ""
    try
        set windowTitle to name of front window of frontApp
    end try
    return appName & " | " & windowTitle
end tell
'''
        return _run_osascript(script)

    @registry.register(
        name="control_brightness",
        description=(
            "Get or set macOS display brightness. Level is 0-100. "
            "Omit level to just read current brightness. "
            "Requires the 'brightness' CLI tool (brew install brightness) for setting; "
            "falls back to keyboard keys if not installed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Brightness 0-100 (omit to read current)"},
            },
            "required": [],
        },
        tier=Tier.NOTIFY,
    )
    def control_brightness(level: int = -1) -> str:
        import shutil

        if level < 0:
            # Read current brightness via ioreg
            try:
                r = subprocess.run(
                    ["ioreg", "-c", "IODisplayConnect", "-r", "-d", "1"],
                    capture_output=True, text=True, timeout=5, env=_safe_subprocess_env(),
                )
                for line in r.stdout.splitlines():
                    if "IODisplayBrightness" in line:
                        val = float(line.split("=")[-1].strip())
                        return f"Current brightness: {int(val * 100)}%"
            except Exception:
                pass
            return "Brightness read not available. Install 'brightness' via brew for full control."

        level = max(0, min(100, level))

        # Try 'brightness' CLI (brew install brightness)
        if shutil.which("brightness"):
            try:
                r = subprocess.run(
                    ["brightness", str(level / 100.0)],
                    capture_output=True, text=True, timeout=5, env=_safe_subprocess_env(),
                )
                if r.returncode == 0:
                    return f"Brightness set to {level}%."
            except Exception:
                pass

        # Fallback: use keyboard brightness keys via AppleScript
        # macOS brightness up key = F2 (key code 144), down = F1 (key code 145)
        # Strategy: press brightness-down 16 times to zero, then press brightness-up proportionally
        if level == 100:
            steps_down, steps_up = 16, 16
        elif level == 0:
            steps_down, steps_up = 16, 0
        else:
            steps_down = 16
            steps_up = round(level / 6.25)  # each step ≈ 6.25%

        script = f'''
tell application "System Events"
    repeat {steps_down} times
        key code 145  -- brightness down
    end repeat
    repeat {steps_up} times
        key code 144  -- brightness up
    end repeat
end tell
'''
        result = _run_osascript(script, timeout=15)
        if result.startswith("Error"):
            return (
                f"Could not set brightness automatically. "
                f"Install 'brightness' CLI: brew install brightness\n{result}"
            )
        return f"Brightness adjusted to ~{level}% (via keyboard keys). For precise control: brew install brightness"

    @registry.register(
        name="say_text",
        description="Use macOS text-to-speech to speak text aloud.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to speak"},
                "voice": {"type": "string", "description": "Voice name (e.g. 'Samantha', 'Alex', 'Daniel')"},
                "rate": {"type": "integer", "description": "Speech rate words-per-minute (default 200)"},
            },
            "required": ["text"],
        },
        tier=Tier.NOTIFY,
    )
    def say_text(text: str, voice: str = "", rate: int = 200) -> str:
        cmd = ["say", "-r", str(rate)]
        if voice:
            cmd += ["-v", voice]
        cmd.append(text)
        try:
            subprocess.Popen(cmd, env=_safe_subprocess_env())  # non-blocking
            return f"Speaking: {text[:80]}{'...' if len(text) > 80 else ''}"
        except Exception as e:
            return f"Error: {e}"
