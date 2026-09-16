"""Gmail as a MessageSource: historical import and incremental sync.

Two sync paths, because Gmail gives two and they fail differently:

* ``users.messages.list`` with a query — the historical import. Bounded by a configured
  maximum so a first run against a large mailbox cannot pull the whole thing.
* ``users.history.list`` from a stored ``historyId`` — the incremental path. Gmail
  expires history cursors after about a week, so an expired cursor falls back to a
  bounded date-window scan rather than silently returning nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.config import Settings, get_settings
from app.ingestion.base import SyncResult
from app.ingestion.gmail import auth, client
from app.ingestion.messages import RawMessage

log = logging.getLogger("dealbench.gmail")


class GmailSource:
    name = "gmail"

    def __init__(self, settings: Settings | None = None, service=None) -> None:
        self.settings = settings or get_settings()
        self._service = service

    # -- plumbing ----------------------------------------------------------
    @property
    def service(self):  # pragma: no cover - needs live credentials
        if self._service is None:
            self._service = client.build_service(auth.get_credentials(self.settings))
        return self._service

    def _get_message(self, message_id: str) -> RawMessage:
        payload = (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return client.to_raw_message(payload)

    def profile(self) -> dict:
        return self.service.users().getProfile(userId="me").execute()

    # -- historical --------------------------------------------------------
    def fetch_all(self, *, limit: int | None = None, query: str | None = None) -> list[RawMessage]:
        query = query if query is not None else self.settings.gmail_import_query
        cap = limit or self.settings.gmail_import_max_messages

        ids: list[str] = []
        page_token = None
        while len(ids) < cap:
            response = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=query or None,
                    maxResults=min(500, cap - len(ids)),
                    pageToken=page_token,
                )
                .execute()
            )
            ids.extend(item["id"] for item in response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        log.info("Gmail historical import: %d message(s) matched %r", len(ids), query)
        return sorted(
            (self._get_message(message_id) for message_id in ids[:cap]),
            key=lambda m: m.sent_at,
        )

    def preview(self, *, limit: int = 25, query: str | None = None) -> list[dict]:
        """Headers only, for a dry run before anything is written.

        A first import against a personal mailbox is the one irreversible-feeling step
        in the whole application, so it gets a look-before-you-leap path.
        """
        query = query if query is not None else self.settings.gmail_import_query
        response = (
            self.service.users()
            .messages()
            .list(userId="me", q=query or None, maxResults=limit)
            .execute()
        )
        out = []
        for item in response.get("messages", []):
            payload = (
                self.service.users()
                .messages()
                .get(
                    userId="me",
                    id=item["id"],
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
                .execute()
            )
            message = client.to_raw_message(payload)
            out.append(
                {
                    "id": message.source_identifier,
                    "from": message.from_address,
                    "subject": message.subject,
                    "sent_at": message.sent_at.isoformat(),
                }
            )
        return out

    # -- metadata-only sweep ----------------------------------------------
    def fetch_metadata(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> list[RawMessage]:
        """Headers and Gmail's own snippet. No bodies leave the mailbox.

        This is what lets the inbox sweep cover a wide date range: the buyer
        sees enough to recognise a message, and nothing they have not claimed is
        ever written to disk.
        """
        query = self.settings.gmail_import_query or ""
        if since is not None:
            query = f"{query} after:{since.strftime('%Y/%m/%d')}".strip()
        cap = limit or self.settings.gmail_import_max_messages

        ids: list[str] = []
        page_token = None
        while len(ids) < cap:
            response = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=query or None,
                    maxResults=min(500, cap - len(ids)),
                    pageToken=page_token,
                )
                .execute()
            )
            ids.extend(item["id"] for item in response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        out: list[RawMessage] = []
        for message_id in ids[:cap]:
            payload = (
                self.service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=[
                        "From", "To", "Cc", "Subject", "Date", "Message-ID",
                        "In-Reply-To", "References", "Delivered-To",
                    ],
                )
                .execute()
            )
            out.append(client.to_raw_message(payload))
        log.info("Inbox sweep: %d message(s) matched %r", len(out), query)
        return sorted(out, key=lambda m: m.sent_at)

    def fetch_one(self, source_identifier: str) -> RawMessage | None:
        """One message in full. Called when the buyer promotes it."""
        payload = (
            self.service.users()
            .messages()
            .get(userId="me", id=source_identifier, format="full")
            .execute()
        )
        return client.to_raw_message(payload)

    # -- incremental -------------------------------------------------------
    def fetch_incremental(self, cursor: str | None) -> SyncResult:
        if not cursor:
            messages = self.fetch_all()
            newest = max((m.history_id for m in messages if m.history_id), default=None)
            return SyncResult(messages, newest)

        try:
            return self._history_since(cursor)
        except Exception as exc:  # expired or invalid cursor
            log.warning(
                "Gmail history cursor %s unusable (%s); falling back to a date-window "
                "scan. Duplicates are filtered by the ingestion pipeline.",
                cursor,
                exc.__class__.__name__,
            )
            return self._window_scan()

    def _history_since(self, cursor: str) -> SyncResult:
        message_ids: list[str] = []
        page_token = None
        newest = cursor
        while True:
            response = (
                self.service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=cursor,
                    historyTypes=["messageAdded"],
                    pageToken=page_token,
                )
                .execute()
            )
            newest = str(response.get("historyId") or newest)
            for record in response.get("history", []):
                for added in record.get("messagesAdded", []):
                    message_ids.append(added["message"]["id"])
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        unique = list(dict.fromkeys(message_ids))
        messages = sorted(
            (self._get_message(message_id) for message_id in unique), key=lambda m: m.sent_at
        )
        return SyncResult(messages, newest)

    def _window_scan(self, days: int = 14) -> SyncResult:
        """Bounded rescan used when the history cursor has expired."""
        after = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
        query = f"{self.settings.gmail_import_query} after:{after}".strip()
        messages = self.fetch_all(query=query)
        newest = max((m.history_id for m in messages if m.history_id), default=None)
        return SyncResult(messages, newest, exhausted=False)
