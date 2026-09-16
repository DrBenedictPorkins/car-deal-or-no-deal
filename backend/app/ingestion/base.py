"""Transport abstraction.

A ``MessageSource`` produces ``RawMessage``; a ``MessageTransport`` consumes an
``OutboundMessage``. Nothing else about Gmail leaks past this file, which is what lets
the same negotiation logic run under all three test modes:

    UNIT    no transport at all — fixtures constructed in the test
    REPLAY  historical .eml files, replayed chronologically
    GMAIL   the real Gmail API against a dedicated account

Outbound has a hard safety layer that is *not* optional and *not* per-transport:
``SafeTransport`` wraps whatever is configured, so a new transport cannot accidentally
ship without the allowlist check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.config import Settings, get_settings
from app.ingestion import eml
from app.ingestion.messages import OutboundMessage, RawMessage, SentMessage


class SendRefused(RuntimeError):
    """Raised when a send is blocked. Carries the reason, verbatim, to the caller."""


def strip_body(message: RawMessage, snippet_chars: int = 200) -> RawMessage:
    """Drop the body, keeping a short preview.

    Local sources have the body to hand, but the inbox is metadata-only by
    contract — uniformly, so the replay path exercises the same discipline the
    Gmail path is bound by.
    """
    from dataclasses import replace

    preview = (message.snippet or message.best_body or "").strip().replace("\n", " ")
    return replace(
        message,
        body_text=None,
        body_html=None,
        raw_bytes=None,
        snippet=preview[:snippet_chars],
    )


@dataclass(frozen=True)
class SyncResult:
    messages: list[RawMessage]
    cursor: str | None
    exhausted: bool = True


@runtime_checkable
class MessageSource(Protocol):
    name: str

    def fetch_all(self, *, limit: int | None = None) -> list[RawMessage]:
        """Everything this source can see — the historical import."""

    def fetch_incremental(self, cursor: str | None) -> SyncResult:
        """Only what is new since ``cursor``."""

    def fetch_metadata(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> list[RawMessage]:
        """Headers and a snippet only — no bodies.

        The inbox sweep uses this so a broad date range can be scanned without
        the buyer's personal correspondence landing on disk. Bodies arrive only
        when a message is promoted.
        """

    def fetch_one(self, source_identifier: str) -> RawMessage | None:
        """One message in full, fetched at promote time."""


@runtime_checkable
class MessageTransport(Protocol):
    name: str

    def send(self, message: OutboundMessage) -> SentMessage: ...


# ------------------------------------------------------------------- sources


class EmlDirectorySource:
    """Reads .eml files from disk. Backs both UNIT fixtures and REPLAY corpora.

    ``fetch_incremental`` uses the message timestamp as its cursor, which makes the
    same incremental-sync code path testable without Gmail: drop another .eml into the
    directory and sync again.
    """

    def __init__(self, directory: Path, *, name: str = "replay") -> None:
        self.directory = Path(directory)
        self.name = name

    def _load(self) -> list[RawMessage]:
        if not self.directory.is_dir():
            return []
        return eml.load_directory(self.directory, source_system=self.name)

    def fetch_all(self, *, limit: int | None = None) -> list[RawMessage]:
        messages = self._load()
        return messages[:limit] if limit else messages

    def fetch_incremental(self, cursor: str | None) -> SyncResult:
        messages = self._load()
        if cursor:
            since = datetime.fromisoformat(cursor)
            messages = [m for m in messages if m.sent_at > since]
        newest = max((m.sent_at for m in messages), default=None)
        return SyncResult(
            messages=messages,
            cursor=newest.isoformat() if newest else cursor,
        )

    def fetch_metadata(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> list[RawMessage]:
        messages = [m for m in self._load() if since is None or m.sent_at >= since]
        return [strip_body(m) for m in (messages[:limit] if limit else messages)]

    def fetch_one(self, source_identifier: str) -> RawMessage | None:
        return next(
            (m for m in self._load() if m.source_identifier == source_identifier), None
        )


class StaticSource:
    """An in-memory list. UNIT mode, and the seam every test constructs by hand."""

    def __init__(self, messages: list[RawMessage], *, name: str = "fixture") -> None:
        self._messages = sorted(messages, key=lambda m: (m.sent_at, m.source_identifier))
        self.name = name

    def fetch_all(self, *, limit: int | None = None) -> list[RawMessage]:
        return self._messages[:limit] if limit else list(self._messages)

    def fetch_incremental(self, cursor: str | None) -> SyncResult:
        messages = list(self._messages)
        if cursor:
            since = datetime.fromisoformat(cursor)
            messages = [m for m in messages if m.sent_at > since]
        newest = max((m.sent_at for m in messages), default=None)
        return SyncResult(messages, newest.isoformat() if newest else cursor)

    def fetch_metadata(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> list[RawMessage]:
        messages = [m for m in self._messages if since is None or m.sent_at >= since]
        return [strip_body(m) for m in (messages[:limit] if limit else messages)]

    def fetch_one(self, source_identifier: str) -> RawMessage | None:
        return next(
            (m for m in self._messages if m.source_identifier == source_identifier), None
        )


# ---------------------------------------------------------------- transports


class NullTransport:
    """Refuses to send. The default, and what UNIT mode always gets."""

    name = "null"

    def send(self, message: OutboundMessage) -> SentMessage:
        raise SendRefused(
            "No outbound transport is configured. Approved drafts stay in the app; "
            "copy the text into your mail client to send it."
        )


class RecordingTransport:
    """Records instead of sending. Lets outbound flow be tested without a mailbox."""

    name = "recording"

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> SentMessage:
        self.sent.append(message)
        index = len(self.sent)
        return SentMessage(
            source_system=self.name,
            source_identifier=f"recorded-{index}",
            thread_identifier=message.thread_identifier or f"recorded-thread-{index}",
            rfc822_message_id=f"<recorded-{index}@dealbench.test>",
            sent_at=datetime.now(),
            to_addresses=message.to_addresses,
        )


class SafeTransport:
    """Allowlist enforcement in front of any transport.

    Wrapping rather than trusting each transport to check is deliberate: the failure
    this guards against is a future transport that forgets, and a forgotten check here
    means real email to a real dealership from a test run.
    """

    def __init__(self, inner: MessageTransport, settings: Settings | None = None) -> None:
        self.inner = inner
        self.settings = settings or get_settings()
        self.name = f"safe({inner.name})"

    def send(self, message: OutboundMessage) -> SentMessage:
        if not message.to_addresses:
            raise SendRefused("No recipient.")
        for recipient in (*message.to_addresses, *message.cc_addresses):
            allowed, reason = self.settings.sending_allowed_to(recipient)
            if not allowed:
                raise SendRefused(reason)
        return self.inner.send(message)


class DemoSource(StaticSource):
    """The reference negotiation, with its dates shifted to the recent past.

    Lets the inbox, the promote flow and the dashboard be exercised end to end
    with no Gmail account and no network — which matters because the whole point
    of the inbox view is that you look at it and click, and you cannot do that
    against an empty mailbox.
    """

    def __init__(self, *, ending_days_ago: int = 1) -> None:
        from datetime import timedelta

        from app.fixtures import golden_corpus

        messages = golden_corpus.build()
        latest = max(m.sent_at for m in messages)
        target = datetime.now().replace(microsecond=0) - timedelta(days=ending_days_ago)
        shift = target - latest

        from dataclasses import replace

        super().__init__(
            [replace(m, sent_at=m.sent_at + shift) for m in messages], name="demo"
        )


def get_transport(settings: Settings | None = None) -> MessageTransport:
    """The configured transport, always wrapped in the safety layer."""
    settings = settings or get_settings()
    if not settings.send_enabled:
        return NullTransport()
    if settings.ingest_mode.upper() == "GMAIL":
        from app.ingestion.gmail.transport import GmailTransport

        return SafeTransport(GmailTransport(settings=settings), settings)
    return SafeTransport(RecordingTransport(), settings)


def get_source(settings: Settings | None = None) -> MessageSource:
    """The configured inbound source for the current mode."""
    settings = settings or get_settings()
    mode = settings.ingest_mode.upper()
    if mode == "GMAIL":
        from app.ingestion.gmail.source import GmailSource

        return GmailSource(settings=settings)
    if mode == "REPLAY":
        return EmlDirectorySource(settings.data_dir / "replay")
    if mode == "DEMO":
        return DemoSource()
    return StaticSource([])
