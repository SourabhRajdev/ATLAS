"""Secrets via macOS Keychain. Never read .env at runtime.

Usage:
    security add-generic-password -a atlas -s GEMINI_API_KEY -w "AIza..."

Then in code:
    key = get_secret("GEMINI_API_KEY")
"""

from __future__ import annotations

import logging
import os
import subprocess
from functools import lru_cache

logger = logging.getLogger("atlas.secrets")

KEYCHAIN_ACCOUNT = "atlas"


@lru_cache(maxsize=64)
def get_secret(name: str, default: str | None = None) -> str | None:
    """Fetch a secret from macOS Keychain. Falls back to env var.

    Order of precedence:
      1. macOS Keychain (security CLI)
      2. Environment variable (for CI / non-mac)
      3. default
    """
    # Try keychain first
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-a", KEYCHAIN_ACCOUNT, "-s", name, "-w"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # not on mac or security CLI absent

    # Env fallback
    val = os.environ.get(name) or os.environ.get(f"ATLAS_{name}")
    if val:
        return val

    return default


def set_secret(name: str, value: str) -> bool:
    """Store a secret in Keychain. Idempotent (overwrites)."""
    try:
        subprocess.run(
            ["security", "add-generic-password",
             "-a", KEYCHAIN_ACCOUNT, "-s", name, "-w", value, "-U"],
            check=True, capture_output=True, timeout=2,
        )
        get_secret.cache_clear()
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error("Failed to store secret %s: %s", name, e)
        return False


def delete_secret(name: str) -> None:
    try:
        subprocess.run(
            ["security", "delete-generic-password",
             "-a", KEYCHAIN_ACCOUNT, "-s", name],
            check=False, capture_output=True, timeout=2,
        )
        get_secret.cache_clear()
    except FileNotFoundError:
        pass
