"""Logging with credential redaction.

OAuth tokens must never reach a log file. This filter is applied to the root logger at
startup rather than relying on every call site to remember.
"""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"((?:access|refresh|id)_token\"?\s*[:=]\s*\"?)[^\"\s,&]+", re.IGNORECASE),
    re.compile(r"(client_secret\"?\s*[:=]\s*\"?)[^\"\s,&]+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\"?\s*[:=]\s*\"?)[^\"\s,&]+", re.IGNORECASE),
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(
                redact(a) if isinstance(a, str) else a for a in record.args
            ) if isinstance(record.args, tuple) else record.args
        return True


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


def configure(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )
    redactor = RedactingFilter()
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(redactor)
