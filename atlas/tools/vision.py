"""Vision tools — screen capture + Moondream (local Ollama) for understanding.

Gives ATLAS eyes. Can read text, describe UI, identify apps/content,
answer questions about what's visible on screen.
"""

from __future__ import annotations

import base64
import logging
import subprocess
import tempfile
from pathlib import Path

import httpx

from atlas.core.models import Tier
from atlas.tools.registry import ToolRegistry

logger = logging.getLogger("atlas.tools.vision")


def _capture_screen(display: int = 1) -> bytes | None:
    """Capture screen to PNG bytes via screencapture."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        result = subprocess.run(
            ["screencapture", "-x", "-D", str(display), path],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            # Fallback: no display flag
            subprocess.run(["screencapture", "-x", path], check=True, timeout=5)
        return Path(path).read_bytes()
    except Exception as e:
        logger.warning("screencapture failed: %s", e)
        return None
    finally:
        Path(path).unlink(missing_ok=True)


def _capture_window() -> bytes | None:
    """Capture just the focused window."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        subprocess.run(
            ["screencapture", "-x", "-w", "-t", "png", path],
            timeout=5, check=True,
        )
        return Path(path).read_bytes()
    except Exception as e:
        logger.warning("window capture failed: %s", e)
        return None
    finally:
        Path(path).unlink(missing_ok=True)


async def _ask_moondream(
    image_bytes: bytes,
    question: str,
    base_url: str = "http://localhost:11434",
) -> str:
    """Send image to Moondream via Ollama /api/chat."""
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": "moondream",
        "messages": [{"role": "user", "content": question, "images": [b64]}],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data.get("message", {}).get("content", "").strip()


def register(registry: ToolRegistry, config=None) -> None:
    ollama_url = getattr(config, "ollama_base_url", "http://localhost:11434") if config else "http://localhost:11434"

    @registry.register(
        name="see_screen",
        description=(
            "Capture the screen and use Moondream AI to visually understand it. "
            "Can read text, describe UI, identify apps, understand content. "
            "Use when user asks 'what do you see', 'read the screen', 'what's on my screen', "
            "'what tab is open', 'what does my screen show', etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What to look for or ask about the screen. E.g. 'What app is open?', 'Read the text on screen', 'Describe what you see'",
                },
                "target": {
                    "type": "string",
                    "description": "What to capture: 'screen' (full, default) or 'window' (focused window only)",
                },
            },
            "required": ["question"],
        },
        tier=Tier.AUTO,
    )
    async def see_screen(question: str, target: str = "screen") -> str:
        if target == "window":
            image_bytes = _capture_window()
        else:
            image_bytes = _capture_screen()

        if not image_bytes:
            return "Could not capture screen. Check screen recording permissions in System Settings > Privacy."

        try:
            answer = await _ask_moondream(image_bytes, question, ollama_url)
            if not answer:
                return "Moondream returned empty response."
            return answer
        except httpx.ConnectError:
            return "Ollama not running. Start with: ollama serve"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return "Moondream model not found. Install with: ollama pull moondream"
            return f"Ollama error: {e}"
        except Exception as e:
            return f"Vision error: {type(e).__name__}: {e}"

    @registry.register(
        name="read_screen_text",
        description=(
            "Read and extract all visible text from the current screen. "
            "Good for reading articles, code, notifications, error messages."
        ),
        parameters={
            "type": "object",
            "properties": {
                "area": {
                    "type": "string",
                    "description": "Focus area hint: e.g. 'top', 'center', 'all' (default: all)",
                },
            },
            "required": [],
        },
        tier=Tier.AUTO,
    )
    async def read_screen_text(area: str = "all") -> str:
        image_bytes = _capture_screen()
        if not image_bytes:
            return "Could not capture screen."
        question = f"Extract and transcribe all visible text on screen. Area: {area}. Be thorough and accurate."
        try:
            return await _ask_moondream(image_bytes, question, ollama_url)
        except httpx.ConnectError:
            return "Ollama not running. Start with: ollama serve"
        except Exception as e:
            return f"Vision error: {e}"

    @registry.register(
        name="describe_screen",
        description=(
            "Give a detailed description of what's currently on screen — apps open, "
            "what the user is working on, layout, content. Use for context awareness."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        tier=Tier.AUTO,
    )
    async def describe_screen() -> str:
        image_bytes = _capture_screen()
        if not image_bytes:
            return "Could not capture screen."
        question = (
            "Describe this Mac screen in detail: what app is in focus, what content is visible, "
            "what the user appears to be doing, any important text or UI elements."
        )
        try:
            return await _ask_moondream(image_bytes, question, ollama_url)
        except httpx.ConnectError:
            return "Ollama not running. Start with: ollama serve"
        except Exception as e:
            return f"Vision error: {e}"
