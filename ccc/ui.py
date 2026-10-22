"""Small, task-based Streamlit interface for the coordinator and CCC."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import re
import time
import zipfile
from collections import Counter
from pathlib import Path
from datetime import date, datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ccc import __version__
from ccc import model as m
from ccc.security import (authenticate, identity_is_current, role_allows,
                          validate_users, SecurityConfigError, AuthenticationRateLimited)
from ccc.storage import (GitHubStore, StorageError, ConflictError,
                         SaveUnconfirmedError, encrypt_state, decrypt_state)


_REVIEW_TEXT_LABELS = {
    **m.REVIEW_TEXT_FIELDS,
    "duty_max_hours_4weeks": "Maximum average weekly hours over a 28-day window",
}


def _clear_session() -> None:
    for key in list(st.session_state):
        del st.session_state[key]


def _escape(value: object) -> str:
    """Display uploaded text literally in Markdown-capable widget labels."""
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>$-])", r"\\\1", str(value))


def _resident_labels(residents: list[dict]) -> dict[str, str]:
    counts = Counter(m.normalize_name(r["name"]) for r in residents)
    return {
        r["id"]: (
            r["name"] if counts[m.normalize_name(r["name"])] == 1
            else f"{r['name']} · started {r.get('start_date', '')} · {r['id'][:8]}"
        ) for r in residents
    }


def _today(state: dict | None = None) -> date:
    zone = (state or {}).get("settings", {}).get("timezone", "America/New_York")
    try:
        return datetime.now(ZoneInfo(zone)).date()
    except (ValueError, KeyError):
        return datetime.now(ZoneInfo("America/New_York")).date()


def _pretty_time(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(ZoneInfo("America/New_York")).strftime("%b %d, %Y at %I:%M %p ET")
    except (ValueError, TypeError):
        return "Not saved yet"


def _configure() -> None:
    st.set_page_config(page_title="Med-Peds Resident Progress", page_icon="📋", layout="wide")
    # Only static CSS is rendered as HTML; uploaded names and comments are text.
    st.markdown("""<style>
    .block-container{padding-top:1.8rem;max-width:1480px}
    h1,h2,h3{color:#153E60}
    [data-testid="stMetric"]{background:#F2F6FA;border-radius:8px;padding:14px}
    [data-testid="stMetricLabel"]{font-weight:600}
    [data-testid="stSidebar"]{background:#F4F7FA}
    [data-testid="stFileUploader"]{min-height:142px}
    .stButton button{min-height:42px}
    </style>""", unsafe_allow_html=True)


def _read_config() -> tuple[dict, dict, list[str], dict]:
    try:
        secrets = st.secrets.to_dict()
    except Exception:
        raise SecurityConfigError("Add the account and storage settings to Streamlit Secrets to finish setup.") from None
    users = validate_users(secrets.get("users", {}))
    gh = dict(secrets.get("github", {}))
    repo = str(gh.get("repo", "")).strip()
    if repo.startswith("https://"):
        parsed = urlparse(repo)
        if parsed.netloc != "github.com" or parsed.query or parsed.fragment or parsed.username:
            raise SecurityConfigError("Use an OWNER/REPOSITORY value for the data repository.")
        repo = parsed.path.strip("/").removesuffix(".git")
    if "/" in repo:
        parts = repo.split("/")
        if len(parts) != 2:
            raise SecurityConfigError("Use an OWNER/REPOSITORY value for the data repository.")
        gh["owner"], gh["repo"] = parts
    gh.setdefault("branch", "main")
    if "path" not in gh:
        gh["path"] = str(gh.get("base_path", "encrypted")).strip("/") + "/ccc.enc"
    enc = secrets.get("encryption", {})
    previous = enc.get("previous_keys", [])
    if not isinstance(previous, list):
        raise SecurityConfigError("Previous encryption keys must be a list in app secrets.")
    keys = [enc.get("key", ""), *previous]
    app_config = dict(secrets.get("app", {}))
    try:
        app_config["session_timeout_minutes"] = max(5, min(120, int(app_config.get("session_timeout_minutes", 30))))
        app_config["max_session_hours"] = max(1, min(12, int(app_config.get("max_session_hours", 8))))
    except (TypeError, ValueError):
        raise SecurityConfigError("Session limits must be whole numbers in app secrets.") from None
    return users, gh, keys, app_config


def _expired(identity: dict, config: dict) -> bool:
    now = time.monotonic()
    return now - identity.get("last_active", 0) > config["session_timeout_minutes"] * 60 or now - identity.get("signed_in", 0) > config["max_session_hours"] * 3600


@st.fragment(run_every=60)
def _session_watchdog(config: dict) -> None:
    identity = st.session_state.get("identity")
    if identity and _expired(identity, config):
        _clear_session()
        st.session_state["session_expired"] = True
        st.rerun(scope="app")


def _login(users: dict, config: dict) -> dict:
    identity = st.session_state.get("identity")
    if identity and (not identity_is_current(identity, users) or _expired(identity, config)):
        _clear_session()
        st.session_state["session_expired"] = True
        identity = None
    if identity:
        identity["last_active"] = time.monotonic()
        _session_watchdog(config)
        return identity
    left, center, right = st.columns([1, 1.5, 1])
    with center:
        st.title("Med-Peds Resident Progress")
        st.write("Sign in to review resident progress, add evidence, and prepare CCC reports.")
        if st.session_state.pop("session_expired", False):
            st.info("Your session ended. Sign in again to continue.")
        login_message = st.session_state.pop("login_message", None)
        if login_message:
            st.error(login_message)
        with st.form("sign_in", clear_on_submit=True):
            username = st.text_input("Username", max_chars=64, key="login_username")
            password = st.text_input("Password", type="password", max_chars=1024, key="login_password")
            submit = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submit:
            login_error = "The username or password could not be verified."
            try:
                identity = authenticate(username, password, users)
            except AuthenticationRateLimited as exc:
                login_error = str(exc)
                identity = None
            if identity:
                _clear_session()
                identity.update({"last_active": time.monotonic(), "signed_in": time.monotonic()})
                st.session_state["identity"] = identity
                st.rerun()
            else:
                _clear_session()
                st.session_state["login_message"] = login_error
                st.rerun()
        st.caption("Access is limited to the program's authorized team.")
    st.stop()


def _permission(action: str) -> bool:
    return not st.session_state.get("demo", False) and role_allows(st.session_state.get("identity", {}), action)


def _actor() -> str:
    return st.session_state.get("identity", {}).get("username", "demo")


def _reset_uploads() -> None:
    st.session_state.pop("upload_preview", None)
    st.session_state.pop("upload_preview_context", None)
    for key in list(st.session_state):
        if key.startswith(("map_", "replace_", "include_", "omit_upload_", "ack_upload_", "allow_updates_",
                           "skip_unmatched_", "automatic_skips_")):
            st.session_state.pop(key, None)
    st.session_state["upload_generation"] = st.session_state.get("upload_generation", 0) + 1


def _clear_removed_resident_state(removed_ids: set[str]) -> None:
    """Discard selections and previews that can refer to a removed resident."""
    _reset_uploads()
    for key in list(st.session_state):
        if key in {"selected_resident", "resident_page_picker", "export_resident", "roster_selection", "yearly_progress_resident"}:
            if st.session_state[key] in removed_ids:
                st.session_state.pop(key, None)
        elif key.startswith(("map_", "replace_", "include_")):
            st.session_state.pop(key, None)
        elif key.startswith(("review_", "confirm_remove_", "annual_", "export_range_")) and any(rid in key for rid in removed_ids):
            st.session_state.pop(key, None)


def _load(store: GitHubStore) -> dict | None:
    if "state" not in st.session_state:
        with st.spinner("Opening saved resident records…"):
            state, sha = store.load()
            if state is not None:
                m.validate_state(state)
            st.session_state["state"] = state
            st.session_state["sha"] = sha
    return st.session_state["state"]


def _persist(candidate: dict, store: GitHubStore, message: str) -> bool:
    if st.session_state.get("pending_save"):
        st.error("Resolve the earlier save problem before making another change.")
        return False
    m.validate_state(candidate)
    expected_sha = st.session_state.get("sha")
    try:
        with st.spinner("Saving securely…"):
            sha = store.save(candidate, expected_sha)
    except (StorageError, m.ValidationError) as exc:
        st.session_state["pending_save"] = {"state": candidate, "expected_sha": expected_sha,
                                             "message": message, "conflict": isinstance(exc, (ConflictError, SaveUnconfirmedError)),
                                             "error": str(exc)}
        st.error("Not saved. " + str(exc))
        return False
    previous = st.session_state.get("state") or {}
    removed_ids = {r["id"] for r in previous.get("residents", [])} - {r["id"] for r in candidate["residents"]}
    if removed_ids:
        _clear_removed_resident_state(removed_ids)
    st.session_state["state"] = candidate
    st.session_state["sha"] = sha
    st.session_state["notice"] = message
    st.session_state.pop("generated_export", None)
    return True


def _save_problem(store: GitHubStore, keys: list[str]) -> None:
    pending = st.session_state.get("pending_save")
    if not pending:
        return
    st.error("Changes are waiting to be saved. " + pending["error"])
    st.caption("Your last saved dashboard remains available. The pending changes are kept only in this session until resolved.")
    a, b, c = st.columns(3)
    with a:
        if not pending["conflict"] and st.button("Retry this save", type="primary"):
            st.session_state.pop("pending_save")
            if _persist(pending["state"], store, pending["message"]):
                st.rerun()
    with b:
        try:
            pending_backup = encrypt_state(pending["state"], keys)
        except StorageError:
            st.warning("The pending changes are too large for an archive. Reload and import a smaller batch.")
        else:
            st.download_button("Download encrypted pending changes", pending_backup,
                               "pending-changes.enc", "application/octet-stream")
    with c:
        acknowledge = st.checkbox("I have kept a copy of any changes I need")
        if st.button("Reload saved records", disabled=not acknowledge):
            state, sha = store.load()
            if state is not None:
                m.validate_state(state)
            st.session_state.update({"state": state, "sha": sha})
            st.session_state.pop("pending_save", None)
            st.session_state.pop("upload_preview", None)
            st.session_state.pop("generated_export", None)
            st.rerun()
    st.divider()


def _range_controls(state: dict, *, prefix: str, default_start: date | None = None) -> tuple[str, str]:
    today = _today(state)
    a, b = st.columns(2)
    with a:
        start = st.date_input("Reporting period starts", value=default_start or m.current_academic_start(today), key=prefix + "_start")
    with b:
        end = st.date_input("Evidence through", value=today, key=prefix + "_end", help="Includes evaluations completed through this date when a completion date is supplied. Otherwise the source event or reporting date is used.")
    if start > end:
        st.error("Choose an end date on or after the start date.")
        st.stop()
    return start.isoformat(), end.isoformat()


def _pick_resident(state: dict, key: str, include_inactive: bool = True) -> dict | None:
    residents = [r for r in state["residents"] if include_inactive or r.get("status") == "Active"]
    residents.sort(key=lambda r: r["name"].casefold())
    if not residents:
        st.info("Add residents under Administration to begin.")
        return None
    ids = [r["id"] for r in residents]
    lookup = _resident_labels(residents)
    selected_id = st.session_state.get("selected_resident")
    default = ids.index(selected_id) if selected_id in ids else 0
    picked = st.selectbox("Find a resident", ids, index=default, format_func=lambda x: lookup[x], key=key)
    st.session_state["selected_resident"] = picked
    return m.resident_by_id(state, picked)


def _metrics(records: list[dict]) -> None:
    counts = m.summary_counts(records)
    a, b, c, d = st.columns(4)
    a.metric("Medicine evaluations", counts["medicine_evaluations"])
    b.metric("Pediatrics evaluations", counts["pediatrics_evaluations"])
    c.metric("Self-assessments", counts["self_assessments"])
    d.metric("Procedure entries", counts["procedures"])
    combined = sum(r["category"] == "evaluations" and r["specialty"] == "Combined" for r in records)
    if combined:
        st.caption(f"Also available: {combined} Combined evaluations. These are retained separately from the Medicine and Pediatrics counts.")


def _evidence_table(records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Resident": r.get("resident_name", ""),
        "Date": r.get("date", ""), "Specialty": r.get("specialty", ""),
        "Category": m.CATEGORIES.get(r.get("category"), r.get("category", "")),
        "Measure or form": r.get("metric") or r.get("form", ""),
        "Value": "Not assessed" if r.get("value") is None and r.get("category") == "milestones" else str(r.get("value", "") if r.get("value") is not None else ""),
        "Source interval": " to ".join(str(r.get(k, "")) for k in ("period_start", "period_end")),
        "Source": r.get("source_name", ""),
    } for r in records])


def _evaluation_status_details(status: dict) -> None:
    def shown_date(value: str) -> str:
        parsed = m.parse_date(value)
        return parsed.strftime("%b %d, %Y") if parsed else "Not available"

    st.caption("Last saved update: " + _pretty_time(status.get("last_updated_at", "")))
    st.caption("Latest completed evaluation: " + shown_date(status.get("latest_completed_date", "")))
    st.caption("Latest rotation end: " + shown_date(status.get("latest_rotation_end_date", "")))
    if status.get("earliest_evaluation_date") and status.get("latest_evaluation_date"):
        st.caption("Saved evaluation dates: " + shown_date(status["earliest_evaluation_date"])
                   + " to " + shown_date(status["latest_evaluation_date"]))
    count = status.get("evaluation_count", 0)
    st.caption(f"{count:,} evaluation{'s' if count != 1 else ''} saved")


def _evaluation_update_summary(state: dict) -> None:
    from ccc.upload_workflow import evaluation_import_status
    statuses = evaluation_import_status(state)
    st.subheader("Evaluation updates")
    for specialty, column in zip(("Medicine", "Pediatrics"), st.columns(2)):
        with column, st.container(border=True):
            st.write(specialty)
            _evaluation_status_details(statuses[specialty])
    st.caption("These dates describe saved evaluations across all years. A latest date does not mean every evaluation before it has been received.")


def _program_dashboard(state: dict) -> None:
    st.header("Program dashboard")
    _evaluation_update_summary(state)
    st.caption("Choose a date range to see the evidence currently available for each resident.")
    start, end = _range_controls(state, prefix="dashboard")
    year_filter = st.multiselect("Training years", ["PGY1", "PGY2", "PGY3", "PGY4", "Other"], default=["PGY1", "PGY2", "PGY3", "PGY4", "Other"])
    active = st.checkbox("Show active residents only", value=True)
    rows = []
    for resident in sorted(state["residents"], key=lambda r: r["name"].casefold()):
        if active and resident.get("status") != "Active":
            continue
        pgy = m.current_pgy(resident, end)
        label = f"PGY{pgy}" if 1 <= pgy <= 4 else "Other"
        if label not in year_filter:
            continue
        records = m.records_for(state, resident["id"], start, end)
        counts = m.summary_counts(records)
        reviews = sorted([r for r in state["reviews"] if r["resident_id"] == resident["id"] and r["review_date"] <= end], key=lambda r: r["review_date"])
        latest = reviews[-1] if reviews else None
        needed = []
        if not counts["medicine_evaluations"]:
            needed.append("Medicine evaluations")
        if not counts["pediatrics_evaluations"]:
            needed.append("Pediatrics evaluations")
        if not counts["self_assessments"]:
            needed.append("Self-assessment")
        rows.append({"Resident": resident["name"], "Training year": label,
                     "Medicine evals": counts["medicine_evaluations"], "Pediatrics evals": counts["pediatrics_evaluations"],
                     "Combined evals": sum(r["category"] == "evaluations" and r["specialty"] == "Combined" for r in records),
                     "Last evidence": counts["latest_evidence"] or "None in period",
                     "Latest review": f"PGY{latest['pgy']} {latest['half']} · {latest['status']}" if latest else "Not started",
                     "Evidence to check": ", ".join(needed) if needed else "Available",
                     "id": resident["id"]})
    a, b, c = st.columns(3)
    a.metric("Residents shown", len(rows))
    b.metric("With evidence to check", sum(r["Evidence to check"] != "Available" for r in rows))
    c.metric("Saved uploads", sum(s.get("active", True) for s in state["sources"]))
    if rows:
        df = pd.DataFrame(rows)
        selected = st.dataframe(df.drop(columns="id"), hide_index=True, width="stretch",
                                on_select="rerun", selection_mode="single-row", key="cohort_table")
        st.caption("Missing evidence is a prompt to check upload coverage and rotations; it is not a judgment of resident performance.")
        picks = selected.selection.rows
        if picks and st.button("Open selected resident", type="primary"):
            st.session_state["selected_resident"] = rows[picks[0]]["id"]
            st.session_state["nav_target"] = "Resident review"
            st.rerun()
    else:
        st.info("No residents match this view. Add the roster under Administration, or adjust these filters.")
    if state["sources"]:
        with st.expander("Recent uploads"):
            items = sorted(state["sources"], key=lambda x: x.get("uploaded_at", ""), reverse=True)[:10]
            st.dataframe(pd.DataFrame([{"File": s.get("name", ""), "Category": m.CATEGORIES.get(s.get("kind"), ""),
                                         "Specialty": s.get("specialty", ""), "Saved": _pretty_time(s.get("uploaded_at", "")),
                                         "Entries at upload": s.get("record_count", 0), "Status": "Active" if s.get("active", True) else "Excluded"} for s in items]),
                         hide_index=True, width="stretch")


def _show_milestones(records: list[dict]) -> None:
    milestone_records = [r for r in records if r["category"] == "milestones"]
    if not milestone_records:
        st.info("No milestone scores are saved for this period.")
        return
    specialty = st.radio("Milestone set", ["Medicine", "Pediatrics"], horizontal=True, key="milestone_set")
    selected = [r for r in milestone_records if r["specialty"] == specialty]
    if not selected:
        st.info("No scores for this specialty in the selected period.")
        return
    df = pd.DataFrame([{"Date": r["date"], "Milestone": r.get("metric", ""), "Score": r.get("value"), "Form": r.get("form", ""),
                         "Source": r.get("source_name", "")} for r in selected])
    codes = sorted(set(df["Milestone"]))
    code = st.selectbox("Follow a milestone over time", codes)
    series = df[df["Milestone"] == code].copy()
    series["Score"] = pd.to_numeric(series["Score"], errors="coerce")
    conflicting = series.groupby("Date")["Score"].nunique().gt(1).any()
    if conflicting:
        st.warning("More than one score is recorded on the same date. Review the sources; no trend line is combined.")
    elif series["Score"].notna().sum() > 1:
        st.line_chart(series.dropna(subset=["Score"]).drop_duplicates(["Date", "Score"]).set_index("Date")[["Score"]], y_label="Recorded score")
    st.dataframe(df, hide_index=True, width="stretch")
    st.caption("Medicine and Pediatrics use different milestone sets. The dashboard shows recorded scores and leaves unassessed items blank. The Word report includes the notebook's calculated trend and readiness descriptions for committee review.")


def _show_evidence(state: dict, resident: dict, records: list[dict], start: str, end: str) -> None:
    categories = ["All evidence", *m.CATEGORIES.values()]
    picked = st.selectbox("Evidence type", categories, key="evidence_kind")
    selected = [r for r in records if picked == "All evidence" or m.CATEGORIES[r["category"]] == picked]
    specialty = st.radio("Show specialty", ["All", *m.SPECIALTIES], horizontal=True, key="evidence_specialty")
    if specialty != "All":
        selected = [r for r in selected if r["specialty"] == specialty]
    if not selected:
        st.info("No matching evidence is saved for this date range.")
    else:
        st.dataframe(_evidence_table(selected), hide_index=True, width="stretch")
        ids = [r["id"] for r in selected]
        lookup = {r["id"]: r for r in selected}
        chosen = st.selectbox("Open an entry to read the original feedback", ids,
                               format_func=lambda x: f"{lookup[x]['date']} · {lookup[x]['specialty']} · {lookup[x].get('metric') or lookup[x].get('form') or m.CATEGORIES[lookup[x]['category']]} · {x[:6]}")
        record = lookup[chosen]
        st.text(f"Source: {record.get('source_name', '')} · {record.get('source_locator', '')}")
        if record.get("evaluator"):
            st.text("Evaluator: " + str(record["evaluator"]))
        if record.get("rotation"):
            st.text("Rotation: " + str(record["rotation"]))
        if record.get("text"):
            st.text(record["text"])
        for q in record.get("questions", []):
            st.text(str(q.get("text", "")))
            if q.get("response") not in (None, ""):
                st.text("Response: " + str(q["response"]))
            if q.get("comment"):
                st.text("Comment: " + str(q["comment"]))
        if record.get("values"):
            st.dataframe(pd.DataFrame([{"Field": k, "Value": str(v)} for k, v in record["values"].items()]), hide_index=True, width="stretch")
        if record.get("possible_revision"):
            st.warning("This entry was imported as a possible revision. Check the other source before interpreting both as separate events.")
    excluded = m.coverage_exclusions(state, resident["id"], start, end)
    if excluded:
        with st.expander(f"{len(excluded)} summary entries cover a wider reporting period"):
            st.caption("These summaries are saved but excluded from the selected-period totals. Expand the reporting dates to review their complete source period.")
            st.dataframe(_evidence_table(excluded), hide_index=True, width="stretch")


def _timeline(state: dict, resident: dict) -> None:
    rows = []
    for pgy in range(1, 5):
        for half in m.HALVES:
            matches = sorted([r for r in state["reviews"] if r["resident_id"] == resident["id"] and r["pgy"] == pgy and r["half"] == half], key=lambda r: (r["academic_year"], r["review_date"]))
            if not matches:
                default = m.checkpoint_defaults(resident, pgy, half)
                rows.append({"Checkpoint": f"PGY{pgy} {half}", "Academic year": default["academic_year"], "Meeting": "", "Status": "Not started"})
            for review in matches:
                rows.append({"Checkpoint": f"PGY{pgy} {half}", "Academic year": review["academic_year"], "Meeting": review["review_date"], "Status": review["status"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Year-end reviews cover the full training year by default. Review dates and periods can be adjusted for leave, off-cycle starts, or repeated training years.")


def _resident_page(state: dict, store: GitHubStore | None) -> None:
    st.header("Resident review")
    resident = _pick_resident(state, "resident_page_picker")
    if resident is None:
        return
    st.subheader(_escape(resident["name"]))
    st.caption(f"Training began {resident['start_date']} · {resident.get('status', 'Active')} · Expected graduation {resident.get('graduation_date') or 'not entered'}")
    if st.button("Open yearly exam scores, clinic tallies and encounters"):
        st.session_state["yearly_progress_resident"] = resident["id"]
        st.session_state["nav_target"] = "Yearly progress"
        st.rerun()
    start, end = _range_controls(state, prefix="resident_range")
    records = m.records_for(state, resident["id"], start, end)
    tabs = st.tabs(["Overview", "Evidence", "Milestones", "CCC review", "Review history"])
    with tabs[0]:
        _metrics(records)
        a, b, c = st.columns(3)
        a.metric("Conference summaries", sum(r["category"] == "conferences" for r in records))
        b.metric("Duty-hour entries", sum(r["category"] == "duty_hours" for r in records))
        c.metric("Reported duty-hour concerns", sum(r["category"] == "duty_violations" for r in records))
        reviews = sorted([r for r in state["reviews"] if r["resident_id"] == resident["id"] and r["review_date"] <= end], key=lambda r: r["review_date"])
        if reviews:
            latest = reviews[-1]
            st.subheader("Most recent CCC assessment and plan")
            st.caption(f"PGY{latest['pgy']} {latest['half']} · {latest['review_date']} · {latest['status']}")
            st.text(latest["fields"].get("ccc_decision") or "Assessment not entered yet.")
            if latest["fields"].get("action_plan"):
                st.text(latest["fields"]["action_plan"])
            if latest["fields"].get("mentor") or latest["fields"].get("follow_up_date"):
                st.text("Support: " + latest["fields"].get("mentor", "Not entered") + "\nFollow-up: " + latest["fields"].get("follow_up_date", "Not entered"))
            values = [{"Program measure": label, "Recorded value": latest["fields"][key]} for key, label in m.REVIEW_VALUE_FIELDS.items() if latest["fields"].get(key)]
            if values:
                st.dataframe(pd.DataFrame(values), hide_index=True, width="stretch")
        st.subheader("Four-year review timeline")
        _timeline(state, resident)
    with tabs[1]:
        _show_evidence(state, resident, records, start, end)
    with tabs[2]:
        _show_milestones(records)
    with tabs[3]:
        _review_editor(state, resident, store)
    with tabs[4]:
        _history(state, resident, store)


def _yearly_progress_page(state: dict, store: GitHubStore | None) -> None:
    from ccc import annual

    st.header("Yearly progress")
    resident = _pick_resident(state, "yearly_progress_resident")
    if resident is None:
        return
    st.subheader(_escape(resident["name"]))
    st.caption("Save exam results, clinic patient tallies, and observed encounters for each residency year. These values carry forward into CCC summaries.")
    current = annual.progress_for(state, resident["id"], _today(state).isoformat())
    summary = []
    for pgy in range(1, 5):
        rows = [entry for entry in current if entry["pgy"] == pgy]
        for entry in rows or [annual.default_progress(resident, pgy, _today(state))]:
            fields = entry["fields"]
            summary.append({"Year": f"PGY{pgy}", "Academic year": entry["academic_year"],
                            "Pediatrics score": fields.get("pediatrics_ite_score"),
                            "Expected pass rate (%)": fields.get("pediatrics_ite_expected_pass_rate"),
                            "Medicine score": fields.get("medicine_ite_score"),
                            "Medicine percentile": fields.get("medicine_ite_percentile"),
                            "Adult clinic patients": fields.get("adult_clinic_count"),
                            "Pediatric clinic patients": fields.get("pediatric_clinic_count"),
                            "Observed encounters": (f"{sum(fields.get(key) is True for key in annual.ENCOUNTER_FIELDS)} of 4 completed"
                                                      if any(fields.get(key) is not None for key in annual.ENCOUNTER_FIELDS) else "Not recorded"),
                            "Updated through": entry.get("as_of", "") if rows else ""})
    st.dataframe(pd.DataFrame(summary), hide_index=True, width="stretch")
    st.caption("A blank value means it has not been recorded. A zero is a recorded result.")
    if not _permission("review") or store is None:
        st.info("A reviewer or administrator can update yearly progress.")
        return

    pgy = st.selectbox("Residency year", [1, 2, 3, 4],
                       index=min(4, max(1, m.current_pgy(resident, _today(state)))) - 1,
                       format_func=lambda value: f"PGY{value}", key=f"annual_pgy_{resident['id']}")
    saved = sorted([entry for entry in state.get("annual_progress", [])
                    if entry["resident_id"] == resident["id"] and entry["pgy"] == pgy],
                   key=lambda entry: (entry["period_start"], entry["as_of"]), reverse=True)
    selected = None
    if saved:
        lookup = {entry["id"]: entry for entry in saved}
        choice = st.selectbox("Training year to update", [*lookup, "__new__"],
                               format_func=lambda value: "Add another training year for this PGY" if value == "__new__"
                               else f"{lookup[value]['academic_year']} · {lookup[value]['period_start']} to {lookup[value]['period_end']}",
                               key=f"annual_entry_{resident['id']}_{pgy}")
        selected = lookup.get(choice)
    defaults = copy.deepcopy(selected) if selected else annual.default_progress(resident, pgy, _today(state))
    if selected:
        defaults["as_of"] = max(defaults["period_start"], min(defaults["period_end"], _today(state).isoformat()))
    fields = defaults["fields"]
    legacy = sorted([review for review in state["reviews"] if review["resident_id"] == resident["id"]
                     and review["pgy"] == pgy and review.get("academic_year") == defaults["academic_year"]],
                    key=lambda review: (review["period_end"], review.get("updated_at", "")), reverse=True)
    legacy_rows = []
    for review in legacy:
        for key, label in m.REVIEW_VALUE_FIELDS.items():
            value = review["fields"].get(key)
            if key in {*annual.NUMERIC_FIELDS, "pediatrics_ite_percentile", "observed_encounters_completed"} and value not in (None, ""):
                legacy_rows.append({"Review date": review["review_date"], "Field": label, "Recorded value": str(value)})
    if legacy_rows:
        with st.expander("Values previously entered in CCC reviews"):
            st.dataframe(pd.DataFrame(legacy_rows), hide_index=True, width="stretch")
            st.caption("These remain in their original reviews. Enter the values below to include them in the yearly table. An earlier Pediatrics percentile is not an expected pass rate.")

    form_key = f"annual_form_{resident['id']}_{pgy}_{defaults.get('id', 'new')}_{state['revision']}"
    with st.form(form_key):
        as_of = st.date_input("Progress recorded through", value=m.parse_date(defaults["as_of"]),
                              help="Use the date represented by these scores and year-to-date tallies. Earlier saved values remain available for reports with earlier cutoffs.")
        with st.expander("Training-year dates"):
            a, b, c = st.columns(3)
            academic_year = a.text_input("Academic year for these values", value=defaults["academic_year"])
            period_start = b.date_input("Training year starts", value=m.parse_date(defaults["period_start"]))
            period_end = c.date_input("Training year ends", value=m.parse_date(defaults["period_end"]))
            st.caption("Change these dates for an off-cycle or repeated training year.")
        st.subheader("In-training exams")
        entered = {}
        a, b = st.columns(2)
        for container, keys in [(a, ("pediatrics_ite_score", "pediatrics_ite_expected_pass_rate")),
                                (b, ("medicine_ite_score", "medicine_ite_percentile"))]:
            for key in keys:
                entered[key] = container.number_input(annual.FIELD_LABELS[key], min_value=0.0,
                                                      max_value=100.0 if key in annual.PERCENT_FIELDS else None,
                                                      value=float(fields[key]) if fields.get(key) is not None else None,
                                                      step=1.0, format="%g", placeholder="Not recorded")
        st.subheader("Clinic patient tallies")
        a, b = st.columns(2)
        for container, key in [(a, "adult_clinic_count"), (b, "pediatric_clinic_count")]:
            entered[key] = container.number_input(annual.FIELD_LABELS[key], min_value=0,
                                                  value=fields.get(key), step=1, placeholder="Not recorded")
        st.caption("Enter the total for this residency year through the progress date above.")
        st.subheader("Observed encounters")
        st.caption("Record each encounter separately so you can see what is complete for this year.")
        statuses = {None: "Not recorded", False: "Not completed", True: "Completed"}
        for col, key in zip(st.columns(4), annual.ENCOUNTER_FIELDS):
            entered[key] = col.selectbox(annual.ENCOUNTER_FIELDS[key], [None, False, True],
                                         index=[None, False, True].index(fields.get(key)),
                                         format_func=lambda value: statuses[value])
        st.caption("Select Save yearly progress to keep these changes.")
        submitted = st.form_submit_button("Save yearly progress", type="primary", width="stretch")
    if submitted:
        data = {**defaults, "pgy": pgy, "academic_year": academic_year,
                "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
                "as_of": as_of.isoformat(), "fields": entered}
        try:
            candidate = annual.save_progress(state, resident["id"], data, _actor())
            if _persist(candidate, store, f"PGY{pgy} yearly progress saved. CCC summaries will include these values."):
                st.rerun()
        except m.ValidationError as exc:
            st.error(str(exc))


def _review_editor(state: dict, resident: dict, store: GitHubStore | None) -> None:
    if not _permission("review"):
        st.info("A reviewer or administrator can create and finalize CCC reviews.")
        return
    reviews = sorted([r for r in state["reviews"] if r["resident_id"] == resident["id"]], key=lambda r: r["review_date"], reverse=True)
    choices = ["__new__", *[r["id"] for r in reviews]]
    lookup = {r["id"]: r for r in reviews}
    chosen = st.selectbox("Start or open a review", choices,
                           format_func=lambda x: "Start a new review" if x == "__new__" else f"PGY{lookup[x]['pgy']} {lookup[x]['half']} · {lookup[x]['academic_year']} · {lookup[x]['status']}",
                           key=f"review_choice_{resident['id']}")
    review = lookup.get(chosen)
    if review and review["status"] == "Final":
        st.success(f"Finalized on {_pretty_time(review['finalized_at'])}. Its evidence is preserved.")
        for key, label in _REVIEW_TEXT_LABELS.items():
            if review["fields"].get(key):
                st.markdown(f"**{label}**")
                st.text(review["fields"][key])
        st.caption("An administrator can reopen a finalized review with a reason under Review history.")
        return
    c1, c2 = st.columns(2)
    with c1:
        pgy = st.selectbox("Training year for this review", [1, 2, 3, 4],
                           index=(review["pgy"] if review else min(4, max(1, m.current_pgy(resident, _today(state))))) - 1,
                           format_func=lambda x: f"PGY{x}", key=f"review_pgy_{resident['id']}_{chosen}")
    with c2:
        half = st.selectbox("Checkpoint", m.HALVES, index=m.HALVES.index(review["half"]) if review else 0,
                            key=f"review_half_{resident['id']}_{chosen}")
    defaults = review or m.checkpoint_defaults(resident, pgy, half)
    fields = defaults.get("fields", {})
    form_key = f"review_form_{resident['id']}_{chosen}_{pgy}_{half}"
    st.caption("Changes are not saved until you select Save draft or Finalize review.")
    with st.form(form_key):
        a, b, c, d = st.columns(4)
        year = a.text_input("Academic year", value=defaults["academic_year"])
        first = b.date_input("Review period starts", value=m.parse_date(defaults["period_start"]))
        last = c.date_input("Evidence cutoff", value=m.parse_date(defaults["period_end"]))
        meeting = d.date_input("Meeting date", value=m.parse_date(defaults["review_date"]))
        edited = copy.deepcopy(fields)
        st.subheader("Progress and next steps")
        for key in ("overall_impression", "strengths", "areas_for_growth", "patterns_over_time", "ccc_summary", "ccc_decision", "action_plan"):
            edited[key] = st.text_area(m.REVIEW_TEXT_FIELDS[key], value=fields.get(key, ""), height=90)
        a, b = st.columns(2)
        edited["mentor"] = a.text_input(m.REVIEW_TEXT_FIELDS["mentor"], value=fields.get("mentor", ""))
        edited["coach_name"] = b.text_input(m.REVIEW_TEXT_FIELDS["coach_name"], value=fields.get("coach_name", resident.get("coach_name", "")))
        a, b = st.columns(2)
        edited["follow_up_date"] = a.text_input("Action plan follow-up date", value=fields.get("follow_up_date", ""), placeholder="YYYY-MM-DD or not yet scheduled")
        edited["next_review_date"] = b.text_input("Next review date", value=fields.get("next_review_date", ""), placeholder="YYYY-MM-DD or not yet scheduled")
        st.caption("Enter yearly exam scores, clinic patient tallies, and observed encounters on the Yearly progress page.")
        with st.expander("Other scores and earlier review values"):
            keys = [key for key in m.REVIEW_VALUE_FIELDS if key.startswith("usmle_") or fields.get(key) not in (None, "")]
            for i in range(0, len(keys), 3):
                cols = st.columns(3)
                for col, key in zip(cols, keys[i:i + 3]):
                    edited[key] = col.text_input(m.REVIEW_VALUE_FIELDS[key], value=str(fields.get(key, "")))
        with st.expander("Rotations, scholarship and additional feedback"):
            for key in ("rotation_summary", "observed_encounters_notes", "abstracts", "publications", "advocacy", "nursing_feedback", "resident_comments"):
                edited[key] = st.text_area(m.REVIEW_TEXT_FIELDS[key], value=fields.get(key, ""), height=80)
        with st.expander("Practice readiness assessment"):
            st.caption("Enter the committee's assessment. The Word report also includes the notebook's calculated readiness descriptions.")
            for key in ("readiness_clinical_reasoning", "readiness_team_leadership", "readiness_learner_supervision", "readiness_communication", "readiness_professionalism", "readiness_systems_based_practice"):
                edited[key] = st.text_input(m.REVIEW_TEXT_FIELDS[key], value=fields.get(key, ""))
        with st.expander("Duty-hour review notes"):
            st.caption("For each rolling 28-day window, add the logged work hours and divide by four. Enter the highest weekly average and its window below.")
            for key in ("duty_min_days_off", "duty_days_off_window", "duty_days_off_rotations", "duty_max_hours_4weeks", "duty_80_hour_window", "duty_80_hour_rotations", "duty_resolution_notes"):
                edited[key] = st.text_input(_REVIEW_TEXT_LABELS[key], value=fields.get(key, ""))
        confirm_final = st.checkbox("I reviewed the evidence and this is the CCC's recorded assessment")
        a, b = st.columns(2)
        draft = a.form_submit_button("Save draft", width="stretch")
        final = b.form_submit_button("Finalize review", type="primary", width="stretch")
    if draft or final:
        if final and not confirm_final:
            st.error("Confirm that the evidence and CCC assessment were reviewed before finalizing.")
            return
        data = {**(review or {}), "pgy": pgy, "half": half, "academic_year": year,
                "period_start": first.isoformat(), "period_end": last.isoformat(), "review_date": meeting.isoformat(), "fields": edited}
        try:
            candidate = m.save_review(state, resident["id"], data, _actor(), finalize=final)
            if _persist(candidate, store, "Review finalized and evidence preserved." if final else "Review draft saved."):
                st.rerun()
        except (ValueError, TypeError) as exc:
            st.error(str(exc) if isinstance(exc, m.ValidationError) else "Check the review dates and values before saving.")


def _history(state: dict, resident: dict, store: GitHubStore | None) -> None:
    reviews = sorted([r for r in state["reviews"] if r["resident_id"] == resident["id"]], key=lambda r: r["review_date"], reverse=True)
    if not reviews:
        st.info("No CCC reviews have been saved for this resident.")
        return
    for review in reviews:
        with st.expander(f"PGY{review['pgy']} {review['half']} · {review['academic_year']} · {review['status']}"):
            st.text(f"Meeting: {review['review_date']}\nEvidence period: {review['period_start']} to {review['period_end']}")
            st.text(review.get("fields", {}).get("ccc_decision", "CCC assessment not yet entered."))
            if review["status"] == "Final":
                st.caption(f"{len(review.get('snapshot', []))} evidence entries preserved at finalization.")
                if _permission("manage"):
                    reason = st.text_input("Reason to reopen", key="reason_" + review["id"])
                    if st.button("Reopen this review", key="reopen_" + review["id"]):
                        try:
                            candidate = m.reopen_review(state, review["id"], _actor(), reason)
                            if _persist(candidate, store, "Review reopened. The prior final version remains in its history."):
                                st.rerun()
                        except m.ValidationError as exc:
                            st.error(str(exc))
            versions = review.get("versions", [])
            if versions:
                st.markdown("**Prior finalized versions**")
                for n, version in enumerate(versions, 1):
                    st.caption(f"Version {n} · {_pretty_time(version.get('finalized_at'))} · {len(version.get('snapshot', []))} preserved entries")
                    st.text(version.get("fields", {}).get("ccc_decision", ""))
    if _permission("manage"):
        with st.expander("Resident change history"):
            events = [a for a in state["audit"] if a.get("details", {}).get("resident_id") == resident["id"]
                      or a.get("details", {}).get("review_id") in {r["id"] for r in reviews}]
            st.dataframe(pd.DataFrame([{"When": _pretty_time(a["at"]), "Who": a["actor"], "Change": a["action"],
                                         "Reason": a.get("details", {}).get("reason", "")} for a in events]), hide_index=True, width="stretch")


def _uploads(state: dict, store: GitHubStore) -> None:
    if not _permission("upload"):
        st.info("An administrator can add files.")
        return
    from ccc.importers import parse_upload
    from ccc.upload_workflow import evaluation_import_status, prepare_upload_batch
    st.header("Upload files")
    st.write("Start with all available evaluation files for the past three to four years. Later, add new exports here using the same steps. One file is enough.")
    if not state["residents"]:
        st.info("First add the resident roster under Administration so names can be matched correctly.")
        return
    generation = st.session_state.get("upload_generation", 0)
    groups = []
    statuses = evaluation_import_status(state)
    for specialty, widget, column in zip(("Medicine", "Pediatrics"), ("eval", "peds_eval"), st.columns(2)):
        with column, st.container(border=True):
            st.subheader(specialty + " evaluations")
            _evaluation_status_details(statuses[specialty])
            files = st.file_uploader("Add " + specialty + " evaluation files",
                                     type=["tab", "csv", "xlsx", "xls", "tsv"], accept_multiple_files=True,
                                     key=f"files_{widget}_{generation}")
            groups.append(("evaluations", files, specialty))
            st.caption("Select several .tab files at once. This area is optional.")
    st.caption("Overlapping exports are welcome. Saved duplicates are skipped, and changed entries are flagged for review. Include earlier rotations when checking for evaluations completed late.")
    with st.expander("Saved evaluation files"):
        history = []
        seen_files = set()
        for source in sorted(state["sources"], key=lambda item: item.get("uploaded_at", ""), reverse=True):
            if (not source.get("active", True) or source.get("kind") not in {"evaluations", "self_assessments"}
                    or not source.get("record_count", 0)):
                continue
            file_key = (source.get("sha256") or source["id"], source.get("kind"), source.get("specialty"))
            if file_key in seen_files:
                continue
            seen_files.add(file_key)
            history.append({"File": source.get("name", ""), "Specialty": source.get("specialty", ""),
                            "Report": m.CATEGORIES.get(source.get("kind"), ""),
                            "Last saved": _pretty_time(source.get("uploaded_at", ""))})
        if history:
            st.dataframe(pd.DataFrame(history), hide_index=True, width="stretch")
            st.caption("These files added saved evaluation entries. Repeated copies appear once, with their most recent save. Files with no new entries do not change this history.")
        else:
            st.caption("No saved evaluation files to show.")
    with st.expander("Self-assessments and other reports"):
        specialty = st.radio("These files are for", m.SPECIALTIES, horizontal=True, key="upload_specialty")
        st.caption("This choice applies to the additional reports below. Choose Combined for a mixed evaluation export or shared administrative reports.")
        cols = st.columns(4)
        with cols[0], st.container(border=True):
            st.subheader("Other evaluations")
            kind = st.selectbox("Evaluation report", ["self_assessments", "evaluations"], format_func=lambda x: m.CATEGORIES[x], key="kind_eval")
            files = st.file_uploader("Add self-assessment or mixed evaluation files", type=["csv", "xlsx", "xls", "tab", "tsv"], accept_multiple_files=True, key=f"files_other_eval_{generation}")
            groups.append((kind, files, specialty))
        with cols[1], st.container(border=True):
            st.subheader("Milestones")
            files = st.file_uploader("Add milestone files", type=["pdf", "csv", "xlsx", "xls"], accept_multiple_files=True, key=f"files_milestones_{generation}")
            groups.append(("milestones", files, specialty))
            st.caption("PDFs with labeled scores, or a spreadsheet export.")
        with cols[2], st.container(border=True):
            st.subheader("Activities")
            kind = st.selectbox("Activity report", ["procedures", "conferences"], format_func=lambda x: m.CATEGORIES[x], key="kind_activity")
            files = st.file_uploader("Add activity files", type=["csv", "xlsx", "xls", "tsv"], accept_multiple_files=True, key=f"files_activity_{generation}")
            groups.append((kind, files, specialty))
        with cols[3], st.container(border=True):
            st.subheader("Duty hours")
            kind = st.selectbox("Duty-hour report", ["duty_hours", "duty_violations"], format_func=lambda x: "Shift log" if x == "duty_hours" else "Violation report", key="kind_duty")
            files = st.file_uploader("Add duty-hour files", type=["csv", "xlsx", "xls", "tsv"], accept_multiple_files=True, key=f"files_duty_{generation}")
            groups.append((kind, files, specialty))
    with st.expander("Dates for reports without dates"):
        st.caption("Dates inside each export are kept, including older years. These fallback dates are used only when the source does not supply the required dates. They do not limit the history imported.")
        earliest = min((m.parse_date(r.get("start_date")) for r in state["residents"] if m.parse_date(r.get("start_date"))), default=m.current_academic_start(_today(state)))
        a, b = st.columns(2)
        first = a.date_input("Fallback period starts", value=earliest, key="upload_range_start").isoformat()
        last = b.date_input("Fallback period ends", value=_today(state), key="upload_range_end").isoformat()
        if first > last:
            st.error("Choose a fallback end date on or after its start date.")
            return
    count = sum(len(files) for _, files, _ in groups)
    if count:
        st.caption(f"{count} file{'s' if count != 1 else ''} selected. Empty upload areas do not need a file.")
    else:
        st.caption("Add at least one file to process. You do not need every report type.")
    upload_context = (first, last, tuple((kind, specialty, f.name, hashlib.sha256(f.getvalue()).hexdigest()) for kind, files, specialty in groups for f in files))
    process_label = f"Process {count} file{'s' if count != 1 else ''}" if count else "Process files"
    if st.button(process_label, type="primary", disabled=count == 0, width="stretch"):
        if count > 100 or sum(f.size for _, files, _ in groups for f in files) > 60 * 1024 * 1024:
            st.error("Add up to 100 files and 60 MB in one batch. Each file can be up to 15 MB. Larger histories can be saved in successive batches.")
            return
        evaluation_specialties = {}
        for kind, specialty, filename, digest in upload_context[2]:
            if kind != "evaluations":
                continue
            previous_specialty = evaluation_specialties.get(digest)
            if previous_specialty and previous_specialty != specialty:
                st.session_state.pop("upload_preview", None)
                st.session_state.pop("upload_preview_context", None)
                st.error("The same evaluation file was selected for both " + previous_specialty + " and " + specialty
                         + ": " + _escape(filename) + ". Keep the file only in the correct specialty area, then process again.")
                return
            evaluation_specialties[digest] = specialty
        previews = []
        parsed_files = {}
        saved_file_keys = {(source.get("sha256"), source.get("kind"), source.get("specialty"))
                           for source in state["sources"] if source.get("active", True)}
        with st.spinner("Reading files and checking the fields…"):
            for kind, files, specialty in groups:
                for file in files:
                    data = file.getvalue()
                    digest = hashlib.sha256(data).hexdigest()
                    # Reuse parsing within this batch only. The extension can
                    # affect the reader, and each copy keeps its own filename.
                    parse_key = (digest, kind, specialty, Path(file.name).suffix.lower())
                    if parse_key in parsed_files:
                        result = copy.deepcopy(parsed_files[parse_key])
                        for record in result.records:
                            record["source_name"] = Path(file.name).name
                    else:
                        result = parse_upload(file.name, data, kind, specialty, period_start=first, period_end=last)
                        parsed_files[parse_key] = result
                    source = {"id": m.new_id(), "name": file.name, "sha256": digest, "size": len(data),
                              "kind": kind, "specialty": specialty, "period_start": first, "period_end": last, "profile": result.profile}
                    # A content match is a hint, not a reason to skip parsing or
                    # matching. The roster may now include previously skipped names.
                    previously_uploaded = (digest, kind, specialty) in saved_file_keys
                    previews.append({"source": source, "data": data, "records": result.records, "errors": result.errors,
                                     "warnings": result.warnings, "previously_uploaded": previously_uploaded})
        st.session_state["upload_preview"] = previews
        st.session_state["upload_preview_context"] = upload_context
    previews = st.session_state.get("upload_preview", [])
    if not previews:
        with st.expander("Help choosing an export"):
            st.write("Use the original New Innovations exports. The app reads CSV, Excel, tab-delimited files, and the HTML-style Excel files used by the older reports. Scanned or unlabeled milestone PDFs need a labeled spreadsheet; they are not assigned scores by position.")
            st.caption("Blank compatibility templates are available under Administration. Do not upload the populated REDCap dashboard PDF here.")
        return
    st.subheader("Review before saving")
    if st.session_state.get("upload_preview_context") != upload_context:
        st.warning("The files, report types, specialty or dates have changed. Process the selected files again to update the preview. If you returned from another page, add the same files again.")
        return
    st.caption("Not saved yet. Check the resident summary, then save. Files with no new entries are skipped automatically.")
    batch_summary = st.container()
    readable = [preview for preview in previews if preview["records"] and not preview["errors"]]
    readable_ids = {preview["source"]["id"] for preview in readable}
    file_labels = {preview["source"]["id"]: f"{i + 1}. {preview['source']['name']} · {preview['source']['specialty']}"
                   for i, preview in enumerate(previews)}
    file_batch = hashlib.sha256("|".join(preview["source"]["id"] for preview in previews).encode()).hexdigest()[:16]
    with st.expander("File details"):
        file_status = st.container()
        omitted = set(st.multiselect("Leave files out of this import", [preview["source"]["id"] for preview in readable],
                                    format_func=lambda source_id: file_labels[source_id], key="omit_upload_" + file_batch,
                                    help="Optional. Readable files are included automatically. Duplicate entries are skipped when you save."))
        for preview in previews:
            for error in preview["errors"]:
                st.error(_escape(preview["source"]["name"] + ": " + error))
            for warning in preview["warnings"]:
                st.warning(_escape(preview["source"]["name"] + ": " + warning))
    included = [preview for preview in readable if preview["source"]["id"] not in omitted]

    def show_file_summary(batch=None, resident_mappings=None) -> None:
        """Summarize actual batch results without a card for every file."""
        saved_ids = set(batch.saved_source_ids) if batch is not None else set()
        skipped_ids = set(batch.skipped_source_ids) if batch is not None else set()
        new_counts = {source["id"]: source.get("record_count", 0) for source in batch.candidate["sources"]
                      if source["id"] in saved_ids} if batch is not None else {}
        valid_residents = {resident["id"] for resident in state["residents"]}
        counts = Counter()
        rows = []
        for preview in previews:
            source = preview["source"]
            source_id = source["id"]
            if source_id not in readable_ids:
                label, status = "Could not read, left out", "unreadable"
            elif source_id in omitted:
                label, status = "Left out of this import", "omitted"
            elif source_id in saved_ids:
                label, status = "Ready to save", "ready"
            elif source_id in skipped_ids:
                has_selected_entries = any((resident_mappings or {}).get(m.mapping_key(record)) in valid_residents
                                           for record in preview["records"])
                if has_selected_entries:
                    label, status = "Duplicate entries, skipped", "duplicate"
                else:
                    label, status = "No selected resident entries, skipped", "no_roster"
            else:
                label, status = "Check resident matches or warnings", "pending"
            counts[status] += 1
            rows.append({"File": source["name"], "Specialty": source["specialty"],
                         "Report": m.CATEGORIES.get(source["kind"], source["kind"]), "Status": label,
                         "New entries": new_counts.get(source_id, 0) if batch is not None else None})
        with file_status:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        with batch_summary:
            parts = []
            if batch is not None:
                parts.append(f"{counts['ready']} file{'s' if counts['ready'] != 1 else ''} ready to save")
            for key, singular, plural in (
                    ("duplicate", "duplicate-only file skipped", "duplicate-only files skipped"),
                    ("no_roster", "file with no selected resident entries", "files with no selected resident entries"),
                    ("omitted", "file left out", "files left out")):
                if counts[key]:
                    parts.append(f"{counts[key]} {singular if counts[key] == 1 else plural}")
            if parts:
                st.write(" · ".join(parts))
            if batch is not None:
                stats = batch.stats
                st.caption(f"{stats['new_records']} new entries after matching and any selected replacements; {stats['duplicates']} duplicate entries will be skipped.")
            if counts["unreadable"]:
                number = counts["unreadable"]
                st.warning(f"{number} file{'s' if number != 1 else ''} could not be read and will be left out. Open File details to check. Readable files can still be saved.")
            if any(preview["warnings"] for preview in included):
                st.warning("Some files have warnings. Open File details to check them before saving.")

    if not included:
        show_file_summary()
        st.info("No readable files are selected to save. Check File details or add another file.")
        return
    all_records = [r for p in included for r in p["records"]]
    names = {}
    name_categories = {}
    for record in all_records:
        key = m.mapping_key(record)
        names.setdefault(key, record)
        name_categories.setdefault(key, set()).add(record.get("category"))
    evaluation_categories = {"evaluations", "self_assessments"}
    has_evaluations = any(categories & evaluation_categories for categories in name_categories.values())
    st.subheader("Residents in your Administration roster" if has_evaluations else "Confirm the residents")
    if has_evaluations:
        st.caption("Only residents matched to your Administration roster are included. Other evaluation entries are ignored automatically. If a resident uses a different spelling, add it under Other names used in exports in Administration.")
    else:
        st.caption("Residents are matched by name. Confirm any different spelling below. New Innovations and REDCap record IDs are not required.")
    roster_summary = st.container() if has_evaluations else None
    roster_ids = [r["id"] for r in sorted(state["residents"], key=lambda r: r["name"].casefold())]
    roster_labels = _resident_labels(state["residents"])
    match_batch = hashlib.sha256("|".join(p["source"]["id"] for p in included).encode()).hexdigest()[:16]
    auto_matches = {key: m.match_resident(state, record.get("resident_name", ""), record.get("external_id", ""))
                    for key, record in names.items()}
    roster_names = {m.normalize_name(name) for resident in state["residents"]
                    for name in [resident["name"], *resident.get("aliases", [])] if m.normalize_name(name)}
    roster_external_ids = {str(resident.get("external_id", "")).strip() for resident in state["residents"]
                           if str(resident.get("external_id", "")).strip()}
    # An ambiguous roster name or conflicting identifier needs a deliberate
    # choice. In mixed batches, do not silently skip another report category.
    safe_evaluation_skips = {
        key for key, record in names.items()
        if auto_matches[key] not in roster_ids and name_categories[key] <= evaluation_categories
        and m.normalize_name(record.get("resident_name", "")) not in roster_names
        and str(record.get("external_id", "")).strip() not in roster_external_ids
    }
    unmatched_keys = [key for key in names if auto_matches[key] not in roster_ids]
    widget_keys = {key: "map_" + match_batch + "_" + hashlib.sha256(key.encode()).hexdigest()[:16] for key in names}
    mappings = {}
    if has_evaluations:
        # Ignore obsolete focus toggles and unresolved hidden widget values.
        # The exclusion is a rule on every rerun, not a checkbox transition.
        for prefix in ("skip_unmatched_", "skip_unmatched_applied_", "automatic_skips_"):
            st.session_state.pop(prefix + match_batch, None)
        for key in safe_evaluation_skips:
            previous_match = st.session_state.get(widget_keys[key])
            if previous_match not in roster_ids:
                mappings[key] = "__skip__"
                st.session_state.pop(widget_keys[key], None)
        # Keep a deliberate roster match from an older unsaved preview. Its
        # control is still rendered below, so Streamlit retains that choice.
        matched_keys = [key for key in names if auto_matches[key] in roster_ids
                        or (key in safe_evaluation_skips and key not in mappings)]
    else:
        skip_unmatched = st.checkbox(
            "Skip unmatched names for this import", value=False, key="skip_unmatched_" + match_batch,
            help="Useful when an export includes residents from other programs. Check unfamiliar spellings before skipping. You can change any individual match below.",
        )
        prior_skip = st.session_state.get("skip_unmatched_applied_" + match_batch, False)
        automatic_skips = set(st.session_state.get("automatic_skips_" + match_batch, []))
        for key, widget_key in widget_keys.items():
            if skip_unmatched and not prior_skip and key in unmatched_keys and st.session_state.get(widget_key, "__choose__") == "__choose__":
                st.session_state[widget_key] = "__skip__"
                automatic_skips.add(widget_key)
            elif prior_skip and not skip_unmatched and widget_key in automatic_skips and st.session_state.get(widget_key) == "__skip__":
                st.session_state[widget_key] = "__choose__"
    options = ["__choose__", *roster_ids, "__skip__"]

    def resident_match(key: str) -> None:
        record = names[key]
        auto = auto_matches[key]
        label = str(record.get("resident_name") or "Unnamed resident")
        if record.get("external_id"):
            label += " · source ID " + str(record["external_id"])
        mappings[key] = st.selectbox(_escape(label), options, index=options.index(auto) if auto in options else 0,
                                     format_func=lambda x: "Choose a resident" if x == "__choose__" else "Skip these entries" if x == "__skip__" else roster_labels[x],
                                     key=widget_keys[key])

    if has_evaluations:
        if matched_keys:
            with st.expander("Review roster name matches"):
                st.caption("These matches are already selected. Change one only if needed.")
                for key in matched_keys:
                    resident_match(key)
        review_keys = [key for key in unmatched_keys if key not in safe_evaluation_skips]
        if review_keys:
            st.write("Names that need a match or an explicit choice to skip")
            st.caption("These names may match your roster, or also appear in another report type. Confirm the resident before including them.")
            for key in review_keys:
                resident_match(key)
    else:
        if unmatched_keys:
            st.caption(f"{len(unmatched_keys)} name{'s' if len(unmatched_keys) != 1 else ''} need a match or an explicit choice to skip.")
        for key in names:
            resident_match(key)
        st.session_state["skip_unmatched_applied_" + match_batch] = skip_unmatched
        st.session_state["automatic_skips_" + match_batch] = sorted(automatic_skips) if skip_unmatched else []
    skipped_names = sum(value == "__skip__" for value in mappings.values())
    if roster_summary is not None:
        evaluation_counts = Counter()
        other_counts = Counter()
        for record in all_records:
            target = mappings[m.mapping_key(record)]
            if target in roster_ids:
                counts = evaluation_counts if record.get("category") in evaluation_categories else other_counts
                counts[target] += 1
        selected_ids = [resident_id for resident_id in roster_ids if evaluation_counts[resident_id] or other_counts[resident_id]]
        with roster_summary:
            if selected_ids:
                rows = [{"Resident": roster_labels[resident_id], "Evaluation entries": evaluation_counts[resident_id]}
                        for resident_id in selected_ids]
                if other_counts:
                    for row, resident_id in zip(rows, selected_ids):
                        row["Other entries"] = other_counts[resident_id]
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
                st.caption("Selected entries for your roster residents, before duplicate checks.")
            else:
                st.info("No entries are matched to your Administration roster yet. Check the resident names and Other names used in exports in Administration, then process the files again.")
            if skipped_names:
                skipped_entries = sum(mappings[m.mapping_key(record)] == "__skip__" for record in all_records)
                st.caption(f"{skipped_names} name{'s' if skipped_names != 1 else ''} skipped, covering {skipped_entries} entr{'ies' if skipped_entries != 1 else 'y'}. These entries will not be added to resident dashboards.")
    elif skipped_names:
        st.caption(f"{skipped_names} name{'s' if skipped_names != 1 else ''} set to skip. Their entries will not be added in this import.")
    if "__choose__" in mappings.values():
        st.warning("Match the remaining names. If a resident is missing, add them under Administration; this preview will remain available.")
    remember = st.checkbox("Remember confirmed name variations for future uploads", value=True)
    archive = st.checkbox("Keep an encrypted copy of each original file", value=True)
    with st.expander("Replacing an earlier upload"):
        st.caption("Use this only when the new file is a complete replacement for the selected old file. Excluding the old upload removes its entries from live views; finalized review snapshots are preserved.")
        replacements = {}
        for preview in included:
            src = preview["source"]
            candidates = [s for s in state["sources"] if s.get("active", True) and s.get("kind") == src["kind"] and s.get("specialty") == src["specialty"]]
            labels = {s["id"]: f"{s['name']} · {s.get('period_start', '')} to {s.get('period_end', '')}" for s in candidates}
            replacements[src["id"]] = st.selectbox("Replacement for " + _escape(src["name"]), ["__none__", *labels],
                                                    format_func=lambda x: "Add as a new upload" if x == "__none__" else labels[x], key="replace_" + src["id"])
    possible = 0
    preview_problem = False
    batch_preview = None
    try:
        batch_preview = prepare_upload_batch(state, included, mappings, _actor(), replacements=replacements,
                                             allow_possible_updates=True)
        stats = batch_preview.stats
        possible = stats["possible_updates"]
        if possible:
            overlap_label = "entry overlaps" if possible == 1 else "entries overlap"
            st.warning(f"{possible} {overlap_label} saved evidence or another selected file with changed content. Review the overlaps before confirming they should be kept as additional evidence.")
    except m.ValidationError as exc:
        preview_problem = True
        st.error(str(exc))
    show_file_summary(batch_preview, mappings)
    if batch_preview is not None and not batch_preview.saved_source_ids:
        st.info("No new entries to save. The selected entries are already saved or were set to skip. The last saved update date is unchanged.")
        return
    preview_id = hashlib.sha256(json.dumps({"sources": [p["source"]["id"] for p in included],
                                           "mappings": mappings, "replacements": replacements}, sort_keys=True).encode()).hexdigest()[:16]
    allow_updates = st.checkbox("I checked overlapping entries and want to keep any remaining revisions as additional evidence", disabled=possible == 0, key="allow_updates_" + preview_id)
    acknowledged = st.checkbox("The included files, resident matches, dates, and any warnings are correct", key="ack_upload_" + preview_id)
    if st.button("Save to resident dashboards", type="primary", disabled=not acknowledged or preview_problem or "__choose__" in mappings.values() or (possible > 0 and not allow_updates), width="stretch"):
        if st.session_state.get("pending_save"):
            st.error("Resolve the earlier save problem before importing another batch.")
            return
        try:
            batch = prepare_upload_batch(state, included, mappings, _actor(), replacements=replacements,
                                         allow_possible_updates=allow_updates)
            if not batch.saved_source_ids:
                st.info("No new entries to save. The selected entries are already saved or were set to skip. The last saved update date is unchanged.")
                return
            candidate = batch.candidate
            saved_sources = [(p, p["source"]["id"]) for p in included if p["source"]["id"] in batch.saved_source_ids]
            if remember:
                saved_names = {m.mapping_key(r): r for p, _ in saved_sources for r in p["records"]}
                for key, record in saved_names.items():
                    target = mappings[key]
                    if target not in roster_ids or not record.get("resident_name"):
                        continue
                    resident = copy.deepcopy(m.resident_by_id(candidate, target))
                    alias = str(record["resident_name"]).strip()
                    if m.normalize_name(alias) not in {m.normalize_name(n) for n in [resident["name"], *resident.get("aliases", [])]}:
                        resident.setdefault("aliases", []).append(alias)
                        candidate = m.save_resident(candidate, resident, _actor())
            m.validate_state(candidate)
            # Preflight expansion/size before making any source-file writes.
            encrypt_state(candidate, st.session_state["encryption_keys"])
            if archive:
                with st.spinner("Protecting and saving original files…"):
                    for preview, source_id in saved_sources:
                        path, sha = store.write_blob(source_id, preview["data"])
                        src = next(s for s in candidate["sources"] if s["id"] == source_id)
                        src.update({"blob_id": source_id, "blob_path": path, "blob_sha": sha})
            count_saved = len(saved_sources)
            message = f"Saved {count_saved} file{'s' if count_saved != 1 else ''}. Resident dashboards are updated."
            if batch.skipped_source_ids:
                count_skipped = len(batch.skipped_source_ids)
                message += f" Skipped {count_skipped} file{'s' if count_skipped != 1 else ''} with no new entries."
            if _persist(candidate, store, message):
                _reset_uploads()
                st.rerun()
        except (m.ValidationError, StorageError) as exc:
            st.error("Not saved to the dashboard. " + str(exc))


def _word_reports(state: dict) -> None:
    from ccc import annual
    from ccc.ccc_summary import build_ccc_summary
    from ccc.reports import build_resident_report, build_ccc_letter

    # Discard passphrase fields left over from an older app session.
    for key in ("export_password", "export_confirmation", "clear_export_passwords"):
        st.session_state.pop(key, None)
    st.header("Word reports")
    export_error = st.session_state.pop("export_error", None)
    if export_error:
        st.error(export_error)
    st.write("Create your CCC summary, a detailed progress report, or a resident letter as an editable Word file.")
    if not _permission("export") and not st.session_state.get("demo"):
        st.info("Your account cannot export reports.")
        return
    residents = sorted(state["residents"], key=lambda r: r["name"].casefold())
    if not residents:
        st.info("Add the resident roster first.")
        return
    scope = st.radio("Who is this for?", ["One resident", "Several residents"], horizontal=True)
    document_type = st.radio("Document", ["CCC summary", "Progress report", "CCC letter"], horizontal=True)
    review = None
    selected_review = None
    bulk_basis = "Current dashboard date range"
    own_start_dates = False
    if scope == "One resident":
        resident = _pick_resident(state, "export_resident")
        selected_residents = [resident]
        reviews = sorted([r for r in state["reviews"] if r["resident_id"] == resident["id"]], key=lambda r: r["review_date"], reverse=True)
        review_map = {r["id"]: r for r in reviews}
        for r in reviews:
            for n, old in enumerate(r.get("versions", []), 1):
                review_map[f"{r['id']}::version-{n}"] = old
        options = ([] if document_type == "CCC letter" else ["__live__"]) + list(review_map)
        if not options:
            st.info("Save a CCC review before generating its letter.")
            return
        selected_review = st.selectbox("Use evidence from", options,
                                        format_func=lambda x: "Current dashboard date range" if x == "__live__" else f"PGY{review_map[x]['pgy']} {review_map[x]['half']} · {review_map[x]['academic_year']} · {review_map[x]['status']}" + (" · prior version" if "::version" in x else ""))
        review = review_map.get(selected_review)
        if review:
            start, end = review["period_start"], review["period_end"]
            st.caption(f"Evidence period: {start} to {end}. " + ("This finalized review uses its preserved evidence and yearly values." if review["status"] == "Final" else "This draft uses current saved evidence."))
        else:
            start, end = _range_controls(state, prefix=f"export_range_{resident['id']}",
                                         default_start=m.parse_date(resident["start_date"]))
            st.caption("The default range is the resident's start date through today. Adjust either date if needed.")
    else:
        ids = [r["id"] for r in residents]
        labels = _resident_labels(residents)
        selected = st.multiselect("Choose residents", ids, default=[r["id"] for r in residents if r.get("status") == "Active"], format_func=lambda x: labels[x])
        selected_residents = [r for r in residents if r["id"] in selected]
        date_basis = st.radio("Report start dates", ["Each resident's start date", "Use the same start date"], horizontal=True)
        own_start_dates = date_basis == "Each resident's start date"
        if own_start_dates:
            start = min((r["start_date"] for r in selected_residents), default=_today(state).isoformat())
            end = st.date_input("Evidence through", value=_today(state), key="export_group_cutoff").isoformat()
            st.caption("Each report begins on that resident's own start date and ends on the cutoff above.")
        else:
            earliest = min((m.parse_date(r["start_date"]) for r in selected_residents), default=_today(state))
            start, end = _range_controls(state, prefix="export_group_range", default_start=earliest)
        if document_type == "CCC letter":
            st.caption("Each letter uses that resident's latest saved review with a meeting date in the selected range.")
            bulk_basis = "Latest saved review in this range"
        else:
            bulk_basis = st.radio("Group report basis", ["Current dashboard date range", "Latest saved review in this range"], horizontal=True)
            if bulk_basis == "Latest saved review in this range":
                st.caption("Each report uses its selected review's dates and assessment. Finalized reviews retain their saved evidence and yearly values.")
    details = (st.checkbox("Include all evaluation comments and detailed evidence", value=True)
               if document_type == "Progress report" else document_type == "CCC summary")
    if document_type == "CCC summary":
        st.caption("Uses your table format, with Medicine and Pediatrics evaluation summaries starting on separate pages.")
    st.caption("Downloads are standard Word files. No export passphrase is required.")
    context = json.dumps({"revision": state["revision"], "residents": [r["id"] for r in selected_residents],
                          "review": selected_review, "start": start, "end": end, "type": document_type,
                          "protected": False, "details": details, "basis": bulk_basis,
                          "own_start_dates": own_start_dates}, sort_keys=True)
    if st.button("Prepare Word download", type="primary", width="stretch"):
        st.session_state.pop("generated_export", None)
        if not selected_residents:
            st.session_state["export_error"] = "Choose at least one resident."
            st.rerun()
        outputs = []
        try:
            with st.spinner("Preparing Word documents…"):
                name_counts = Counter(m.normalize_name(r["name"]) for r in selected_residents)
                for resident in selected_residents:
                    chosen_review = review
                    report_start = resident["start_date"] if own_start_dates else start
                    if scope == "Several residents" and bulk_basis == "Latest saved review in this range":
                        matches = sorted([r for r in state["reviews"] if r["resident_id"] == resident["id"] and report_start <= r["review_date"] <= end], key=lambda r: r["review_date"])
                        if not matches:
                            raise m.ValidationError("A selected resident has no review in this period. Save their review or remove them from this batch.")
                        chosen_review = matches[-1]
                    export_resident = chosen_review.get("snapshot_resident", resident) if chosen_review and chosen_review.get("status") == "Final" else resident
                    program_name = state["settings"].get("program_name", "Medicine Pediatrics Residency")
                    selected_start = chosen_review["period_start"] if chosen_review else report_start
                    selected_end = chosen_review["period_end"] if chosen_review else end
                    if selected_start > selected_end:
                        raise m.ValidationError("A report ends before the resident's start date. Adjust the dates or resident selection.")
                    if document_type == "CCC letter":
                        plain = build_ccc_letter(export_resident, chosen_review, program_name=program_name)
                    elif document_type == "CCC summary":
                        tracking = annual.progress_for(state, resident["id"], selected_end, review=chosen_review)
                        plain = build_ccc_summary(export_resident, m.active_records(state), state["reviews"],
                                                  period_start=selected_start, period_end=selected_end, review=chosen_review,
                                                  program_name=program_name, tracking=tracking)
                    else:
                        plain = build_resident_report(export_resident, m.active_records(state), state["reviews"],
                                                       period_start=selected_start, period_end=selected_end, review=chosen_review,
                                                       program_name=program_name, include_details=details)
                    safe_name = re.sub(r"[^\w .-]", "", export_resident["name"]).strip(" .").replace(" ", "_") or "Resident"
                    if name_counts[m.normalize_name(resident["name"])] > 1:
                        safe_name += "_" + resident["id"][:8]
                    suffix = {"CCC summary": "CCC_Summary", "Progress report": "Progress_Report", "CCC letter": "CCC_Letter"}[document_type]
                    filename = f"{selected_end}_{safe_name}_{suffix}.docx"
                    outputs.append((filename, plain))
            if len(outputs) == 1:
                filename, payload = outputs[0]
                mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            else:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
                    for filename, data in outputs:
                        z.writestr(filename, data)
                payload, filename, mime = buffer.getvalue(), "resident-word-reports.zip", "application/zip"
            st.session_state["generated_export"] = {"data": payload, "filename": filename, "mime": mime, "context": context, "protected": False}
        except ValueError as exc:
            st.session_state["export_error"] = str(exc) if isinstance(exc, m.ValidationError) else "The Word file could not be prepared. Check the selected review and evidence."
        st.rerun()
    generated = st.session_state.get("generated_export")
    if generated and generated["context"] == context:
        st.success("Your Word download is ready.")
        st.download_button("Download Word file" if scope == "One resident" else "Download Word files", generated["data"], generated["filename"], generated["mime"], type="primary", width="stretch")


def _roster(state: dict, store: GitHubStore) -> None:
    st.subheader("Resident roster")
    st.caption("Use each resident's name as it appears in New Innovations. You do not need to create a record ID.")
    existing = sorted(state["residents"], key=lambda r: r["name"].casefold())
    if existing:
        st.dataframe(pd.DataFrame([{"Name": r["name"], "Start": r["start_date"],
                                    "Graduation": r.get("graduation_date", ""), "Status": r.get("status", "Active")} for r in existing]), hide_index=True, width="stretch")
    lookup = {r["id"]: r for r in existing}
    labels = _resident_labels(existing)
    options = ["__new__", *lookup]
    if st.session_state.get("roster_selection", "__new__") not in options:
        st.session_state.pop("roster_selection", None)
    chosen = st.selectbox("Add or edit a resident", options, format_func=lambda x: "Add a resident" if x == "__new__" else labels[x], key="roster_selection")
    item = lookup.get(chosen, {})
    with st.form("roster_form_" + chosen):
        name = st.text_input("Resident name", value=item.get("name", ""))
        a, b = st.columns(2)
        start = a.date_input("Training start date", value=m.parse_date(item.get("start_date")) or m.current_academic_start(_today(state)))
        graduation = b.date_input("Expected graduation date", value=m.parse_date(item.get("graduation_date")), help="Leave blank to calculate four years from the training start date.")
        coach = st.text_input("Faculty coach", value=item.get("coach_name", ""))
        a, b = st.columns(2)
        status = a.selectbox("Resident status", ["Active", "Graduated", "Inactive"], index=["Active", "Graduated", "Inactive"].index(item.get("status", "Active")))
        override = b.selectbox("Current training year", [0, 1, 2, 3, 4], index=int(item.get("pgy_override") or 0), format_func=lambda x: "Calculate from start date" if x == 0 else f"PGY{x} override")
        aliases = st.text_area("Other names used in exports", value="\n".join(item.get("aliases", [])), help="One confirmed name variation per line. When changing a resident's name, include the previous name here so older exports still match. Confirmed matches can also be saved during upload.")
        with st.expander("Optional reference from an older import"):
            external = st.text_input("Existing source reference (optional)", value=item.get("external_id", ""), help="Leave blank for your name-based New Innovations exports. Do not create a new ID or enter a REDCap record number here. Existing references remain supported for earlier imports.")
        submitted = st.form_submit_button("Save resident", type="primary")
    if submitted:
        from datetime import timedelta
        data = {**item, "name": name, "external_id": external, "start_date": start.isoformat(), "graduation_date": (graduation or (m.add_months(start, 48) - timedelta(days=1))).isoformat(),
                "coach_name": coach, "status": status, "pgy_override": override or None, "aliases": aliases.splitlines()}
        try:
            candidate = m.save_resident(state, data, _actor())
            if _persist(candidate, store, "Resident saved."):
                st.rerun()
        except m.ValidationError as exc:
            st.error(str(exc))
    if item:
        with st.expander("Remove this resident"):
            record_count = sum(r["resident_id"] == chosen for r in state["records"])
            review_count = sum(r["resident_id"] == chosen for r in state["reviews"])
            annual_count = sum(r["resident_id"] == chosen for r in state.get("annual_progress", []))
            st.text(f"Resident: {item['name']}")
            if record_count or review_count or annual_count:
                evidence_label = "entry" if record_count == 1 else "entries"
                review_label = "review" if review_count == 1 else "reviews"
                st.warning(f"This removes the resident, {record_count} saved evidence {evidence_label}, and {review_count} CCC {review_label} from the dashboard, including all saved versions of those reviews.")
                if annual_count:
                    st.write(f"It also removes {annual_count} yearly progress entries and their saved value history.")
            else:
                st.write("This resident has no saved evidence or CCC reviews. Removing them deletes this roster entry.")
            st.caption("For a real resident whose history should stay available, change their status to Inactive instead. Removal keeps original encrypted uploads, older backups, and previous repository versions.")
            confirmed = st.checkbox("I understand and want to remove this resident", key=f"confirm_remove_{chosen}_{state['revision']}")
            if st.button("Remove resident", disabled=not confirmed or bool(st.session_state.get("pending_save"))):
                try:
                    candidate = m.remove_resident(state, chosen, _actor())
                    if _persist(candidate, store, "Resident removed. The roster and dashboards are updated."):
                        st.rerun()
                except m.ValidationError as exc:
                    st.error(str(exc))
    with st.expander("Add the roster from a CSV"):
        st.caption("The roster needs the resident's name and training start date. Dates use YYYY-MM-DD. Existing residents are matched by name or a saved name variation before updating. No record ID is required.")
        uploaded = st.file_uploader("Roster CSV", type=["csv"], key="roster_upload")
        if uploaded:
            try:
                if uploaded.size > 1024 * 1024:
                    raise m.ValidationError("The roster file must be under 1 MB.")
                df = pd.read_csv(io.BytesIO(uploaded.getvalue()), dtype=str, keep_default_na=False)
                allowed = {"name", "external_id", "start_date", "graduation_date", "coach_name", "status", "aliases"}
                if set(df.columns) - allowed:
                    raise m.ValidationError("The roster contains unsupported columns. Use the blank roster template; internal record IDs are not editable through CSV.")
                if not {"name", "start_date"}.issubset(df.columns) or len(df) > 500:
                    raise m.ValidationError("Use the roster template with name and start_date columns, and at most 500 residents.")
                st.dataframe(df, hide_index=True, width="stretch")
                approved = st.checkbox("I checked this roster and the dates")
                if st.button("Save roster", disabled=not approved):
                    candidate = copy.deepcopy(state)
                    for _, row in df.iterrows():
                        item = row.to_dict()
                        target = m.match_resident(candidate, item["name"], item.get("external_id", ""))
                        if not target:
                            named = [r for r in candidate["residents"] if m.normalize_name(item["name"]) in
                                     {m.normalize_name(n) for n in [r["name"], *r.get("aliases", [])]}]
                            if named:
                                raise m.ValidationError("A roster name cannot be matched unambiguously. Edit that resident in the roster form before importing this CSV.")
                        if target:
                            item = {**m.resident_by_id(candidate, target), **item, "id": target}
                        if "aliases" in df.columns:
                            item["aliases"] = [x.strip() for x in str(row.get("aliases", "")).split(";") if x.strip()]
                        else:
                            item.setdefault("aliases", [])
                        if not item.get("status"):
                            item["status"] = "Active"
                        candidate = m.save_resident(candidate, item, _actor())
                    if _persist(candidate, store, f"Roster saved with {len(df)} entries."):
                        st.rerun()
            except (ValueError, UnicodeError) as exc:
                st.error(str(exc) if isinstance(exc, m.ValidationError) else "The roster could not be read. Use the blank CSV template and check the dates.")


def _archive_management(state: dict, store: GitHubStore, keys: list[str]) -> None:
    st.subheader("Uploads and backups")
    if state["sources"]:
        sources = sorted(state["sources"], key=lambda s: s.get("uploaded_at", ""), reverse=True)
        st.dataframe(pd.DataFrame([{"File": s["name"], "Category": m.CATEGORIES.get(s.get("kind"), ""), "Specialty": s.get("specialty", ""),
                                    "Saved": _pretty_time(s.get("uploaded_at")), "Original copy": "Encrypted" if s.get("blob_id") else "Not retained",
                                    "Status": "Active" if s.get("active", True) else "Excluded"} for s in sources]), hide_index=True, width="stretch")
        active = [s for s in sources if s.get("active", True)]
        if active:
            with st.expander("Exclude an incorrect upload"):
                labels = {s["id"]: f"{s['name']} · {s.get('specialty', '')} · {s.get('period_end', '')}" for s in active}
                choice = st.selectbox("Upload to exclude", list(labels), format_func=lambda x: labels[x])
                reason = st.text_input("Why should this upload be excluded?")
                st.caption("Its evidence is removed from current dashboard totals. The source, audit history, and finalized review snapshots remain saved.")
                confirm = st.checkbox("Exclude all current entries from this upload")
                if st.button("Exclude upload", disabled=not confirm):
                    try:
                        candidate = m.deactivate_source(state, choice, _actor(), reason)
                        if _persist(candidate, store, "Upload excluded from current views."):
                            st.rerun()
                    except m.ValidationError as exc:
                        st.error(str(exc))
    st.markdown("**Encrypted dashboard backup**")
    st.caption("Includes resident records, imported evidence, reviews and change history. Original file copies remain separately encrypted in the data repository; this download does not contain those copies.")
    st.download_button("Download encrypted dashboard backup", encrypt_state(state, keys),
                       "resident-dashboard-backup.enc", "application/octet-stream")
    with st.expander("Restore an encrypted dashboard backup"):
        file = st.file_uploader("Encrypted backup", type=["enc"], key="restore_upload")
        if file:
            try:
                restored = decrypt_state(file.getvalue(), keys)
                m.validate_state(restored)
                st.write(f"Backup: {len(restored['residents'])} residents, {len(restored['records'])} evidence entries, and {len(restored['reviews'])} reviews.")
                st.write(f"Current dashboard: {len(state['residents'])} residents, {len(state['records'])} evidence entries, and {len(state['reviews'])} reviews.")
                st.warning("Restoring replaces the current dashboard. Save a current backup first. Original-file references work only when those encrypted files remain in the same repository and path.")
                typed = st.text_input("Type RESTORE to replace the dashboard")
                kept = st.checkbox("I saved a backup of the current dashboard")
                if st.button("Restore this backup", disabled=typed != "RESTORE" or not kept):
                    candidate = copy.deepcopy(restored)
                    audit_ids = {a.get("id") for a in candidate["audit"]}
                    candidate["audit"].extend(copy.deepcopy(a) for a in state["audit"] if a.get("id") not in audit_ids)
                    candidate = m.with_audit(candidate, _actor(), "Encrypted backup restored", previous_revision=state.get("revision"))
                    if _persist(candidate, store, "Encrypted backup restored."):
                        st.session_state.pop("upload_preview", None)
                        st.rerun()
            except (StorageError, m.ValidationError):
                st.error("This backup could not be read with the configured encryption keys, or its records are incomplete. The current dashboard has not changed.")


def _administration(state: dict, store: GitHubStore, keys: list[str], users: dict, gh: dict) -> None:
    if not _permission("manage"):
        st.info("Administration is available to administrators.")
        return
    st.header("Administration")
    tabs = st.tabs(["Residents", "Uploads and backups", "Program settings", "Help and templates"])
    with tabs[0]:
        _roster(state, store)
    with tabs[1]:
        _archive_management(state, store, keys)
    with tabs[2]:
        with st.form("program_settings"):
            program_name = st.text_input("Program name on reports", value=state["settings"].get("program_name", "Medicine Pediatrics Residency"))
            save = st.form_submit_button("Save program settings", type="primary")
        if save:
            if not program_name.strip():
                st.error("Enter the program name.")
            else:
                candidate = copy.deepcopy(state)
                candidate["settings"]["program_name"] = program_name.strip()
                candidate = m.with_audit(candidate, _actor(), "Program settings saved")
                if _persist(candidate, store, "Program settings saved."):
                    st.rerun()
        st.subheader("Account access")
        st.dataframe(pd.DataFrame([{"Account": name, "Name": user["display_name"], "Role": user["role"], "Enabled": user.get("enabled", True)} for name, user in users.items()]), hide_index=True, width="stretch")
        st.caption("The app owner manages accounts and password hashes in Streamlit Secrets. Use scripts/generate_secrets.py to add a user without changing the encryption key.")
        with st.expander("Data connection"):
            st.text(f"Data repository: {gh.get('owner', '')}/{gh.get('repo', '')}\nBranch: {gh.get('branch', 'main')}\nEncrypted dashboard: {gh.get('path', '')}")
            if st.button("Check the saved data connection"):
                try:
                    live_state, sha = store.load()
                    if live_state is not None:
                        m.validate_state(live_state)
                    if sha == st.session_state.get("sha"):
                        st.success("Connection works. This dashboard has the current saved version.")
                    else:
                        st.info("Connection works. A newer saved version is available; use Refresh saved data in the sidebar.")
                except (StorageError, m.ValidationError) as exc:
                    st.error(str(exc))
        st.caption(f"App version {__version__} · The dashboard shows recorded evidence. Word reports include notebook calculations for committee review.")
    with tabs[3]:
        st.subheader("Daily routine")
        st.markdown("1. Export whichever reports are ready in New Innovations.\n2. Open Upload files, choose the specialty and dates, and add one or more files. Leave unused areas empty.\n3. Process the selected files, review the entries and resident matches, then save.\n4. Open Yearly progress to enter exam results, clinic patient tallies, and observed encounters.\n5. Open Resident review to review evidence and document the CCC discussion.\n6. Open Word reports to download a CCC summary or letter.")
        st.subheader("Compatibility templates")
        st.caption("Use these only when the standard export is unavailable or a PDF has no reliable score labels. Keep one resident or observation per row and use YYYY-MM-DD dates.")
        templates = Path(__file__).resolve().parents[1] / "templates"
        for file in sorted(templates.glob("*.csv")):
            st.download_button(file.stem.replace("_", " ").capitalize() + " template", file.read_bytes(), file.name, "text/csv", key="template_" + file.stem)
        st.subheader("Review periods and missing information")
        st.write("Midyear defaults to the first six months; Year end defaults to the full training year. You can adjust the dates for an individual resident. Missing evidence and unassessed milestones stay visible as missing, and broader-period summaries are not prorated into smaller date windows.")
        st.subheader("Moving the old REDCap history")
        st.write("The dashboard PDF describes the old layout; it is not a complete data export. Historical evidence can be loaded from the original New Innovations files. Existing CCC narratives can be entered into dated reviews. A bulk REDCap-history migration needs its CSV export and data dictionary so fields and residents can be mapped and checked.")


def _sidebar(state: dict) -> str:
    with st.sidebar:
        st.title("Med-Peds CCC")
        identity = st.session_state.get("identity", {})
        st.text(identity.get("display_name", "Fictional preview"))
        if st.session_state.get("demo"):
            pages = ["Program dashboard", "Yearly progress", "Resident review", "Word reports"]
            st.caption("Fictional data only. Uploads and saving are unavailable.")
        else:
            pages = ["Program dashboard"]
            if _permission("upload"):
                pages.append("Upload files")
            pages.extend(["Yearly progress", "Resident review", "Word reports"])
            if _permission("manage"):
                pages.append("Administration")
        if st.session_state.get("nav_target") in pages:
            st.session_state["navigation"] = st.session_state.pop("nav_target")
        if st.session_state.get("navigation") not in pages:
            st.session_state["navigation"] = pages[0]
        page = st.radio("Go to", pages, key="navigation")
        st.divider()
        if st.session_state.get("pending_save"):
            st.warning("Changes not saved")
        else:
            st.success("Saved" if not st.session_state.get("demo") else "Fictional preview")
        st.caption(_pretty_time(state.get("updated_at", "")))
        if not st.session_state.get("demo"):
            if st.button("Refresh saved data", disabled=bool(st.session_state.get("pending_save")), width="stretch"):
                st.session_state.pop("state", None)
                st.session_state.pop("generated_export", None)
                st.rerun()
            if st.button("Sign out", width="stretch"):
                _clear_session()
                st.rerun()
    return page


def run() -> None:
    _configure()
    try:
        users, gh, keys, config = _read_config()
    except SecurityConfigError as exc:
        st.title("Med-Peds Resident Progress")
        st.info("Setup is required before resident information can be opened.")
        st.write(str(exc))
        st.caption("The app owner should follow README.md and add the generated settings to Streamlit Secrets. No data connection is attempted until sign-in succeeds.")
        st.stop()
    _login(users, config)
    # A configuration change must never move the old session's records to a
    # different repository or re-encrypt them with an unintended key.
    binding = hashlib.sha256(json.dumps({k: gh.get(k) for k in ("owner", "repo", "branch", "path")}, sort_keys=True).encode() + "|".join(keys).encode()).hexdigest()
    if st.session_state.get("storage_binding") not in (None, binding):
        for key in list(st.session_state):
            if key not in {"identity"}:
                del st.session_state[key]
    st.session_state["storage_binding"] = binding
    st.session_state["encryption_keys"] = keys
    try:
        store = GitHubStore(gh, keys)
        state = _load(store)
    except (StorageError, m.ValidationError) as exc:
        st.title("Resident records could not be opened")
        st.error(str(exc))
        st.write("No new empty dashboard has been created. Check the data connection or encryption settings, then try again.")
        if st.button("Try opening saved records again"):
            st.session_state.pop("state", None)
            st.rerun()
        if st.button("Sign out"):
            _clear_session()
            st.rerun()
        st.stop()
    if state is None:
        st.title("Start the resident dashboard")
        st.write("Encrypted storage is connected. No dashboard has been saved at the configured location yet.")
        if _permission("manage"):
            st.caption("If this app should already have resident records, check the repository and path before creating a new dashboard.")
            if st.button("Create an empty resident dashboard", type="primary"):
                candidate = m.new_state(config.get("program_name", "Medicine Pediatrics Residency"))
                if _persist(candidate, store, "Dashboard created. Add the resident roster to begin."):
                    st.rerun()
            _save_problem(store, keys)
        else:
            st.info("An administrator needs to initialize the dashboard.")
        if st.button("Sign out"):
            _clear_session()
            st.rerun()
        st.stop()
    st.title("Med-Peds Resident Progress")
    st.caption("Evidence, CCC reviews and resident letters in one place.")
    notice = st.session_state.pop("notice", None)
    if notice:
        st.success(notice)
    _save_problem(store, keys)
    page = _sidebar(state)
    if page == "Program dashboard":
        _program_dashboard(state)
    elif page == "Upload files":
        _uploads(state, store)
    elif page == "Yearly progress":
        _yearly_progress_page(state, store)
    elif page == "Resident review":
        _resident_page(state, store)
    elif page == "Word reports":
        _word_reports(state)
    elif page == "Administration":
        _administration(state, store, keys, users, gh)


def run_demo() -> None:
    from ccc.demo import create_demo_state
    _configure()
    if not st.session_state.get("demo"):
        _clear_session()
    st.session_state["demo"] = True
    if "state" not in st.session_state:
        st.session_state["state"] = create_demo_state(_today())
    state = st.session_state["state"]
    st.title("Med-Peds Resident Progress")
    st.info("Fictional preview. All names, scores and comments are examples. This preview cannot accept uploads or save records.")
    page = _sidebar(state)
    if page == "Program dashboard":
        _program_dashboard(state)
    elif page == "Yearly progress":
        _yearly_progress_page(state, None)
    elif page == "Resident review":
        _resident_page(state, None)
    elif page == "Word reports":
        _word_reports(state)
