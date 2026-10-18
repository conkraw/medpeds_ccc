"""Production Streamlit flows with fictional data and a memory-only fake store.

AppTest executes the actual app.py entry point, including login and role gates.
Every network request is blocked during these tests. No real token is needed.
"""

from __future__ import annotations

import copy
import hashlib
import io
from datetime import date
from pathlib import Path

import msoffcrypto
import pytest
import requests
from cryptography.fernet import Fernet
from streamlit.testing.v1 import AppTest

from ccc import model as m
from ccc import export_security, security, ui
from ccc.storage import ConflictError, StorageError

APP = Path(__file__).resolve().parents[1] / "app.py"
PASSWORD = "Fictional app test sign-in passphrase 567!"


def by_label(elements, label):
    matches = [element for element in elements if element.label == label]
    assert len(matches) == 1, f"Expected one {label!r}; available: {[e.label for e in elements]}"
    return matches[0]


def no_exceptions(app):
    assert not app.exception, [error.message for error in app.exception]


@pytest.fixture(scope="module")
def password_hash():
    return security.hash_password(PASSWORD)


class MemoryBackend:
    def __init__(self):
        initial = m.new_state("Fictional App Test Program")
        initial = m.save_resident(initial, {"id": "resident-alex", "name": "Alex Fictional",
                                             "external_id": "NI-101", "start_date": "2026-07-01",
                                             "aliases": [], "status": "Active"}, "setup")
        self.destinations = {"private-data": initial, "alternate-data": m.new_state("Different Fictional Program")}
        self.shas = {name: hashlib.sha1(name.encode()).hexdigest() for name in self.destinations}
        self.constructed = []
        self.loads = []
        self.saves = []
        self.next_failure = None
        self.counter = 0

    def store_class(self):
        backend = self

        class FakeStore:
            def __init__(self, config, keys):
                self.destination = config["repo"]
                backend.constructed.append(self.destination)

            def load(self):
                backend.loads.append(self.destination)
                return copy.deepcopy(backend.destinations[self.destination]), backend.shas[self.destination]

            def save(self, state, expected_sha):
                if backend.next_failure is not None:
                    exc, backend.next_failure = backend.next_failure, None
                    raise exc
                if expected_sha != backend.shas[self.destination]:
                    raise ConflictError("Another synthetic session saved first.")
                backend.counter += 1
                sha = hashlib.sha1(f"synthetic-save-{backend.counter}".encode()).hexdigest()
                backend.destinations[self.destination] = copy.deepcopy(state)
                backend.shas[self.destination] = sha
                backend.saves.append((self.destination, copy.deepcopy(state)))
                return sha

            def write_blob(self, *_args, **_kwargs):
                raise AssertionError("These app checks do not persist original files.")

            def close(self):
                pass

        return FakeStore


@pytest.fixture
def app_factory(monkeypatch, password_hash):
    backend = MemoryBackend()
    monkeypatch.setattr(ui, "GitHubStore", backend.store_class())
    monkeypatch.setattr(ui, "_today", lambda _state=None: date(2026, 10, 17))
    monkeypatch.setattr(security, "DEFAULT_RATE_LIMITER", security.LoginRateLimiter())

    def reject_network(*args, **kwargs):
        raise AssertionError("A test attempted a real network request.")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    ephemeral_key = Fernet.generate_key().decode("ascii")
    config = {
        "users": {role: {"display_name": f"Fictional {role}", "role": role, "password_hash": password_hash}
                  for role in ("admin", "reviewer", "viewer")},
        "github": {"owner": "example", "repo": "private-data", "branch": "main",
                   "path": "encrypted/ccc.enc", "token": "github_pat_SYNTHETIC_NOT_A_REAL_TOKEN_123456"},
        "encryption": {"key": ephemeral_key, "previous_keys": []},
        "app": {"session_timeout_minutes": 30, "max_session_hours": 8},
    }

    def create(*, configured=True):
        app = AppTest.from_file(APP, default_timeout=15)
        if configured:
            app.secrets.update(copy.deepcopy(config))
        app.run()
        no_exceptions(app)
        return app

    return create, backend


def login(app, role="admin", password=PASSWORD):
    by_label(app.text_input, "Username").set_value(role)
    by_label(app.text_input, "Password").set_value(password)
    by_label(app.button, "Sign in").click().run()
    no_exceptions(app)
    return app


def navigate(app, page):
    by_label(app.radio, "Go to").set_value(page).run()
    no_exceptions(app)
    return app


def enter_new_resident(app):
    navigate(app, "Administration")
    by_label(app.text_input, "Resident name").set_value("Casey Fictional")


