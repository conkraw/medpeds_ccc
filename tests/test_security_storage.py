"""Synthetic-only checks for data protection and lost-update prevention.

No test contacts GitHub, uses real secrets, or reads an uploaded resident file.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import sys
import threading
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

import pytest
import requests
from cryptography.fernet import Fernet, InvalidToken

from ccc import security, storage
from scripts import generate_secrets


@pytest.fixture(scope="module")
def password_hash():
    return security.hash_password("Synthetic only long passphrase 987!")


@pytest.fixture
def users(password_hash):
    return {"admin": {"password_hash": password_hash, "display_name": "Example Admin", "role": "admin"},
            "reader": {"password_hash": password_hash, "display_name": "Example Reader", "role": "viewer"}}


def test_argon2_hashes_are_salted_and_verified(password_hash):
    second = security.hash_password("Synthetic only long passphrase 987!")
    assert second != password_hash
    assert password_hash.startswith("$argon2id$")
    assert security.verify_password("Synthetic only long passphrase 987!", password_hash)
    assert not security.verify_password("the wrong passphrase", password_hash)


def test_authentication_returns_identity_without_credentials(users):
    identity = security.authenticate(" ADMIN ", "Synthetic only long passphrase 987!", users,
                                     security.LoginRateLimiter())
    assert identity["username"] == "admin"
    assert identity["role"] == "admin"
    assert "password_hash" not in identity and "password" not in identity
    assert security.identity_is_current(identity, users)
    for key, value in identity.items():
        assert users["admin"]["password_hash"] not in value


def test_unknown_disabled_and_wrong_password_have_same_result(users):
    limiter = security.LoginRateLimiter()
    assert security.authenticate("missing", "Synthetic only long passphrase 987!", users, limiter) is None
    assert security.authenticate("admin", "wrong", users, limiter) is None
    users["reader"]["enabled"] = False
    assert security.authenticate("reader", "Synthetic only long passphrase 987!", users, limiter) is None


@pytest.mark.parametrize("change", ["role", "enabled", "password_hash", "removed"])
def test_credential_changes_revoke_session(users, change):
    identity = security.authenticate("admin", "Synthetic only long passphrase 987!", users,
                                     security.LoginRateLimiter())
    if change == "removed":
        del users["admin"]
    elif change == "role":
        users["admin"]["role"] = "viewer"
    elif change == "enabled":
        users["admin"]["enabled"] = False
    else:
        users["admin"]["password_hash"] = security.hash_password("A changed synthetic long passphrase")
    assert not security.identity_is_current(identity, users)


@pytest.mark.parametrize("bad_hash", ["plaintext-password", "$argon2id$v=19$m=8,t=1,p=1$YXNk$YXNk"])
def test_weak_or_plaintext_credentials_fail_closed(users, bad_hash):
    users["admin"]["password_hash"] = bad_hash
    with pytest.raises(security.SecurityConfigError):
        security.validate_users(users)


def test_ambiguous_usernames_are_rejected(users):
    users["ADMIN"] = copy.deepcopy(users["admin"])
    with pytest.raises(security.SecurityConfigError):
        security.validate_users(users)


def test_rate_limit_normalizes_usernames_and_expires():
    now = [100.0]
    limiter = security.LoginRateLimiter(per_user_attempts=2, total_attempts=3,
                                        max_users=3, window_seconds=10,
                                        clock=lambda: now[0])
    limiter.acquire("ALICE")
    limiter.acquire(" alice ")
    with pytest.raises(security.AuthenticationRateLimited) as caught:
        limiter.acquire("alice")
    assert caught.value.retry_after_seconds == 10
    limiter.acquire("bob")
    with pytest.raises(security.AuthenticationRateLimited):
        limiter.acquire("new_name")
    now[0] = 110.0
    limiter.acquire("alice")
    assert len(limiter._total) == 1
    assert len(limiter._users) == 1
    assert "alice" not in repr(limiter._users)


def test_rate_limiter_bounds_username_spraying():
    limiter = security.LoginRateLimiter(per_user_attempts=2, total_attempts=10, max_users=10)
    for i in range(10):
        limiter.acquire(f"test_user_{i}")
    for i in range(10, 100):
        with pytest.raises(security.AuthenticationRateLimited):
            limiter.acquire(f"test_user_{i}")
    assert len(limiter._users) == 10
    assert len(limiter._total) == 10


def test_parallel_attempts_cannot_race_the_limiter():
    limiter = security.LoginRateLimiter(per_user_attempts=5, total_attempts=100)
    barrier = threading.Barrier(20)

    def attempt(_):
        barrier.wait()
        try:
            limiter.acquire("example")
            return True
        except security.AuthenticationRateLimited:
            return False

    with ThreadPoolExecutor(max_workers=20) as pool:
        assert sum(pool.map(attempt, range(20))) == 5


def test_roles_default_deny():
    assert security.role_allows({"role": "viewer"}, "export")
    assert not security.role_allows({"role": "viewer"}, "upload")
    assert security.role_allows({"role": "reviewer"}, "review")
    assert not security.role_allows({"role": "reviewer"}, "restore")
    assert security.role_allows({"role": "admin"}, "restore")
    assert not security.role_allows({"role": "invented"}, "view")
    assert not security.role_allows({"role": "admin"}, "invented_action")


def test_setup_cli_rejects_arguments_without_echoing_them(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["generate_secrets.py", "synthetic-secret-in-argv"])
    assert generate_secrets.main() == 2
    captured = capsys.readouterr()
    assert "synthetic-secret-in-argv" not in captured.out + captured.err


def test_setup_helper_hides_password_and_emits_valid_toml(monkeypatch, capsys):
    import tomllib
    monkeypatch.setattr(sys, "argv", ["generate_secrets.py"])
    answers = iter(["setup", "admin", "Example Admin", "admin"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    secret = "A synthetic passphrase not printed 1234"
    hidden_calls = []

    def hidden_input(prompt):
        hidden_calls.append(prompt)
        return secret

    monkeypatch.setattr(generate_secrets.getpass, "getpass", hidden_input)
    assert generate_secrets.main() == 0
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    config = tomllib.loads(captured.out)
    assert len(hidden_calls) == 2
    assert config["users"]["admin"]["role"] == "admin"
    assert security.verify_password(secret, config["users"]["admin"]["password_hash"])
    assert len(config["encryption"]["key"]) == 44


def test_add_user_template_does_not_replace_encryption_key(password_hash):
    template = generate_secrets.build_template("reviewer", "Example", "reviewer", password_hash, None)
    assert "[encryption]" not in template
    assert "[github]" not in template


@pytest.fixture
def keys():
    return [Fernet.generate_key().decode("ascii")]


@pytest.fixture
def state():
    return {"schema_version": 1, "residents": [{"id": "example", "name": "Jordan Example"}],
            "reviews": [], "settings": {"program": "Synthetic Med-Peds"}}


def test_state_encryption_randomness_and_round_trip(keys, state):
    first = storage.encrypt_state(state, keys)
    second = storage.encrypt_state(state, keys)
    assert first != second
    assert b"Jordan Example" not in first and b"residents" not in first
    assert storage.decrypt_state(first, keys) == state


def test_new_state_payload_is_compressed_inside_authenticated_encryption(keys, state):
    token = storage.encrypt_state(state, keys)
    protected_plaintext = Fernet(keys[0]).decrypt(token)
    assert protected_plaintext.startswith(storage._STATE_PREFIX)
    envelope = json.loads(zlib.decompress(protected_plaintext[len(storage._STATE_PREFIX):]))
    assert envelope["state"] == state


def test_compressible_history_larger_than_prior_limit_roundtrips(keys):
    large_state = {"schema_version": 1, "history": "Synthetic repeated clinical education narrative. " * 190000}
    assert len(json.dumps(large_state)) > 8 * 1024 * 1024
    token = storage.encrypt_state(large_state, keys)
    assert len(token) < storage.MAX_STATE_CIPHERTEXT_BYTES
    assert storage.decrypt_state(token, keys) == large_state


def test_uncompressed_v1_backup_remains_readable(keys, state):
    envelope = {"format": "medpeds-ccc", "payload_type": "state", "version": 1, "state": state}
    legacy = Fernet(keys[0]).encrypt(json.dumps(envelope).encode("utf-8"))
    assert storage.decrypt_state(legacy, keys) == state


def test_compressed_size_limit_is_enforced_before_encryption(keys, state, monkeypatch):
    monkeypatch.setattr(storage, "MAX_COMPRESSED_STATE_BYTES", 10)
    with pytest.raises(storage.StorageError, match="compressed dashboard"):
        storage.encrypt_state(state, keys)


def test_authenticated_decompression_is_bounded(keys, monkeypatch):
    monkeypatch.setattr(storage, "MAX_STATE_BYTES", 100)
    envelope = {"format": "medpeds-ccc", "payload_type": "state", "version": 1,
                "state": {"schema_version": 1, "text": "x" * 10000}}
    packed = storage._STATE_PREFIX + zlib.compress(json.dumps(envelope).encode("utf-8"))
    token = Fernet(keys[0]).encrypt(packed)
    with pytest.raises(storage.StorageError, match="expanded dashboard"):
        storage.decrypt_state(token, keys)


@pytest.mark.parametrize("damage", ["truncated", "trailing", "second_stream", "invalid"])
def test_invalid_compression_streams_fail_closed(keys, state, damage):
    token = storage.encrypt_state(state, keys)
    packed = Fernet(keys[0]).decrypt(token)
    compressed = packed[len(storage._STATE_PREFIX):]
    if damage == "truncated":
        compressed = compressed[:-2]
    elif damage == "trailing":
        compressed += b"unexpected trailing bytes"
    elif damage == "second_stream":
        compressed += zlib.compress(b"another stream")
    else:
        compressed = b"not a zlib stream"
    authenticated_bad_stream = Fernet(keys[0]).encrypt(storage._STATE_PREFIX + compressed)
    with pytest.raises(storage.StorageError):
        storage.decrypt_state(authenticated_bad_stream, keys)


def test_wrong_key_and_modified_ciphertext_fail_closed(keys, state):
    token = storage.encrypt_state(state, keys)
    bad = bytearray(token)
    bad[len(bad) // 2] ^= 1
    for candidate, candidate_keys in ((bytes(bad), keys), (token, [Fernet.generate_key().decode("ascii")])):
        with pytest.raises(storage.StorageError) as caught:
            storage.decrypt_state(candidate, candidate_keys)
        assert "Jordan Example" not in str(caught.value)
        assert keys[0] not in str(caught.value)


def test_key_rotation_reads_old_and_writes_new(keys, state):
    old_token = storage.encrypt_state(state, keys)
    new_key = Fernet.generate_key().decode("ascii")
    rotated = [new_key] + keys
    assert storage.decrypt_state(old_token, rotated) == state
    new_token = storage.encrypt_state(state, rotated)
    assert storage.decrypt_state(new_token, [new_key]) == state
    with pytest.raises(InvalidToken):
        Fernet(keys[0]).decrypt(new_token)


@pytest.mark.parametrize("bad_state", [{}, {"schema_version": True}, {"schema_version": 2},
                                       {"schema_version": 1, "bad": float("nan")},
                                       {"schema_version": 1, "bad": b"raw bytes"},
                                       {"schema_version": 1, 5: "numeric key"}])
def test_invalid_states_are_not_encrypted(keys, bad_state):
    with pytest.raises(storage.StorageError):
        storage.encrypt_state(bad_state, keys)


def test_oversized_state_rejected_before_persistence(keys, monkeypatch):
    monkeypatch.setattr(storage, "MAX_STATE_BYTES", 80)
    with pytest.raises(storage.StorageError):
        storage.encrypt_state({"schema_version": 1, "notes": "x" * 100}, keys)


def test_backup_format_and_duplicate_keys_are_rejected(keys):
    plain = b'{"format":"medpeds-ccc","payload_type":"state","version":1,"state":{"schema_version":1,"schema_version":2}}'
    token = Fernet(keys[0]).encrypt(plain)
    with pytest.raises(storage.StorageError):
        storage.decrypt_state(token, keys)
    other = Fernet(keys[0]).encrypt(b'{"schema_version":1}')
    with pytest.raises(storage.StorageError):
        storage.decrypt_state(other, keys)


class FakeResponse:
    def __init__(self, status=200, obj=None, raw=None, headers=None):
        self.status_code = status
        self.raw_bytes = raw if raw is not None else json.dumps(obj or {}).encode("utf-8")
        self.headers = headers or {"Content-Length": str(len(self.raw_bytes))}
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.raw_bytes), chunk_size):
            yield self.raw_bytes[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class MemoryGitHub:
    """Small deterministic API double with immutable commit snapshots."""
    def __init__(self):
        self.files = {}
        self.calls = []
        self.private = True
        self.repo_status = 200
        self.branch_status = 200
        self.content_status = None
        self.put_status = None
        self.large_mode = False
        self.connection_error = False
        self.put_connection_error = False
        self.before_put = None
        self.on_raw = None
        self.metadata_override = None
        self.counter = 0
        self.snapshots = {}
        self.commit = self.snapshot()
        self.trust_env = True

    def snapshot(self):
        self.counter += 1
        commit = hashlib.sha1(f"synthetic-commit-{self.counter}".encode()).hexdigest()
        self.snapshots[commit] = dict(self.files)
        self.commit = commit
        return commit

    def seed(self, path, ciphertext):
        self.files[path] = ciphertext
        self.snapshot()

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert url.startswith("https://api.github.com/repos/example/private-data")
        assert kwargs["allow_redirects"] is False
        assert kwargs["verify"] is True
        assert kwargs["timeout"] == (5, 30)
        assert kwargs["stream"] is True
        if self.connection_error or (method == "PUT" and self.put_connection_error):
            raise requests.ConnectionError("synthetic-token-or-private-data-must-never-escape")
        suffix = url.removeprefix("https://api.github.com/repos/example/private-data")
        if suffix == "":
            return FakeResponse(self.repo_status, {"private": self.private, "full_name": "example/private-data"})
        if suffix == "/branches/main":
            return FakeResponse(self.branch_status, {"name": "main", "commit": {"sha": self.commit}})
        assert suffix.startswith("/contents/")
        path = unquote(suffix.removeprefix("/contents/"))
        if method == "GET":
            if self.content_status:
                return FakeResponse(self.content_status, {"message": "private-data-must-never-escape"})
            contents = self.snapshots[kwargs["params"]["ref"]]
            if path not in contents:
                return FakeResponse(404)
            blob = contents[path]
            if kwargs["headers"]["Accept"] == "application/vnd.github.raw+json":
                if self.on_raw:
                    self.on_raw(self, path)
                return FakeResponse(raw=blob)
            meta = {"type": "file", "path": path, "size": len(blob),
                    "sha": storage._git_blob_sha(blob),
                    "encoding": "none" if self.large_mode else "base64",
                    "content": "" if self.large_mode else base64.b64encode(blob).decode("ascii")}
            if self.metadata_override:
                meta.update(self.metadata_override)
            return FakeResponse(obj=meta)
        assert method == "PUT"
        if self.before_put:
            self.before_put(self, path)
            self.before_put = None
        payload = kwargs["json"]
        if self.put_status:
            return FakeResponse(self.put_status)
        current = self.files.get(path)
        current_sha = storage._git_blob_sha(current) if current is not None else None
        if payload.get("sha") != current_sha:
            return FakeResponse(422 if payload.get("sha") is None else 409)
        blob = base64.b64decode(payload["content"], validate=True)
        self.files[path] = blob
        self.snapshot()
        return FakeResponse(201 if current is None else 200,
                            {"content": {"path": path, "sha": storage._git_blob_sha(blob)}})

    def close(self):
        pass


@pytest.fixture
def github(monkeypatch):
    backend = MemoryGitHub()
    monkeypatch.setattr(storage.requests, "Session", lambda: backend)
    return backend


@pytest.fixture
def config():
    return {"owner": "example", "repo": "private-data", "branch": "main",
            "token": "github_pat_" + "SYNTHETIC_TEST_ONLY_" * 3, "path": "encrypted/ccc.enc"}


def test_missing_state_means_verified_private_repo_branch_and_path(github, config, keys):
    store = storage.GitHubStore(config, keys)
    assert store.load() == (None, None)
    assert len(github.calls) == 3
    assert github.calls[-1][2]["params"] == {"ref": github.commit}
    assert not any(method == "PUT" for method, _, _ in github.calls)
    assert not github.trust_env


def test_round_trip_stores_only_ciphertext_generic_names_and_messages(github, config, keys, state):
    store = storage.GitHubStore(config, keys)
    sha = store.save(state, None)
    assert storage._valid_sha(sha)
    assert store.load() == (state, sha)
    put = [call for call in github.calls if call[0] == "PUT"][0]
    payload = put[2]["json"]
    assert "sha" not in payload
    assert b"Jordan Example" not in github.files[config["path"]]
    assert "Jordan Example" not in put[1] + payload["message"]
    assert config["token"] not in put[1] and config["token"] not in repr(store)
    assert config["token"] not in repr(store)
    assert put[2]["headers"]["Authorization"] == f"Bearer {config['token']}"


def test_only_loaded_sha_can_update_an_existing_state(github, config, keys, state):
    store = storage.GitHubStore(config, keys)
    sha = store.save(state, None)
    newer = dict(state, note="a new synthetic review")
    new_sha = store.save(newer, sha)
    assert new_sha != sha
    with pytest.raises(storage.ConflictError):
        store.save(state, sha)
    with pytest.raises(storage.ConflictError):
        store.save(state, None)
    assert store.load() == (newer, new_sha)


@pytest.mark.parametrize("action", ["load", "save"])
def test_public_repository_is_rejected_before_data_access(github, config, keys, state, action):
    github.private = False
    store = storage.GitHubStore(config, keys)
    with pytest.raises(storage.StorageError, match="private"):
        store.load() if action == "load" else store.save(state, None)
    assert len(github.calls) == 1


@pytest.mark.parametrize("location,status", [("repo_status", 404), ("repo_status", 403),
                                            ("branch_status", 404), ("content_status", 403),
                                            ("content_status", 500)])
def test_access_and_service_failures_never_become_an_empty_database(github, config, keys, location, status):
    setattr(github, location, status)
    with pytest.raises(storage.StorageError):
        storage.GitHubStore(config, keys).load()


def test_network_errors_are_sanitized_and_never_empty(github, config, keys):
    github.connection_error = True
    with pytest.raises(storage.StorageError) as caught:
        storage.GitHubStore(config, keys).load()
    assert "synthetic-token-or-private-data" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_ambiguous_write_is_not_retried(github, config, keys, state):
    github.put_connection_error = True
    with pytest.raises(storage.SaveUnconfirmedError):
        storage.GitHubStore(config, keys).save(state, None)
    assert sum(call[0] == "PUT" for call in github.calls) == 1


def test_concurrent_creation_is_a_conflict_even_if_github_uses_422(github, config, keys, state):
    other = storage.encrypt_state(dict(state, note="another administrator"), keys)
    github.before_put = lambda backend, path: backend.seed(path, other)
    with pytest.raises(storage.ConflictError):
        storage.GitHubStore(config, keys).save(state, None)
    assert github.files[config["path"]] == other


def test_concurrent_update_is_a_conflict(github, config, keys, state):
    store = storage.GitHubStore(config, keys)
    sha = store.save(state, None)
    other = storage.encrypt_state(dict(state, note="another saved review"), keys)
    github.before_put = lambda backend, path: backend.seed(path, other)
    with pytest.raises(storage.ConflictError):
        store.save(state, sha)
    assert github.files[config["path"]] == other


def test_large_file_metadata_and_raw_are_read_at_same_immutable_commit(github, config, keys, state):
    old = storage.encrypt_state(state, keys)
    new = storage.encrypt_state(dict(state, note="newer data"), keys)
    github.seed(config["path"], old)
    old_commit = github.commit
    github.large_mode = True
    github.on_raw = lambda backend, path: backend.seed(path, new)
    loaded, sha = storage.GitHubStore(config, keys).load()
    assert loaded == state
    assert sha == storage._git_blob_sha(old)
    content_gets = [call for call in github.calls if "/contents/" in call[1]]
    assert all(call[2]["params"]["ref"] == old_commit for call in content_gets)
    assert github.files[config["path"]] == new


@pytest.mark.parametrize("overrides", [{"sha": "a" * 40}, {"size": 1}, {"type": "dir"},
                                      {"path": "some-other-file.enc"}, {"encoding": "unsupported"}])
def test_unexpected_metadata_is_rejected(github, config, keys, state, overrides):
    github.seed(config["path"], storage.encrypt_state(state, keys))
    github.metadata_override = overrides
    with pytest.raises(storage.StorageError):
        storage.GitHubStore(config, keys).load()


def test_decryption_error_in_existing_file_never_initializes_new_state(github, config, keys, state):
    token = storage.encrypt_state(state, [Fernet.generate_key().decode("ascii")])
    github.seed(config["path"], token)
    with pytest.raises(storage.StorageError, match="decrypted"):
        storage.GitHubStore(config, keys).load()
    assert github.files[config["path"]] == token
    assert not any(call[0] == "PUT" for call in github.calls)


@pytest.mark.parametrize("path", ["../secret.enc", "/absolute.enc", "a//b.enc", "a/../b.enc", "a/b.json",
                                  "a/name with spaces.enc", ".git/secret.enc", "a/b.enc?token=x"])
def test_unsafe_storage_paths_are_rejected(github, config, keys, path):
    config["path"] = path
    with pytest.raises(storage.StorageError):
        storage.GitHubStore(config, keys)
    assert not github.calls


def test_encrypted_originals_use_opaque_identifiers_and_are_immutable(github, config, keys):
    store = storage.GitHubStore(config, keys)
    identifier = uuid.uuid4().hex
    original = b"%PDF-1.7 synthetic resident content"
    path, sha = store.write_blob(identifier, original)
    assert path == f"encrypted/sources/{identifier}.enc"
    assert original not in github.files[path]
    assert store.read_blob(identifier) == original
    with pytest.raises(storage.ConflictError):
        store.write_blob(identifier, b"replacement")
    assert store.read_blob(identifier) == original
    assert storage._valid_sha(sha)


def test_retrying_identical_original_returns_existing_receipt_without_overwrite(github, config, keys):
    store = storage.GitHubStore(config, keys)
    identifier = uuid.uuid4().hex
    first = store.write_blob(identifier, b"same synthetic original")
    original_token = github.files[first[0]]
    repeated = store.write_blob(identifier, b"same synthetic original")
    assert repeated == first
    assert github.files[first[0]] == original_token
    assert sum(call[0] == "PUT" for call in github.calls) == 1


def test_original_retry_recovers_after_a_write_succeeded_but_its_response_was_lost(github, config, keys, monkeypatch):
    store = storage.GitHubStore(config, keys)
    identifier = uuid.uuid4().hex
    original_request = github.request
    lost = [False]

    def lose_response(method, url, **kwargs):
        response = original_request(method, url, **kwargs)
        if method == "PUT" and not lost[0]:
            lost[0] = True
            raise requests.ConnectionError("Synthetic lost response")
        return response

    monkeypatch.setattr(github, "request", lose_response)
    with pytest.raises(storage.SaveUnconfirmedError):
        store.write_blob(identifier, b"same synthetic original")
    path, sha = store.write_blob(identifier, b"same synthetic original")
    assert store.read_blob(identifier) == b"same synthetic original"
    assert sha == storage._git_blob_sha(github.files[path])
    assert sum(call[0] == "PUT" for call in github.calls) == 1


def test_swapped_valid_ciphertext_cannot_impersonate_another_source(github, config, keys):
    store = storage.GitHubStore(config, keys)
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    path, _ = store.write_blob(first, b"synthetic first original")
    other_path, _ = store.write_blob(second, b"synthetic second original")
    github.seed(other_path, github.files[path])
    with pytest.raises(storage.StorageError, match="does not match"):
        store.read_blob(second)


def test_missing_original_is_an_error(github, config, keys):
    with pytest.raises(storage.StorageError, match="unavailable"):
        storage.GitHubStore(config, keys).read_blob(uuid.uuid4().hex)


def test_named_original_identifier_and_oversized_upload_rejected(github, config, keys, monkeypatch):
    store = storage.GitHubStore(config, keys)
    with pytest.raises(storage.StorageError):
        store.write_blob("Jordan Example.pdf", b"example")
    monkeypatch.setattr(storage, "MAX_BLOB_BYTES", 10)
    with pytest.raises(storage.StorageError):
        store.write_blob(uuid.uuid4().hex, b"x" * 11)
    assert not github.calls


def test_actual_expanded_upload_limit_is_enforced(github, config, keys, state, monkeypatch):
    monkeypatch.setattr(storage, "MAX_GITHUB_UPLOAD_BYTES", 10)
    with pytest.raises(storage.StorageError, match="transfer size"):
        storage.GitHubStore(config, keys).save(state, None)
    assert not any(call[0] == "PUT" for call in github.calls)


def test_http_response_size_limit_closes_response():
    response = FakeResponse(raw=b"x" * 50)
    with pytest.raises(storage.StorageError):
        storage.GitHubStore._read_bytes(response, 10)
    assert response.closed
