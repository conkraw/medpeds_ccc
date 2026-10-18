"""Authenticated encryption and conservative GitHub persistence.

Plaintext JSON and original uploads exist only in memory in this module.
GitHub receives Fernet ciphertext in generic files in a public or private repository.
Reads never interpret an access, network, format, or decryption failure as an
empty database. Writes use the file SHA the user actually loaded, so two
sessions cannot silently replace one another's work. There are no retries of
ambiguous writes and no unsafe fallback store.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import threading
import uuid
import zlib
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import requests
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

MAX_STATE_BYTES = 64 * 1024 * 1024
MAX_COMPRESSED_STATE_BYTES = 8 * 1024 * 1024
MAX_BLOB_BYTES = 15 * 1024 * 1024
MAX_GITHUB_UPLOAD_BYTES = 28 * 1024 * 1024
_MAX_METADATA_BYTES = 2 * 1024 * 1024
_STATE_OVERHEAD = 1024
_STATE_PREFIX = b"MEDPEDS-CCC\x00STATE-ZLIB\x00v1\x00"
_BLOB_PREFIX = b"MEDPEDS-CCC\x00BLOB\x00v1\x00"
_SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_OWNER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")
_REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_PATH_PATTERN = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.enc\Z")


class StorageError(Exception):
    """A safe-to-display failure; never include token, payload, URL, or HTTP body."""


class ConflictError(StorageError):
    """The persisted version differs from the version the user reviewed."""


class SaveUnconfirmedError(StorageError):
    """The server may have accepted a write, but it could not be confirmed."""


def _ciphertext_limit(plaintext_limit: int) -> int:
    padded = (plaintext_limit // 16 + 1) * 16
    return 4 * math.ceil((57 + padded) / 3)


# Preserve the prior token bound so uncompressed v1 archives (at most8MiB
# plus their JSON envelope) remain readable. New writes carry at most8MiB
# compressed data and the short format prefix, both inside Fernet protection.
MAX_STATE_CIPHERTEXT_BYTES = _ciphertext_limit(MAX_COMPRESSED_STATE_BYTES + _STATE_OVERHEAD)
MAX_BLOB_CIPHERTEXT_BYTES = _ciphertext_limit(MAX_BLOB_BYTES + len(_BLOB_PREFIX) + 33)


def _cipher(keys: Sequence[str]) -> MultiFernet:
    if isinstance(keys, (str, bytes)) or not isinstance(keys, Sequence) or not 1 <= len(keys) <= 10:
        raise StorageError("Configure an encryption key in app secrets; old keys may be retained for reading.")
    fernets = []
    try:
        for key in keys:
            if not isinstance(key, str) or len(key) != 44:
                raise ValueError
            raw = key.encode("ascii")
            decoded = base64.b64decode(raw, altchars=b"-_", validate=True)
            if len(decoded) != 32 or base64.urlsafe_b64encode(decoded) != raw:
                raise ValueError
            fernets.append(Fernet(raw))
    except (ValueError, UnicodeError, binascii.Error, TypeError):
        raise StorageError("An encryption key is invalid. Use a generated Fernet key in app secrets.") from None
    return MultiFernet(fernets)


def _validate_json(value: Any, depth: int = 0) -> None:
    if depth > 40:
        raise StorageError("The saved data structure is too deeply nested.")
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise StorageError("Saved data must use text field names.")
        for item in value.values():
            _validate_json(item, depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item, depth + 1)
        return
    raise StorageError("Saved data contains an unsupported value. Review the import before saving.")


def validate_state(state: Any) -> None:
    """Basic storage schema validation; the application validates domain fields."""
    if not isinstance(state, dict) or type(state.get("schema_version")) is not int or state["schema_version"] != 1:
        raise StorageError("This data file has an unsupported format or version.")
    _validate_json(state)


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise StorageError("The data could not be prepared for encrypted storage.") from None


def _reject_duplicate_keys(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _encrypt_state(state: dict[str, Any], cipher: MultiFernet) -> bytes:
    validate_state(state)
    if len(_json_bytes(state)) > MAX_STATE_BYTES:
        raise StorageError("The dashboard data exceeds the 64 MiB processing limit. Contact the app owner before adding more history.")
    envelope = {"format": "medpeds-ccc", "payload_type": "state", "version": 1, "state": state}
    serialized = _json_bytes(envelope)
    compressor = zlib.compressobj(level=6)
    parts = []
    total = 0
    # Compress in chunks so incompressible input cannot cause an unbounded
    # intermediate upload. Resident feedback and frozen snapshots compress well.
    for offset in range(0, len(serialized), 1024 * 1024):
        part = compressor.compress(serialized[offset:offset + 1024 * 1024])
        total += len(part)
        if total > MAX_COMPRESSED_STATE_BYTES:
            raise StorageError("The compressed dashboard exceeds the 8 MiB storage limit. Contact the app owner before adding more history.")
        parts.append(part)
    final = compressor.flush()
    total += len(final)
    if total > MAX_COMPRESSED_STATE_BYTES:
        raise StorageError("The compressed dashboard exceeds the 8 MiB storage limit. Contact the app owner before adding more history.")
    parts.append(final)
    return cipher.encrypt(_STATE_PREFIX + b"".join(parts))


def encrypt_state(state: dict[str, Any], keys: Sequence[str]) -> bytes:
    """Create a portable encrypted backup with fresh randomness on every call."""
    return _encrypt_state(state, _cipher(keys))


def _decrypt_state(token: bytes, cipher: MultiFernet) -> dict[str, Any]:
    if not isinstance(token, bytes) or not token or len(token) > MAX_STATE_CIPHERTEXT_BYTES:
        raise StorageError("The encrypted data file is empty, invalid, or exceeds the storage limit.")
    try:
        # Authenticate before considering either compressed or legacy plaintext.
        plaintext = cipher.decrypt(token)
        if plaintext.startswith(_STATE_PREFIX):
            compressed = plaintext[len(_STATE_PREFIX):]
            if not compressed or len(compressed) > MAX_COMPRESSED_STATE_BYTES:
                raise ValueError
            decompressor = zlib.decompressobj()
            limit = MAX_STATE_BYTES + _STATE_OVERHEAD
            plaintext = decompressor.decompress(compressed, max_length=limit + 1)
            if len(plaintext) > limit or decompressor.unconsumed_tail:
                raise StorageError("The expanded dashboard exceeds the 64 MiB processing limit.")
            if not decompressor.eof or decompressor.unused_data:
                # Reject truncated streams, trailing bytes, or a second stream.
                raise ValueError
        elif len(plaintext) > MAX_COMPRESSED_STATE_BYTES + _STATE_OVERHEAD:
            # Backward compatibility is limited to the former v1 envelope size.
            raise ValueError
        envelope = json.loads(plaintext.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except InvalidToken:
        raise StorageError("The stored data could not be decrypted. Check the encryption keys; the file may have been altered.") from None
    except (ValueError, TypeError, UnicodeError, RecursionError, zlib.error):
        raise StorageError("The encrypted data has an unreadable format. No data has been replaced.") from None
    if (not isinstance(envelope, dict) or envelope.get("format") != "medpeds-ccc"
            or envelope.get("payload_type") != "state" or type(envelope.get("version")) is not int
            or envelope["version"] != 1 or set(envelope) != {"format", "payload_type", "version", "state"}):
        raise StorageError("This encrypted file is not a supported dashboard backup.")
    state = envelope["state"]
    validate_state(state)
    if len(_json_bytes(state)) > MAX_STATE_BYTES:
        raise StorageError("The dashboard data exceeds the 64 MiB processing limit.")
    return state


def decrypt_state(token: bytes, keys: Sequence[str]) -> dict[str, Any]:
    """Authenticate and validate a backup before returning its JSON data."""
    return _decrypt_state(token, _cipher(keys))


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA_PATTERN.fullmatch(value))


def _git_blob_sha(content: bytes, digest_length: int = 40) -> str:
    hasher = hashlib.sha1() if digest_length == 40 else hashlib.sha256()
    hasher.update(f"blob {len(content)}\0".encode("ascii"))
    hasher.update(content)
    return hasher.hexdigest()


def _blob_id(value: str) -> str:
    if not isinstance(value, str) or len(value) not in (32, 36):
        raise StorageError("An original-file identifier is invalid.")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise StorageError("An original-file identifier is invalid.") from None
    if parsed.version != 4 or value.lower() not in (parsed.hex, str(parsed)):
        raise StorageError("An original-file identifier must be a random UUID.")
    return parsed.hex


class GitHubStore:
    """Small encrypted store in one public or private GitHub repository.

    Config keys: owner, repo, branch, token, and optional path (encrypted/ccc.enc).
    Use a fine-grained personal access token restricted to this repository with
    Contents read/write permission. The API confirms actual access, not the
    permissions merely claimed by a config file. Repository visibility does
    not change encryption or access checks. No deletion is implemented.
    """

    def __init__(self, config: Mapping[str, Any], keys: Sequence[str]):
        if not isinstance(config, Mapping):
            raise StorageError("Configure encrypted storage in app secrets.")
        owner, repo = config.get("owner"), config.get("repo")
        branch, token = config.get("branch"), config.get("token")
        path = config.get("path", "encrypted/ccc.enc")
        if (not isinstance(owner, str) or not _OWNER_PATTERN.fullmatch(owner)
                or not isinstance(repo, str) or not _REPO_PATTERN.fullmatch(repo)
                or repo in (".", "..") or repo.endswith(".git")):
            raise StorageError("Configure a valid storage repository owner and name in app secrets.")
        if (not isinstance(branch, str) or not 1 <= len(branch) <= 200
                or not re.fullmatch(r"[A-Za-z0-9_.\-/]+", branch)
                or branch.startswith(("-", "/", ".")) or branch.endswith(("/", ".", ".lock"))
                or ".." in branch or "//" in branch or "/." in branch):
            raise StorageError("Configure an existing storage branch in app secrets.")
        if (not isinstance(token, str) or not token.startswith("github_pat_") or len(token) < 30
                or not re.fullmatch(r"[A-Za-z0-9_]+", token)):
            raise StorageError("Configure a fine-grained GitHub token with Contents read/write access in app secrets.")
        if (not isinstance(path, str) or len(path) > 200 or not _PATH_PATTERN.fullmatch(path)
                or path.startswith(".") or ".." in path):
            raise StorageError("Use a simple generic encrypted .enc file path in storage settings.")
        self._owner = owner
        self._repo = repo
        self._branch = branch
        self._path = path
        self._root = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        self._cipher = _cipher(keys)
        self._session = requests.Session()
        # Avoid implicit .netrc credentials and environment proxy overrides.
        self._session.trust_env = False
        self._headers = {"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2026-03-10",
                         "User-Agent": "medpeds-ccc-encrypted-store"}
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return "<GitHubStore: encrypted repository>"

    def close(self) -> None:
        self._session.close()

    def _request(self, method: str, suffix: str, *, raw: bool = False,
                 object_media: bool = False, **kwargs: Any) -> requests.Response:
        headers = dict(self._headers)
        if raw:
            headers["Accept"] = "application/vnd.github.raw+json"
        elif object_media:
            headers["Accept"] = "application/vnd.github.object+json"
        try:
            return self._session.request(method, self._root + suffix, headers=headers,
                                         timeout=(5, 30), allow_redirects=False,
                                         stream=True, verify=True, **kwargs)
        except requests.RequestException:
            if method == "PUT":
                raise SaveUnconfirmedError("The save could not be confirmed. Reload the latest saved data before trying again.") from None
            raise StorageError("Encrypted storage could not be reached. Try again when the connection is available.") from None

    @staticmethod
    def _check_status(response: requests.Response, allowed: tuple[int, ...], *, writing: bool = False) -> None:
        if response.status_code in allowed:
            return
        code = response.status_code
        response.close()
        if code == 409:
            raise ConflictError("Someone else saved a newer version. Reload the latest data and reapply your changes.")
        if code in (401, 403, 429):
            raise StorageError("Encrypted storage access was denied or temporarily limited. Check the storage token and permissions.")
        if code == 404:
            raise StorageError("The storage repository or branch is unavailable. Check its settings and access permissions.")
        if code == 422:
            raise StorageError("Storage rejected this save. Reload the latest data; if this repeats, check the branch rules and storage settings.")
        if writing and (code >= 500 or code in (408, 504)):
            raise SaveUnconfirmedError("The save could not be confirmed. Reload the latest saved data before trying again.")
        raise StorageError("Encrypted storage returned an unexpected response. Check service availability and storage settings.")

    @staticmethod
    def _read_bytes(response: requests.Response, limit: int, *, writing: bool = False) -> bytes:
        try:
            length = response.headers.get("Content-Length")
            if length is not None and (not str(length).isdigit() or int(length) > limit):
                raise StorageError("The storage response exceeds the allowed size or has an invalid length.")
            parts = []
            total = 0
            for part in response.iter_content(chunk_size=64 * 1024):
                total += len(part)
                if total > limit:
                    raise StorageError("The storage response exceeds the allowed size.")
                parts.append(part)
            return b"".join(parts)
        except requests.RequestException:
            if writing:
                raise SaveUnconfirmedError("The save could not be confirmed. Reload the latest saved data before trying again.") from None
            raise StorageError("The encrypted file could not be completely downloaded. Try loading it again.") from None
        finally:
            response.close()

    @classmethod
    def _read_json(cls, response: requests.Response, *, writing: bool = False) -> dict[str, Any]:
        raw = cls._read_bytes(response, _MAX_METADATA_BYTES, writing=writing)
        try:
            obj = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        except (ValueError, UnicodeError, RecursionError):
            if writing:
                raise SaveUnconfirmedError("The save response could not be read. Reload the latest saved data before trying again.") from None
            raise StorageError("Storage returned unreadable file information.") from None
        if not isinstance(obj, dict):
            raise StorageError("Storage returned unexpected file information.")
        return obj

    def _verify_repository(self) -> str:
        response = self._request("GET", "")
        self._check_status(response, (200,))
        repo = self._read_json(response)
        if type(repo.get("private")) is not bool:
            raise StorageError("The storage repository metadata could not be verified. No data has been replaced.")
        if str(repo.get("full_name", "")).lower() != f"{self._owner}/{self._repo}".lower():
            raise StorageError("Storage returned a different repository. Check the configured repository name.")
        response = self._request("GET", f"/branches/{quote(self._branch, safe='')}")
        self._check_status(response, (200,))
        branch = self._read_json(response)
        commit = branch.get("commit")
        if branch.get("name") != self._branch or not isinstance(commit, dict) or not _valid_sha(commit.get("sha")):
            raise StorageError("The storage branch could not be verified. No data has been replaced.")
        # Use a verified immutable commit for metadata and raw reads, so a
        # concurrent branch update cannot pair old data with a different SHA.
        return commit["sha"]

    def _metadata(self, path: str, commit: str, limit: int) -> dict[str, Any] | None:
        response = self._request("GET", f"/contents/{quote(path, safe='/')}",
                                 object_media=True, params={"ref": commit})
        if response.status_code == 404:
            response.close()
            return None
        self._check_status(response, (200,))
        meta = self._read_json(response)
        size = meta.get("size")
        if (meta.get("type") != "file" or meta.get("path") != path
                or not _valid_sha(meta.get("sha")) or type(size) is not int or size < 0 or size > limit):
            raise StorageError("The encrypted storage file has unexpected information or exceeds the size limit.")
        return meta

    def _read_file(self, path: str, commit: str, meta: dict[str, Any], limit: int) -> bytes:
        encoding = meta.get("encoding")
        if encoding == "base64":
            encoded = meta.get("content")
            if not isinstance(encoded, str) or len(encoded) > 4 * math.ceil(limit / 3) + 100000:
                raise StorageError("Storage returned an invalid encrypted file.")
            try:
                content = base64.b64decode("".join(encoded.split()).encode("ascii"), validate=True)
            except (ValueError, UnicodeError, binascii.Error):
                raise StorageError("Storage returned an unreadable encrypted file.") from None
        elif encoding == "none":
            response = self._request("GET", f"/contents/{quote(path, safe='/')}", raw=True,
                                     params={"ref": commit})
            self._check_status(response, (200,))
            content = self._read_bytes(response, limit)
        else:
            raise StorageError("Storage returned an unsupported file encoding.")
        if (len(content) != meta["size"] or len(content) > limit
                or _git_blob_sha(content, len(meta["sha"])) != meta["sha"]):
            raise StorageError("The encrypted file did not match its saved version. Try loading it again.")
        return content

    def load(self) -> tuple[dict[str, Any] | None, str | None]:
        """Return (None, None) only for a confirmed absent path in a valid store.

        A first-time administrator must explicitly initialize their state and
        call save(state, None). This method never creates a database by itself.
        """
        with self._lock:
            commit = self._verify_repository()
            meta = self._metadata(self._path, commit, MAX_STATE_CIPHERTEXT_BYTES)
            if meta is None:
                return None, None
            token = self._read_file(self._path, commit, meta, MAX_STATE_CIPHERTEXT_BYTES)
            return _decrypt_state(token, self._cipher), meta["sha"]

    def _write_file(self, path: str, ciphertext: bytes, expected_sha: str | None, *, message: str,
                    limit: int) -> str:
        if expected_sha is not None and not _valid_sha(expected_sha):
            raise StorageError("The saved version identifier is invalid. Reload the dashboard before saving.")
        if len(ciphertext) > limit:
            raise StorageError("The encrypted file exceeds its storage size limit.")
        commit = self._verify_repository()
        meta = self._metadata(path, commit, limit)
        actual_sha = meta["sha"] if meta else None
        if actual_sha != expected_sha:
            raise ConflictError("The saved version has changed. Reload the latest data and reapply your changes.")
        payload = {"message": message, "branch": self._branch,
                   "content": base64.b64encode(ciphertext).decode("ascii")}
        if expected_sha is not None:
            payload["sha"] = expected_sha
        # Check the actual expanded request, including Fernet and API base64.
        if len(_json_bytes(payload)) > MAX_GITHUB_UPLOAD_BYTES:
            raise StorageError("The encrypted upload exceeds the allowed transfer size.")
        response = self._request("PUT", f"/contents/{quote(path, safe='/')}", json=payload)
        if response.status_code == 422:
            # GitHub may report a concurrent *creation* as 422 rather than 409.
            # Inspect only fresh metadata; never display the service's body.
            response.close()
            current_commit = self._verify_repository()
            current = self._metadata(path, current_commit, limit)
            if (current["sha"] if current else None) != expected_sha:
                raise ConflictError("Someone else saved a newer version. Reload the latest data and reapply your changes.")
            raise StorageError("Storage rejected this save. Check the branch rules and token write permission.")
        self._check_status(response, (200, 201), writing=True)
        result = self._read_json(response, writing=True)
        content = result.get("content")
        if (not isinstance(content, dict) or content.get("path") != path
                or not _valid_sha(content.get("sha"))
                or content["sha"] != _git_blob_sha(ciphertext, len(content["sha"]))):
            raise SaveUnconfirmedError("The saved version could not be verified. Reload the latest saved data before trying again.")
        return content["sha"]

    def save(self, state: dict[str, Any], expected_sha: str | None) -> str:
        """Encrypt every write, and save only if the loaded file SHA still matches."""
        with self._lock:
            token = _encrypt_state(state, self._cipher)
            return self._write_file(self._path, token, expected_sha,
                                    message="Update encrypted application data",
                                    limit=MAX_STATE_CIPHERTEXT_BYTES)

    def _source_path(self, blob_id: str) -> tuple[str, str]:
        opaque = _blob_id(blob_id)
        path = str(PurePosixPath(self._path).parent / "sources" / f"{opaque}.enc")
        return opaque, path

    def write_blob(self, blob_id: str, data: bytes) -> tuple[str, str]:
        """Create an immutable encrypted source file under a random UUID name.

        Save the returned path, original filename, and UUID *inside* encrypted
        dashboard state. If the later state save fails, an encrypted orphan may
        remain; the caller must not report the whole import as saved. Retrying
        a UUID is idempotent only when its authenticated existing content is
        byte-identical. Existing source objects are never overwritten.
        """
        if not isinstance(data, bytes) or len(data) > MAX_BLOB_BYTES:
            raise StorageError("Original files must be bytes and no larger than 15 MiB each.")
        with self._lock:
            opaque, path = self._source_path(blob_id)
            plaintext = _BLOB_PREFIX + opaque.encode("ascii") + b"\0" + data
            token = self._cipher.encrypt(plaintext)
            try:
                sha = self._write_file(path, token, None, message="Add encrypted source file",
                                       limit=MAX_BLOB_CIPHERTEXT_BYTES)
            except ConflictError:
                # A prior attempt may have saved this original before a later
                # archive/state write failed. Read and authenticate the existing
                # UUID before treating the repeat as success; never overwrite.
                commit = self._verify_repository()
                meta = self._metadata(path, commit, MAX_BLOB_CIPHERTEXT_BYTES)
                if meta is None:
                    raise
                existing = self._read_file(path, commit, meta, MAX_BLOB_CIPHERTEXT_BYTES)
                try:
                    recovered = self._cipher.decrypt(existing)
                except InvalidToken:
                    raise StorageError("The existing encrypted original could not be verified. No file was overwritten.") from None
                if not hmac.compare_digest(recovered, plaintext):
                    raise ConflictError("This original-file identifier already belongs to different content. Review the upload again before saving.") from None
                sha = meta["sha"]
            return path, sha

    def read_blob(self, blob_id: str) -> bytes:
        """Read and authenticate an archived original without local disk storage."""
        with self._lock:
            opaque, path = self._source_path(blob_id)
            commit = self._verify_repository()
            meta = self._metadata(path, commit, MAX_BLOB_CIPHERTEXT_BYTES)
            if meta is None:
                raise StorageError("This encrypted original file is unavailable. The dashboard data has not been changed.")
            token = self._read_file(path, commit, meta, MAX_BLOB_CIPHERTEXT_BYTES)
            try:
                plaintext = self._cipher.decrypt(token)
            except InvalidToken:
                raise StorageError("This original file could not be decrypted. Check the encryption keys or file integrity.") from None
            prefix = _BLOB_PREFIX + opaque.encode("ascii") + b"\0"
            if not plaintext.startswith(prefix) or len(plaintext) - len(prefix) > MAX_BLOB_BYTES:
                raise StorageError("This encrypted original does not match the requested file.")
            return plaintext[len(prefix):]
