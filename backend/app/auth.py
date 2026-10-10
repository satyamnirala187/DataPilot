"""Private demo access: one shared login, checked only by this backend.

POST /auth/login exchanges the shared demo username and password for a short-lived token, which
the frontend sends as "Authorization: Bearer <token>". POST /query refuses any request without a
valid one. There are no user accounts, no cookies (the frontend and the API are different sites,
where browsers block or partition third-party cookies) and no JWT library.

A token is  v1.<expiry>.<session id>.<signature>:
  - expiry      Unix time in whole seconds; the session ends then, however active it is
  - session id  random (secrets.token_urlsafe), so logout can revoke one session
  - signature   HMAC-SHA256 of "v1.<expiry>.<session id>" with DEMO_SESSION_SECRET, URL-safe base64
The token holds no username or password.

Logout revokes a session in this process's memory until the token would have expired anyway. A
restart forgets those revocations, so a logged-out token could work again until its expiry (at most
DEMO_SESSION_MINUTES). Changing DEMO_SESSION_SECRET ends every session at once.
"""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.config import settings

TOKEN_VERSION = "v1"
# The whole token, strictly: version, 1-12 digit expiry, 22-character session id, 43-character
# signature (32 bytes in unpadded URL-safe base64). Anything else is invalid without further parsing.
TOKEN_PATTERN = re.compile(r"v1\.([0-9]{1,12})\.([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{43})", re.ASCII)

# Login attempts per client, successful or not. No global lockout: anyone could trigger it.
LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60

clock = time.time  # wall-clock Unix time; tests replace it

# The one shared demo login is one account. Data that belongs to "whoever is signed in" (History,
# Saved Reports) is stored under this id, so it survives logout and new sessions: a session id is
# random per login and is never an owner. Not part of the token; every session has the same account.
DEMO_ACCOUNT_ID = "demo"


@dataclass(frozen=True)
class Session:
    session_id: str
    expires_at: int  # Unix time in seconds

    @property
    def account_id(self) -> str:
        return DEMO_ACCOUNT_ID


class AuthError(Exception):
    """A request is not signed in. kind (missing_token, invalid_token, expired_token,
    revoked_token, not_configured) is logged; the client only ever sees 401 unauthorized."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def is_configured() -> bool:
    return None not in (settings.demo_username, settings.demo_password, settings.demo_session_secret)


def credentials_match(username: str, password: str) -> bool:
    """Compare both values in constant time, always both, so neither timing nor the answer says
    which one was wrong. SHA-256 first gives equal-length inputs to compare_digest."""
    if not is_configured():
        return False
    username_ok = hmac.compare_digest(_digest(username), _digest(settings.demo_username.get_secret_value()))
    password_ok = hmac.compare_digest(_digest(password), _digest(settings.demo_password.get_secret_value()))
    return username_ok & password_ok


def issue_token() -> tuple[str, Session]:
    session = Session(secrets.token_urlsafe(16), int(clock()) + settings.demo_session_minutes * 60)
    unsigned = f"{TOKEN_VERSION}.{session.expires_at}.{session.session_id}"
    return f"{unsigned}.{_sign(unsigned)}", session


def verify_token(token: str) -> Session:
    """The session a token belongs to, or AuthError. The signature is checked before anything in
    the token is trusted."""
    if not is_configured():
        raise AuthError("not_configured")
    match = TOKEN_PATTERN.fullmatch(token)
    if match is None:
        raise AuthError("invalid_token")
    expiry, session_id, signature = match.groups()
    if not hmac.compare_digest(signature, _sign(f"{TOKEN_VERSION}.{expiry}.{session_id}")):
        raise AuthError("invalid_token")
    if int(expiry) <= clock():
        raise AuthError("expired_token")
    if revoked.is_revoked(session_id):
        raise AuthError("revoked_token")
    return Session(session_id, int(expiry))


def session_from_header(authorization: str | None) -> Session:
    if not authorization:
        raise AuthError("missing_token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("invalid_token")
    return verify_token(token.strip())


def require_session(request: Request) -> Session:
    """FastAPI dependency: the caller's session, or 401 before anything else runs."""
    try:
        return session_from_header(request.headers.get("authorization"))
    except AuthError as error:
        metrics = getattr(request.state, "query_metrics", None)
        if metrics is not None:
            metrics.fail("auth", error.kind)  # logged as stage=auth cause=<kind>
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"}) from None


class RevokedSessions:
    """Session ids ended by logout, each kept until its token would have expired anyway."""

    MAX_ENTRIES = 10_000  # bounds memory; logins are rate-limited, so this is far above real use

    def __init__(self):
        self._expiry_by_id: dict[str, int] = {}
        self._lock = threading.Lock()

    def revoke(self, session: Session) -> None:
        with self._lock:
            self._prune()
            if len(self._expiry_by_id) >= self.MAX_ENTRIES:
                # Drop the revocation closest to expiring: the least time left to misuse it.
                del self._expiry_by_id[min(self._expiry_by_id, key=self._expiry_by_id.get)]
            self._expiry_by_id[session.session_id] = session.expires_at

    def is_revoked(self, session_id: str) -> bool:
        with self._lock:
            self._prune()
            return session_id in self._expiry_by_id

    def _prune(self) -> None:
        now = clock()
        for session_id in [s for s, expiry in self._expiry_by_id.items() if expiry <= now]:
            del self._expiry_by_id[session_id]


revoked = RevokedSessions()


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _sign(unsigned: str) -> str:
    key = settings.demo_session_secret.get_secret_value().encode("utf-8")
    mac = hmac.new(key, unsigned.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")