def test_missing_configuration_and_unsigned_session_never_open_storage(app_factory):
    create, backend = app_factory
    missing = create(configured=False)
    assert any("Setup" in item.value for item in missing.info)
    configured = create()
    assert by_label(configured.button, "Sign in")
    assert backend.constructed == [] and backend.loads == []
    assert "state" not in configured.session_state


def test_wrong_password_never_constructs_or_loads_the_store(app_factory):
    create, backend = app_factory
    app = login(create(), password="Wrong synthetic passphrase")
    assert backend.constructed == [] and backend.loads == []
    assert "identity" not in app.session_state
    assert any("could not be verified" in error.value for error in app.error)
    assert by_label(app.text_input, "Password").value == ""
    assert app.session_state.get("login_password", "") == ""


def test_successful_login_loads_saved_state_and_refresh_restores_server_version(app_factory):
    create, backend = app_factory
    app = login(create())
    assert backend.loads == ["private-data"]
    assert app.session_state["identity"]["username"] == "admin"
    assert app.session_state["state"]["residents"][0]["name"] == "Alex Fictional"
    backend.destinations["private-data"]["settings"]["program_name"] = "Updated by another fictional session"
    by_label(app.button, "Refresh saved data").click().run()
    no_exceptions(app)
    assert len(backend.loads) == 2
    assert app.session_state["state"]["settings"]["program_name"] == "Updated by another fictional session"


@pytest.mark.parametrize("role,can_upload,can_admin,can_review", [
    ("viewer", False, False, False), ("reviewer", False, False, True), ("admin", True, True, True)])
def test_roles_control_navigation_and_review_actions(app_factory, role, can_upload, can_admin, can_review):
    create, _backend = app_factory
    app = login(create(), role)
    navigation = by_label(app.radio, "Go to")
    assert ("Upload files" in navigation.options) is can_upload
    assert ("Administration" in navigation.options) is can_admin
    navigate(app, "Resident review")
    assert any(button.label == "Save draft" for button in app.button) is can_review
    assert any(button.label == "Finalize review" for button in app.button) is can_review
    if not can_admin:
        # Request a destination through the app's own navigation mechanism;
        # AppTest correctly rejects injecting an invalid radio option itself.
        app.session_state["nav_target"] = "Administration"
        app.run()
        no_exceptions(app)
        assert by_label(app.radio, "Go to").value == "Resident review"
        assert not any(button.label == "Save resident" for button in app.button)


def test_admin_roster_save_is_persisted_and_available_to_a_new_session(app_factory):
    create, backend = app_factory
    app = login(create())
    enter_new_resident(app)
    by_label(app.button, "Save resident").click().run()
    no_exceptions(app)
    assert len(backend.saves) == 1
    assert {r["name"] for r in app.session_state["state"]["residents"]} == {"Alex Fictional", "Casey Fictional"}
    added = next(r for r in app.session_state["state"]["residents"] if r["name"] == "Casey Fictional")
    assert added["external_id"] == ""
    assert added["id"]
    assert added["id"] != "Casey Fictional"
    reopened = login(create(), "viewer")
    assert {r["name"] for r in reopened.session_state["state"]["residents"]} == {"Alex Fictional", "Casey Fictional"}
    assert next(r for r in reopened.session_state["state"]["residents"] if r["name"] == "Casey Fictional")["id"] == added["id"]


def test_reviewer_saves_draft_then_finalizes_its_evidence_snapshot(app_factory):
    create, backend = app_factory
    app = login(create(), "reviewer")
    navigate(app, "Resident review")
    by_label(app.text_area, "CCC assessment or decision").set_value("A synthetic committee decision for testing.")
    by_label(app.button, "Save draft").click().run()
    no_exceptions(app)
    saved = app.session_state["state"]["reviews"][0]
    assert saved["status"] == "Draft"
    by_label(app.selectbox, "Start or open a review").set_value(saved["id"]).run()
    no_exceptions(app)
    by_label(app.checkbox, "I reviewed the evidence and this is the CCC's recorded assessment").check()
    by_label(app.button, "Finalize review").click().run()
    no_exceptions(app)
    final = app.session_state["state"]["reviews"][0]
    assert final["status"] == "Final" and isinstance(final["snapshot"], list)
    assert final["finalized_by"] == "reviewer"
    assert len(backend.saves) == 2


