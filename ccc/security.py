"""Named-user authentication without Streamlit or any persistent credential cache.

Keep ``users`` in Streamlit Secrets, never in the repository. The module-level
limiter is shared by all sessions in one Python process, and is deliberately
bounded. It does not coordinate multiple replicas or survive a process restart.
The application is responsible for HTTPS, session expiry, and clearing session
data at logout. No passwords, hashes, or attempted usernames are logged here.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Mapping
from typing import Any, Callable

from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError

ROLES = frozenset({"admin", "reviewer", "viewer"})
MIN_PASSWORD_LENGTH = 14
MAX_PASSWORD_LENGTH = 1024
USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{1,63}\Z")
_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4,
                         hash_len=32, salt_len=16, type=Type.ID)
# Bound simultaneous 64 MiB verification jobs across Streamlit sessions.
_HASH_SLOTS = threading.BoundedSemaphore(2)
_DUMMY_HASH: str | None = None
_DUMMY_LOCK = threading.Lock()


class SecurityConfigError(ValueError):
    """Invalid secrets configuration; messages contain no credential values."""


class AuthenticationRateLimited(Exception):
    """A login attempt was rejected before password verification."""

    def __init__(self, retry_after_seconds: float):
        self.retry_after_seconds = max(1, math.ceil(retry_after_seconds))
        super().__init__(
            f"Too many sign-in attempts. Try again in {self.retry_after_seconds} seconds."
        )


def normalize_username(username: str) -> str:
    """Normalize only ASCII account names; an invalid input has no valid name."""
    if not isinstance(username, str) or len(username) > 128:
        return ""
    candidate = username.strip().lower()
    return candidate if USERNAME_PATTERN.fullmatch(candidate) else ""


class LoginRateLimiter:
    """Atomic, bounded sliding-window attempt limits, including successful logins.

    Counting *before* verification closes the concurrent-attempt race. A global
    cap also prevents username spraying from evicting useful per-user counters.
    No counter is cleared on a successful login. An attacker cannot use their
    own valid account to repeatedly reset the process-wide budget.
    """

    def __init__(self, *, per_user_attempts: int = 5, total_attempts: int = 100,
                 window_seconds: float = 900, max_users: int = 1024,
                 clock: Callable[[], float] = time.monotonic):
        if (per_user_attempts < 1 or total_attempts < per_user_attempts
                or window_seconds <= 0 or max_users < total_attempts):
            raise ValueError("Invalid sign-in rate limit configuration.")
        self.per_user_attempts = per_user_attempts
        self.total_attempts = total_attempts
        self.window_seconds = float(window_seconds)
        self.max_users = max_users
        self._clock = clock
        self._users: OrderedDict[str, deque[float]] = OrderedDict()
        self._total: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, username: str) -> None:
        """Record an allowed attempt, or raise AuthenticationRateLimited."""
        # Invalid names share a counter; do not retain arbitrary input in memory.
        normalized = normalize_username(username) or "<invalid>"
        user_key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        with self._lock:
            now = self._clock()
            cutoff = now - self.window_seconds
            while self._total and self._total[0] <= cutoff:
                self._total.popleft()
            for key in list(self._users):
                attempts = self._users[key]
                while attempts and attempts[0] <= cutoff:
                    attempts.popleft()
                if not attempts:
                    del self._users[key]
            attempts = self._users.get(user_key, deque())
            retries = []
            if len(self._total) >= self.total_attempts:
                retries.append(self._total[0] + self.window_seconds - now)
            if len(attempts) >= self.per_user_attempts:
                retries.append(attempts[0] + self.window_seconds - now)
            if retries:
                raise AuthenticationRateLimited(max(retries))
            # Because max_users >= total_attempts, an active user cannot be
            # evicted during the window before the process-wide cap is reached.
            if user_key not in self._users and len(self._users) >= self.max_users:
                raise AuthenticationRateLimited(self.window_seconds)
            attempts.append(now)
            self._users[user_key] = attempts
            self._users.move_to_end(user_key)
            self._total.append(now)


DEFAULT_RATE_LIMITER = LoginRateLimiter()


def _validate_password_hash(password_hash: Any) -> str:
    if not isinstance(password_hash, str) or not 40 <= len(password_hash) <= 512:
        raise SecurityConfigError("Every user needs an Argon2id password hash in app secrets.")
    try:
        params = extract_parameters(password_hash)
    except (InvalidHashError, ValueError, TypeError):
        raise SecurityConfigError("A user password hash is invalid. Run the setup helper again.") from None
    # Permit the documented default and modest compatible tuning. Reject weak
    # hashes and unexpectedly large costs before asking Argon2 to allocate RAM.
    if (params.type != Type.ID or params.version != 19
            or not 19456 <= params.memory_cost <= 131072
            or not 2 <= params.time_cost <= 5
            or not 1 <= params.parallelism <= 4
            or params.salt_len < 16 or params.hash_len < 32):
        raise SecurityConfigError("A user password hash needs current Argon2id settings. Run the setup helper.")
    return password_hash


def validate_users(users: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate named users and return a normalized copy of the secrets mapping."""
    if not isinstance(users, Mapping) or not users or len(users) > 500:
        raise SecurityConfigError("Configure at least one named user in app secrets.")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_user in users.items():
        name = normalize_username(raw_name)
        if not name or name in normalized or not isinstance(raw_user, Mapping):
            raise SecurityConfigError("User names in app secrets must be distinct, simple account names.")
        role = raw_user.get("role")
        display_name = raw_user.get("display_name")
        enabled = raw_user.get("enabled", True)
        if role not in ROLES or not isinstance(enabled, bool):
            raise SecurityConfigError("Each user needs an admin, reviewer, or viewer role.")
        if (not isinstance(display_name, str) or not display_name.strip()
                or len(display_name) > 120 or any(ord(c) < 32 for c in display_name)):
            raise SecurityConfigError("Each user needs a short display name in app secrets.")
        password_hash = _validate_password_hash(raw_user.get("password_hash"))
        normalized[name] = {"password_hash": password_hash,
                            "display_name": display_name.strip(),
                            "role": role, "enabled": enabled}
    return normalized


