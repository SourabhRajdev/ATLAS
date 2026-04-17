"""Privacy firewall — never look at sensitive apps or content."""

from __future__ import annotations

import re
import time
from pathlib import Path

# Apps we never screenshot, OCR, or describe
BLACKLISTED_APPS = {
    "1Password", "1Password 7", "1Password 8",
    "Keychain Access", "Passwords",
    "Bitwarden", "Dashlane", "LastPass",
    "Tor Browser",
    # Banking — common ones; user can extend via config
    "Chase", "Bank of America", "Robinhood", "Coinbase", "Wells Fargo",
}

# Path patterns we never read
BLACKLISTED_PATH_PATTERNS = [
    re.compile(r".*/\.ssh/.*"),
    re.compile(r".*/Library/Keychains/.*"),
    re.compile(r".*/\.env(\.|$).*"),
    re.compile(r".*\.pem$"),
    re.compile(r".*\.key$"),
    re.compile(r".*_rsa$"),
    re.compile(r".*_dsa$"),
    re.compile(r".*\.p12$"),
    re.compile(r".*/1Password.*"),
    re.compile(r".*/Bitwarden.*"),
]

# OCR scrubbing — strip anything that looks like a secret before storing
SECRET_LINE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"-----BEGIN [A-Z ]+KEY-----"),
    re.compile(r"(?i)password\s*[:=]"),
    re.compile(r"(?i)api[_ -]?key"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
]


def is_app_blacklisted(app_name: str) -> bool:
    return app_name in BLACKLISTED_APPS


def is_path_blacklisted(path: Path | str) -> bool:
    s = str(path)
    return any(p.match(s) for p in BLACKLISTED_PATH_PATTERNS)


def scrub_ocr(text: str) -> str:
    """Drop lines that look like they contain secrets."""
    if not text:
        return text
    out_lines = []
    for line in text.splitlines():
        if any(p.search(line) for p in SECRET_LINE_PATTERNS):
            out_lines.append("[redacted]")
            continue
        # Entropy heuristic — long token-like strings
        for tok in line.split():
            if len(tok) >= 32 and _looks_high_entropy(tok):
                line = line.replace(tok, "[redacted]")
        out_lines.append(line)
    return "\n".join(out_lines)


def _looks_high_entropy(s: str) -> bool:
    """Cheap entropy check — high ratio of unique chars + mixed case + digits."""
    if len(s) < 24:
        return False
    has_upper = any(c.isupper() for c in s)
    has_lower = any(c.islower() for c in s)
    has_digit = any(c.isdigit() for c in s)
    unique_ratio = len(set(s)) / len(s)
    return has_upper and has_lower and has_digit and unique_ratio > 0.5


# ---------- pause control ----------

class PrivacyGate:
    def __init__(self) -> None:
        self._paused_until: float = 0.0

    def pause(self, seconds: int) -> None:
        self._paused_until = max(self._paused_until, time.time() + seconds)

    @property
    def paused(self) -> bool:
        return time.time() < self._paused_until

    def remaining_seconds(self) -> int:
        return max(0, int(self._paused_until - time.time()))