def test_default_word_export_is_password_protected_and_decryptable(app_factory, monkeypatch):
    create, backend = app_factory
    app = login(create(), "viewer")
    navigate(app, "Word reports")
    assert by_label(app.radio, "Download protection").value == "Password-protected Word"
    export_password = "A separate synthetic Word export passphrase 345!"
    by_label(app.text_input, "Export passphrase").set_value(export_password)
    by_label(app.text_input, "Confirm export passphrase").set_value(export_password)
    by_label(app.button, "Prepare Word download").click().run()
    no_exceptions(app)
    generated = app.session_state["generated_export"]
    assert generated["protected"] is True
    assert "Alex" not in generated["filename"]
    assert generated["data"].startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    office = msoffcrypto.OfficeFile(io.BytesIO(generated["data"]))
    office.load_key(password=export_password, verify_password=True)
    plain = io.BytesIO()
    office.decrypt(plain, verify_integrity=True)
    assert plain.getvalue().startswith(b"PK")
    assert not backend.saves
    assert by_label(app.text_input, "Export passphrase").value == ""
    assert by_label(app.text_input, "Confirm export passphrase").value == ""
    assert app.session_state.get("export_password", "") == ""
    assert app.session_state.get("export_confirmation", "") == ""

    # A failure preparing a second download must discard the earlier download
    # and clear both password widgets, including their server-side named state.
    def fail_protection(_docx, _password):
        raise export_security.ExportSecurityError("Synthetic protection failure for this test.")

    monkeypatch.setattr(export_security, "protect_docx", fail_protection)
    second_password = "Synthetic second export passphrase that must fail 456!"
    by_label(app.text_input, "Export passphrase").set_value(second_password)
    by_label(app.text_input, "Confirm export passphrase").set_value(second_password)
    by_label(app.button, "Prepare Word download").click().run()
    no_exceptions(app)
    assert "generated_export" not in app.session_state
    assert not app.download_button
    assert any("Synthetic protection failure" in error.value for error in app.error)
    assert by_label(app.text_input, "Export passphrase").value == ""
    assert by_label(app.text_input, "Confirm export passphrase").value == ""
    assert app.session_state.get("export_password", "") == ""
    assert app.session_state.get("export_confirmation", "") == ""


def test_failed_save_preserves_saved_state_and_conflict_recovery_is_available(app_factory):
    create, backend = app_factory
    app = login(create())
    saved_before = copy.deepcopy(app.session_state["state"])
    enter_new_resident(app)
    backend.next_failure = ConflictError("Another synthetic session saved first.")
    by_label(app.button, "Save resident").click().run()
    no_exceptions(app)
    assert app.session_state["state"] == saved_before
    assert backend.destinations["private-data"] == saved_before
    assert app.session_state["pending_save"]["state"]["residents"][-1]["name"] == "Casey Fictional"
    app.run()
    no_exceptions(app)
    assert not any(button.label == "Retry this save" for button in app.button)
    assert by_label(app.button, "Reload saved records").disabled
    assert by_label(app.download_button, "Download encrypted pending changes")
    by_label(app.checkbox, "I have kept a copy of any changes I need").check().run()
    no_exceptions(app)
    by_label(app.button, "Reload saved records").click().run()
    no_exceptions(app)
    assert "pending_save" not in app.session_state
    assert app.session_state["state"] == saved_before


def test_recoverable_storage_error_can_retry_without_losing_pending_changes(app_factory):
    create, backend = app_factory
    app = login(create())
    enter_new_resident(app)
    backend.next_failure = StorageError("Synthetic temporary storage outage.")
    by_label(app.button, "Save resident").click().run()
    no_exceptions(app)
    app.run()
    no_exceptions(app)
    by_label(app.button, "Retry this save").click().run()
    no_exceptions(app)
    assert "pending_save" not in app.session_state
    assert any(r["name"] == "Casey Fictional" for r in app.session_state["state"]["residents"])
    assert len(backend.saves) == 1


def test_sign_out_clears_resident_data_and_returns_to_the_login_gate(app_factory):
    create, backend = app_factory
    app = login(create())
    app.session_state["upload_preview"] = [{"data": b"synthetic private source"}]
    app.session_state["generated_export"] = {"data": b"synthetic private report"}
    by_label(app.button, "Sign out").click().run()
    no_exceptions(app)
    for key in ("state", "sha", "identity", "upload_preview", "generated_export", "encryption_keys"):
        assert key not in app.session_state
    assert by_label(app.button, "Sign in")
    assert backend.loads == ["private-data"]


def test_storage_destination_change_invalidates_the_old_session_database(app_factory):
    create, backend = app_factory
    app = login(create())
    app.session_state["upload_preview"] = [{"data": b"synthetic private source"}]
    app.secrets["github"]["repo"] = "alternate-data"
    app.run()
    no_exceptions(app)
    assert app.session_state["state"]["settings"]["program_name"] == "Different Fictional Program"
    assert app.session_state["state"]["residents"] == []
    assert "upload_preview" not in app.session_state
    assert backend.loads == ["private-data", "alternate-data"]
    assert backend.saves == []
