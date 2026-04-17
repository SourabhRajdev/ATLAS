"""Log + output redaction. Strip secrets before they hit disk."""

from __future__ import annotations

import logging
import re

# High-precision patterns. Order matters — most specific first.
_PATTERNS = [
    # API keys (provider-prefixed)
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-***"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "AIza***"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "xox-***"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "ghp_***"),
    (re.compile(r"gho_[A-Za-z0-9]{36}"), "gho_***"),
    (re.compile(r"glpat-[A-Za-z0-9_-]{20}"), "glpat-***"),
    # Bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE), "Bearer ***"),
    # AWS
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA***"),
    (re.compile(r"aws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{40}", re.IGNORECASE), "aws_secret=***"),
    # Generic high-entropy assignments
    (re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{12,})['\"]?"),
     r"\1=***"),
    # Private key blocks
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "-----PRIVATE KEY REDACTED-----"),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            try:
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a for a in record.args
                )
            except Exception:
                pass
        return True


def install_global_redaction() -> None:
    """Attach the redacting filter to the root logger."""
    root = logging.getLogger()
    f = RedactingFilter()
    if not any(isinstance(x, RedactingFilter) for x in root.filters):
        root.addFilter(f)
    for h in root.handlers:
        if not any(isinstance(x, RedactingFilter) for x in h.filters):
            h.addFilter(f)
