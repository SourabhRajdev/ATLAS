"""Entry point — wire everything together and start ATLAS."""

from __future__ import annotations

import asyncio
import logging
import sys

from atlas.config import Settings
from atlas.core.redact import install_global_redaction
from atlas.memory.store import MemoryStore
from atlas.tools.registry import ToolRegistry
from atlas.tools import filesystem, system, web, memory_tools, github, vision


def _setup_logging(config: Settings) -> None:
    config.ensure_dirs()
    install_global_redaction()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(config.log_path),
            logging.StreamHandler(sys.stderr) if config.log_level == "DEBUG" else logging.NullHandler(),
        ],
    )


def _build_tools(memory: MemoryStore, config=None) -> ToolRegistry:
    registry = ToolRegistry()
    filesystem.register(registry)
    system.register(registry)
    web.register(registry, config)
    memory_tools.register(registry, memory)
    github.register(registry, config)
    vision.register(registry, config)
    try:
        from atlas.tools import browser
        browser.register(registry)
    except ImportError:
        pass
    return registry


def _check_voice_deps() -> None:
    """Warn if voice deps are missing (non-fatal)."""
    missing = []
    for pkg in ("pyaudio", "faster_whisper", "numpy"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[dim]Voice deps missing: {', '.join(missing)}. Run: pip install -e \".[voice]\" to enable /voice[/dim]", flush=True)


def main() -> None:
    try:
        config = Settings()  # type: ignore[call-arg]
    except Exception as e:
        print(f"Config error: {e}")
        print("Make sure GEMINI_API_KEY is set (env var, .env, or macOS Keychain)")
        sys.exit(1)

    _setup_logging(config)
    config.ensure_dirs()

    memory = MemoryStore(config.db_path)
    tools = _build_tools(memory, config)

    try:
        from atlas.interfaces.cli import run_cli
        asyncio.run(run_cli(config, memory, tools))
    except KeyboardInterrupt:
        pass
    finally:
        memory.close()


if __name__ == "__main__":
    main()