def hash_password(password: str) -> str:
    """Generate a salted Argon2id hash; the caller must obtain input with getpass."""
    if (not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH
            or not password.strip()):
        raise ValueError(f"Use a passphrase of {MIN_PASSWORD_LENGTH}–{MAX_PASSWORD_LENGTH} characters.")
    with _HASH_SLOTS:
        return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password without returning or logging hash-library exceptions."""
    _validate_password_hash(password_hash)
    if not isinstance(password, str) or not 1 <= len(password) <= MAX_PASSWORD_LENGTH:
        return False
    try:
        with _HASH_SLOTS:
            return bool(_HASHER.verify(password_hash, password))
    except (VerificationError, InvalidHashError):
        return False


def _dummy_hash() -> str:
    global _DUMMY_HASH
    with _DUMMY_LOCK:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password(secrets.token_urlsafe(48))
        return _DUMMY_HASH


def _credential_version(username: str, user: Mapping[str, Any]) -> str:
    content = "\0".join((username, user["password_hash"], user["role"], user["display_name"],
                          str(user["enabled"])))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def authenticate(username: str, password: str, users: Mapping[str, Any],
                 limiter: LoginRateLimiter | None = None) -> dict[str, str] | None:
    """Return a session-safe identity or None; bad credentials share one outcome.

    Invalid configuration raises SecurityConfigError and rate limits raise
    AuthenticationRateLimited. Do not store ``password`` in session state.
    """
    accounts = validate_users(users)
    (limiter or DEFAULT_RATE_LIMITER).acquire(username)
    name = normalize_username(username)
    user = accounts.get(name)
    candidate_hash = user["password_hash"] if user and user["enabled"] else _dummy_hash()
    verified = verify_password(password, candidate_hash)
    if not verified or user is None or not user["enabled"]:
        return None
    return {"username": name, "display_name": user["display_name"], "role": user["role"],
            "credential_version": _credential_version(name, user)}


def identity_is_current(identity: Mapping[str, Any], users: Mapping[str, Any]) -> bool:
    """Revoke existing sessions after a password, role, name, or enablement change."""
    if not isinstance(identity, Mapping):
        return False
    accounts = validate_users(users)
    name = identity.get("username")
    if not isinstance(name, str):
        return False
    user = accounts.get(name)
    supplied = identity.get("credential_version")
    return bool(user and user["enabled"] and isinstance(supplied, str)
                and identity.get("role") == user["role"]
                and hmac.compare_digest(supplied, _credential_version(name, user)))


_ROLE_ACTIONS = {
    "viewer": frozenset({"view", "export"}),
    "reviewer": frozenset({"view", "export", "review"}),
    "admin": frozenset({"view", "export", "review", "upload", "manage", "backup", "restore"}),
}


def role_allows(identity: Mapping[str, Any], action: str) -> bool:
    """Default-deny action check; validate the session identity before calling."""
    if not isinstance(identity, Mapping):
        return False
    return action in _ROLE_ACTIONS.get(identity.get("role"), frozenset())
