"""Gmail OAuth 2.0 and token storage.

No password is ever requested, stored, or accepted — the installed-app flow with a
loopback redirect is the whole authentication story. Scopes are requested at the
narrowest level the current configuration needs: read-only until sending is explicitly
enabled, and ``gmail.send`` never implicitly.

The token is encrypted at rest with a key from the OS keyring where one is available,
falling back to a 0600 key file with a warning. Neither the token nor the key is ever
logged; ``app.logging_config`` redacts token-shaped strings globally as a second layer.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings

log = logging.getLogger("dealbench.gmail")

KEYRING_SERVICE = "dealbench"
KEYRING_USER = "gmail-token-key"


class GmailUnavailable(RuntimeError):
    """The Google client libraries are not installed."""


def require_google_libraries() -> None:
    try:  # pragma: no cover - exercised only when the extra is installed
        import google.auth  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        import googleapiclient  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise GmailUnavailable(
            "Gmail support needs the optional dependencies. Install them with:\n"
            "    pip install -e '.[gmail]'\n"
            "Replay and unit modes do not require them."
        ) from exc


# ------------------------------------------------------------------ key material


def _load_or_create_key(settings: Settings) -> bytes:
    """Fernet key from the OS keyring, or a 0600 file if there is no keyring."""
    try:  # pragma: no cover - depends on the host
        import keyring

        stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if stored:
            return stored.encode()
        key = _new_key()
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key.decode())
        return key
    except Exception:  # keyring missing, locked, or headless
        pass

    path = settings.key_path
    if path.exists():
        return path.read_bytes().strip()

    key = _new_key()
    path.write_bytes(key)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    log.warning(
        "No OS keyring available; the Gmail token key is in %s with 0600 permissions. "
        "Anyone who can read your home directory can read it.",
        path,
    )
    return key


def _new_key() -> bytes:
    try:
        from cryptography.fernet import Fernet

        return Fernet.generate_key()
    except ImportError:  # pragma: no cover
        return base64.urlsafe_b64encode(os.urandom(32))


@dataclass
class TokenStore:
    """Encrypted credential storage on local disk."""

    settings: Settings

    @property
    def path(self) -> Path:
        return self.settings.token_path

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.path.write_bytes(self._encrypt(raw))
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        # Deliberately no token content in this message.
        log.info("Stored Gmail credentials (encrypted) at %s", self.path)

    def load(self) -> dict | None:
        if not self.exists():
            return None
        try:
            return json.loads(self._decrypt(self.path.read_bytes()))
        except Exception:
            log.warning(
                "Could not read the stored Gmail credentials at %s — re-authorize.",
                self.path,
            )
            return None

    def clear(self) -> None:
        if self.exists():
            self.path.unlink()

    # -- crypto ------------------------------------------------------------
    def _fernet(self):
        from cryptography.fernet import Fernet

        return Fernet(_load_or_create_key(self.settings))

    def _encrypt(self, raw: bytes) -> bytes:
        try:
            return b"FERNET1:" + self._fernet().encrypt(raw)
        except ImportError:  # pragma: no cover
            log.warning(
                "The 'cryptography' package is not installed, so the Gmail token is "
                "stored unencrypted at 0600. Install the gmail extra to encrypt it."
            )
            return b"PLAIN1:" + raw

    def _decrypt(self, blob: bytes) -> bytes:
        if blob.startswith(b"FERNET1:"):
            return self._fernet().decrypt(blob[len(b"FERNET1:") :])
        if blob.startswith(b"PLAIN1:"):
            return blob[len(b"PLAIN1:") :]
        return blob  # pragma: no cover - pre-format file


# ------------------------------------------------------------------ the flow


def required_scopes(settings: Settings) -> list[str]:
    """The narrowest scope set for the current configuration.

    Read-only is the baseline. Sending and inserting are each added only when the
    corresponding setting is on, so an installation that never sends never holds a
    credential that could.
    """
    scopes = list(settings.gmail_readonly_scopes)
    if settings.send_enabled:
        scopes.append(settings.gmail_send_scope)
    if settings.gmail_allow_insert:
        scopes.append(settings.gmail_insert_scope)
    return scopes


def get_credentials(settings: Settings | None = None, *, interactive: bool = True):
    """Load, refresh, or obtain Gmail credentials.

    ``interactive=False`` refuses to open a browser, which is what a scheduled sync
    wants: it should fail loudly rather than silently block on a consent screen.
    """
    require_google_libraries()
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    settings = settings or get_settings()
    scopes = required_scopes(settings)
    store = TokenStore(settings)

    payload = store.load()
    credentials = Credentials.from_authorized_user_info(payload, scopes) if payload else None

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        store.save(json.loads(credentials.to_json()))
        return credentials

    if not interactive:
        raise GmailUnavailable(
            "No valid Gmail credentials and interactive authorization is disabled. "
            "Run `python -m app.cli gmail auth` first."
        )

    secret_file = settings.gmail_client_secret_file
    if not secret_file or not Path(secret_file).exists():
        raise GmailUnavailable(
            "Set DEALBENCH_GMAIL_CLIENT_SECRET_FILE to the OAuth client secret JSON "
            "downloaded from Google Cloud (Desktop app credentials). This app never "
            "asks for a Gmail password."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), scopes)
    credentials = flow.run_local_server(port=0, prompt="consent")
    store.save(json.loads(credentials.to_json()))
    return credentials
