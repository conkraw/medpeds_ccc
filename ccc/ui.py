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


def _clear_session() -> None:
    for key in list(st.session_state):
        del st.session_state[key]


def _escape(value: object) -> str:
    """Display uploaded text literally in Markdown-capable widget labels."""
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>$-])", r"\\\1", str(value))


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


def _range_controls(state: dict, *, prefix: str) -> tuple[str, str]:
    today = _today(state)
    a, b = st.columns(2)
    with a:
        start = st.date_input("Reporting period starts", value=m.current_academic_start(today), key=prefix + "_start")
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
    lookup = {r["id"]: m.resident_label(r) for r in residents}
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


def _program_dashboard(state: dict) -> None:
    st.header("Program dashboard")
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
                                         "Specialty": s.get("specialty", ""), "Through": s.get("period_end", ""),
                                         "Entries": s.get("record_count", 0), "Status": "Active" if s.get("active", True) else "Excluded"} for s in items]),
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
    st.caption("Medicine and Pediatrics use different milestone sets. Unassessed items remain blank; the app does not translate scores into readiness decisions.")


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
        for key, label in m.REVIEW_TEXT_FIELDS.items():
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
        edited = {}
        st.subheader("Progress and next steps")
        for key in ("overall_impression", "strengths", "areas_for_growth", "patterns_over_time", "ccc_summary", "ccc_decision", "action_plan"):
            edited[key] = st.text_area(m.REVIEW_TEXT_FIELDS[key], value=fields.get(key, ""), height=90)
        a, b = st.columns(2)
        edited["mentor"] = a.text_input(m.REVIEW_TEXT_FIELDS["mentor"], value=fields.get("mentor", ""))
        edited["coach_name"] = b.text_input(m.REVIEW_TEXT_FIELDS["coach_name"], value=fields.get("coach_name", resident.get("coach_name", "")))
        a, b = st.columns(2)
        edited["follow_up_date"] = a.text_input("Action plan follow-up date", value=fields.get("follow_up_date", ""), placeholder="YYYY-MM-DD or not yet scheduled")
        edited["next_review_date"] = b.text_input("Next review date", value=fields.get("next_review_date", ""), placeholder="YYYY-MM-DD or not yet scheduled")
        with st.expander("Clinic visits, exams, observed encounters and scholarship"):
            st.caption("Leave unavailable results blank. These are recorded program values, not automatically verified thresholds.")
            keys = list(m.REVIEW_VALUE_FIELDS)
            for i in range(0, len(keys), 3):
                cols = st.columns(3)
                for col, key in zip(cols, keys[i:i + 3]):
                    edited[key] = col.text_input(m.REVIEW_VALUE_FIELDS[key], value=str(fields.get(key, "")))
            for key in ("rotation_summary", "observed_encounters_notes", "abstracts", "publications", "advocacy", "nursing_feedback", "resident_comments"):
                edited[key] = st.text_area(m.REVIEW_TEXT_FIELDS[key], value=fields.get(key, ""), height=80)
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
    st.header("Upload files")
    st.write("Choose the specialty and reporting dates, add the New Innovations files, then review the import.")
    if not state["residents"]:
        st.info("First add the resident roster under Administration so names can be matched correctly.")
        return
    specialty = st.radio("These files are for", m.SPECIALTIES, horizontal=True, key="upload_specialty")
    st.caption("Upload Medicine and Pediatrics files as separate batches. Choose Combined for shared administrative reports.")
    first, last = _range_controls(state, prefix="upload_range")
    groups = []
    cols = st.columns(4)
    with cols[0], st.container(border=True):
        st.subheader("1. Evaluations")
        kind = st.selectbox("Evaluation report", ["evaluations", "self_assessments"], format_func=lambda x: m.CATEGORIES[x], key="kind_eval")
        files = st.file_uploader("Add evaluation files", type=["csv", "xlsx", "xls", "tab", "tsv"], accept_multiple_files=True, key="files_eval")
        groups.append((kind, files))
        st.caption("Faculty evaluations or resident self-assessments.")
    with cols[1], st.container(border=True):
        st.subheader("2. Milestones")
        st.caption("Medicine or Pediatrics")
        files = st.file_uploader("Add milestone files", type=["pdf", "csv", "xlsx", "xls"], accept_multiple_files=True, key="files_milestones")
        groups.append(("milestones", files))
        st.caption("PDFs with labeled scores, or a spreadsheet export.")
    with cols[2], st.container(border=True):
        st.subheader("3. Activities")
        kind = st.selectbox("Activity report", ["procedures", "conferences"], format_func=lambda x: m.CATEGORIES[x], key="kind_activity")
        files = st.file_uploader("Add activity files", type=["csv", "xlsx", "xls", "tsv"], accept_multiple_files=True, key="files_activity")
        groups.append((kind, files))
        st.caption("Procedure logs or conference attendance.")
    with cols[3], st.container(border=True):
        st.subheader("4. Duty hours")
        kind = st.selectbox("Duty-hour report", ["duty_hours", "duty_violations"], format_func=lambda x: "Shift log" if x == "duty_hours" else "Violation report", key="kind_duty")
        files = st.file_uploader("Add duty-hour files", type=["csv", "xlsx", "xls", "tsv"], accept_multiple_files=True, key="files_duty")
        groups.append((kind, files))
        st.caption("New Innovations shift logs and reported concerns.")
    count = sum(len(files) for _, files in groups)
    upload_context = (specialty, first, last, tuple((kind, f.name, hashlib.sha256(f.getvalue()).hexdigest()) for kind, files in groups for f in files))
    if st.button("Review these files", type="primary", disabled=count == 0, width="stretch"):
        if count > 30 or sum(f.size for _, files in groups for f in files) > 60 * 1024 * 1024:
            st.error("Add up to 30 files and 60 MB in one batch. Each file can be up to 15 MB.")
            return
        previews = []
        with st.spinner("Reading files and checking the fields…"):
            for kind, files in groups:
                for file in files:
                    data = file.getvalue()
                    result = parse_upload(file.name, data, kind, specialty, period_start=first, period_end=last)
                    source = {"id": m.new_id(), "name": file.name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                              "kind": kind, "specialty": specialty, "period_start": first, "period_end": last, "profile": result.profile}
                    duplicate = any(s.get("active", True) and all(s.get(k) == source[k] for k in ("sha256", "kind", "specialty", "period_start", "period_end")) for s in state["sources"])
                    previews.append({"source": source, "data": data, "records": result.records, "errors": result.errors,
                                     "warnings": result.warnings, "duplicate": duplicate})
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
        st.warning("The files, report types, specialty or dates have changed. Select Review these files again to update the preview. If you returned from another page, add the same files again.")
        return
    st.info("Not saved yet. Check the files, resident names, and reporting dates below.")
    included = []
    for i, preview in enumerate(previews):
        source = preview["source"]
        with st.container(border=True):
            st.text(source["name"])
            if preview["duplicate"]:
                st.success("This file and period are already saved. It will be skipped.")
                continue
            for error in preview["errors"]:
                st.error(_escape(error))
            for warning in preview["warnings"]:
                st.warning(_escape(warning))
            usable = bool(preview["records"]) and not preview["errors"]
            keep = st.checkbox(f"Include this file · {len(preview['records'])} entries", value=usable, disabled=not usable,
                               key="include_" + source["id"])
            if keep:
                included.append(preview)
            if preview["records"]:
                with st.expander("Show parsed entries"):
                    st.dataframe(_evidence_table(preview["records"]), hide_index=True, width="stretch")
    if not included:
        return
    all_records = [r for p in included for r in p["records"]]
    names = {}
    for record in all_records:
        names.setdefault(m.mapping_key(record), record)
    st.markdown("**Confirm the residents**")
    roster_ids = [r["id"] for r in sorted(state["residents"], key=lambda r: r["name"].casefold())]
    roster_labels = {r["id"]: m.resident_label(r) for r in state["residents"]}
    mappings = {}
    for key, record in names.items():
        auto = m.match_resident(state, record.get("resident_name", ""), record.get("external_id", ""))
        options = ["__choose__", *roster_ids, "__skip__"]
        label = str(record.get("resident_name") or "Unnamed resident")
        if record.get("external_id"):
            label += " · source ID " + str(record["external_id"])
        mappings[key] = st.selectbox(_escape(label), options, index=options.index(auto) if auto in options else 0,
                                     format_func=lambda x: "Choose a resident" if x == "__choose__" else "Skip these entries" if x == "__skip__" else roster_labels[x],
                                     key="map_" + hashlib.sha256(key.encode()).hexdigest()[:16])
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
    try:
        _, stats = m.prepare_records(state, all_records, mappings)
        possible = stats["possible_updates"]
        st.caption(f"{stats['new_records']} new entries before any selected replacement; {stats['duplicates']} exact saved duplicates will be skipped.")
        if possible:
            st.warning(f"{possible} entries resemble saved evidence with changed content. Choose a complete replacement above, or confirm they should be kept as additional evidence.")
    except m.ValidationError:
        pass
    allow_updates = st.checkbox("I checked overlapping entries and want to keep any remaining revisions as additional evidence", disabled=possible == 0)
    acknowledged = st.checkbox("The included files, resident matches, dates, and any warnings are correct")
    if st.button("Save to resident dashboards", type="primary", disabled=not acknowledged or "__choose__" in mappings.values(), width="stretch"):
        if st.session_state.get("pending_save"):
            st.error("Resolve the earlier save problem before importing another batch.")
            return
        try:
            candidate = copy.deepcopy(state)
            saved_sources = []
            for preview in included:
                source = copy.deepcopy(preview["source"])
                source["warnings"] = list(preview["warnings"])
                replace_id = replacements.get(source["id"])
                candidate, stats = m.import_records(candidate, preview["records"], source, mappings, _actor(),
                                                     replace_source_id=replace_id if replace_id != "__none__" else None,
                                                     allow_possible_updates=allow_updates)
                saved_sources.append((preview, source["id"]))
            if remember:
                for key, record in names.items():
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
            if _persist(candidate, store, f"Saved {len(saved_sources)} upload(s). Resident dashboards are updated."):
                st.session_state.pop("upload_preview", None)
                st.rerun()
        except (m.ValidationError, StorageError) as exc:
            st.error("Not saved to the dashboard. " + str(exc))


def _word_reports(state: dict) -> None:
    from ccc.reports import build_resident_report, build_ccc_letter
    from ccc.export_security import protect_docx, ExportSecurityError
    if st.session_state.pop("clear_export_passwords", False):
        st.session_state.pop("export_password", None)
        st.session_state.pop("export_confirmation", None)
    st.header("Word reports")
    export_error = st.session_state.pop("export_error", None)
    if export_error:
        st.error(export_error)
    st.write("Create an editable progress report or CCC letter for one resident or the whole group.")
    if not _permission("export") and not st.session_state.get("demo"):
        st.info("Your account cannot export reports.")
        return
    residents = sorted(state["residents"], key=lambda r: r["name"].casefold())
    if not residents:
        st.info("Add the resident roster first.")
        return
    scope = st.radio("Who is this for?", ["One resident", "Several residents"], horizontal=True)
    document_type = st.radio("Document", ["Progress report", "CCC letter"], horizontal=True)
    review = None
    bulk_basis = "Current dashboard date range"
    if scope == "One resident":
        resident = _pick_resident(state, "export_resident")
        selected_residents = [resident]
        reviews = sorted([r for r in state["reviews"] if r["resident_id"] == resident["id"]], key=lambda r: r["review_date"], reverse=True)
        review_map = {r["id"]: r for r in reviews}
        for r in reviews:
            for n, old in enumerate(r.get("versions", []), 1):
                review_map[f"{r['id']}::version-{n}"] = old
        options = (["__live__"] if document_type == "Progress report" else []) + list(review_map)
        if not options:
            st.info("Save a CCC review before generating its letter.")
            return
        selected_review = st.selectbox("Use evidence from", options,
                                        format_func=lambda x: "Current dashboard date range" if x == "__live__" else f"PGY{review_map[x]['pgy']} {review_map[x]['half']} · {review_map[x]['academic_year']} · {review_map[x]['status']}" + (" · prior version" if "::version" in x else ""))
        review = review_map.get(selected_review)
        if review:
            start, end = review["period_start"], review["period_end"]
            st.caption(f"Evidence period: {start} to {end}. " + ("This finalized review uses its preserved evidence." if review["status"] == "Final" else "This draft uses current saved evidence."))
        else:
            start, end = _range_controls(state, prefix="export_range")
    else:
        ids = [r["id"] for r in residents]
        labels = {r["id"]: m.resident_label(r) for r in residents}
        selected = st.multiselect("Choose residents", ids, default=[r["id"] for r in residents if r.get("status") == "Active"], format_func=lambda x: labels[x])
        selected_residents = [r for r in residents if r["id"] in selected]
        start, end = _range_controls(state, prefix="export_group_range")
        if document_type == "CCC letter":
            st.caption("Each letter uses that resident's latest saved review with a meeting date in the selected range. Missing reviews block the batch so nobody is silently omitted.")
            bulk_basis = "Latest saved review in this range"
        else:
            bulk_basis = st.radio("Group report basis", ["Latest saved review in this range", "Current dashboard date range"], horizontal=True)
            if bulk_basis == "Latest saved review in this range":
                st.caption("Each report uses that resident's latest review with a meeting date in this range, including its CCC assessment and evidence period. Missing reviews block the batch.")
    details = st.checkbox("Include all evaluation comments and detailed evidence", value=True, disabled=document_type == "CCC letter")
    protected = st.radio("Download protection", ["Password-protected Word", "Standard Word"], horizontal=True) == "Password-protected Word"
    if protected:
        st.caption("Use a separate passphrase of at least 14 characters. Word will ask for it when the file is opened.")
    else:
        st.info("Standard Word files are readable after download. Keep them in the program's approved secure location.")
    context = json.dumps({"revision": state["revision"], "residents": [r["id"] for r in selected_residents], "review": selected_review if scope == "One resident" else None,
                           "start": start, "end": end, "type": document_type, "protected": protected, "details": details, "basis": bulk_basis}, sort_keys=True)
    with st.form("word_export_form", clear_on_submit=True):
        if protected:
            a, b = st.columns(2)
            password = a.text_input("Export passphrase", type="password", max_chars=1024, key="export_password")
            confirmation = b.text_input("Confirm export passphrase", type="password", max_chars=1024, key="export_confirmation")
        else:
            password = confirmation = ""
        submitted = st.form_submit_button("Prepare Word download", type="primary", width="stretch")
    if submitted:
        st.session_state.pop("generated_export", None)
        if not selected_residents:
            st.session_state["export_error"] = "Choose at least one resident."
            st.session_state["clear_export_passwords"] = True
            st.rerun()
        if protected and (len(password) < 14 or password != confirmation):
            st.session_state["export_error"] = "Enter matching export passphrases of at least 14 characters."
            st.session_state["clear_export_passwords"] = True
            st.rerun()
        outputs = []
        try:
            with st.spinner("Preparing Word documents…"):
                for resident in selected_residents:
                    chosen_review = review
                    if scope == "Several residents" and bulk_basis == "Latest saved review in this range":
                        matches = sorted([r for r in state["reviews"] if r["resident_id"] == resident["id"] and start <= r["review_date"] <= end], key=lambda r: r["review_date"])
                        if not matches:
                            raise m.ValidationError("A selected resident has no review in this period. Save their review or remove them from this batch.")
                        chosen_review = matches[-1]
                    export_resident = chosen_review.get("snapshot_resident", resident) if chosen_review and chosen_review.get("status") == "Final" else resident
                    program_name = state["settings"].get("program_name", "Medicine Pediatrics Residency")
                    if document_type == "CCC letter":
                        plain = build_ccc_letter(export_resident, chosen_review, program_name=program_name)
                    else:
                        selected_start = chosen_review["period_start"] if chosen_review else start
                        selected_end = chosen_review["period_end"] if chosen_review else end
                        plain = build_resident_report(export_resident, m.active_records(state), state["reviews"],
                                                       period_start=selected_start, period_end=selected_end, review=chosen_review,
                                                       program_name=program_name, include_details=details)
                    data = protect_docx(plain, password) if protected else plain
                    # Password-protected contents do not protect filesystem names.
                    filename = "resident-" + resident["id"][:8] + ("-ccc-letter.docx" if document_type == "CCC letter" else "-progress.docx")
                    outputs.append((filename, data))
            if len(outputs) == 1:
                filename, payload = outputs[0]
                mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            else:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
                    for filename, data in outputs:
                        z.writestr(filename, data)
                payload, filename, mime = buffer.getvalue(), "resident-word-reports.zip", "application/zip"
            st.session_state["generated_export"] = {"data": payload, "filename": filename, "mime": mime, "context": context, "protected": protected}
        except (ValueError, ExportSecurityError) as exc:
            st.session_state["export_error"] = str(exc) if isinstance(exc, (m.ValidationError, ExportSecurityError)) else "The Word file could not be prepared. Check the selected review and evidence."
        finally:
            password = confirmation = ""
            st.session_state["clear_export_passwords"] = True
        st.rerun()
    generated = st.session_state.get("generated_export")
    if generated and generated["context"] == context:
        st.success("Your Word download is ready." + (" Each document requires your export passphrase." if generated["protected"] else ""))
        st.download_button("Download Word file" if scope == "One resident" else "Download Word files", generated["data"], generated["filename"], generated["mime"], type="primary", width="stretch")


def _roster(state: dict, store: GitHubStore) -> None:
    st.subheader("Resident roster")
    existing = sorted(state["residents"], key=lambda r: r["name"].casefold())
    if existing:
        st.dataframe(pd.DataFrame([{"Name": r["name"], "New Innovations ID": r.get("external_id", ""), "Start": r["start_date"],
                                    "Graduation": r.get("graduation_date", ""), "Status": r.get("status", "Active")} for r in existing]), hide_index=True, width="stretch")
    lookup = {r["id"]: r for r in existing}
    chosen = st.selectbox("Add or edit a resident", ["__new__", *lookup], format_func=lambda x: "Add a resident" if x == "__new__" else m.resident_label(lookup[x]))
    item = lookup.get(chosen, {})
    with st.form("roster_form_" + chosen):
        a, b = st.columns(2)
        name = a.text_input("Resident name", value=item.get("name", ""))
        external = b.text_input("New Innovations resident ID", value=item.get("external_id", ""), help="Use the stable resident ID if your export supplies one.")
        a, b = st.columns(2)
        start = a.date_input("Training start date", value=m.parse_date(item.get("start_date")) or m.current_academic_start(_today(state)))
        graduation = b.date_input("Expected graduation date", value=m.parse_date(item.get("graduation_date")), help="Leave blank to calculate four years from the training start date.")
        coach = st.text_input("Faculty coach", value=item.get("coach_name", ""))
        a, b = st.columns(2)
        status = a.selectbox("Resident status", ["Active", "Graduated", "Inactive"], index=["Active", "Graduated", "Inactive"].index(item.get("status", "Active")))
        override = b.selectbox("Current training year", [0, 1, 2, 3, 4], index=int(item.get("pgy_override") or 0), format_func=lambda x: "Calculate from start date" if x == 0 else f"PGY{x} override")
        aliases = st.text_area("Other names used in exports", value="\n".join(item.get("aliases", [])), help="One exact name variation per line. Confirmed matches can also be saved during upload.")
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
    with st.expander("Add the roster from a CSV"):
        st.caption("Use the blank roster template below. Dates use YYYY-MM-DD. Existing residents with matching New Innovations IDs are updated after preview.")
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
        st.caption(f"App version {__version__} · No automatic readiness or duty-hour compliance decisions.")
    with tabs[3]:
        st.subheader("Daily routine")
        st.markdown("1. Export the reports from New Innovations.\n2. Open Upload files and choose the specialty and dates.\n3. Review the entries and match the residents, then save.\n4. Open Resident review to review evidence and document the CCC discussion.\n5. Open Word reports to prepare reports or letters.")
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
            pages = ["Program dashboard", "Resident review", "Word reports"]
            st.caption("Fictional data only. Uploads and saving are unavailable.")
        else:
            pages = ["Program dashboard"]
            if _permission("upload"):
                pages.append("Upload files")
            pages.extend(["Resident review", "Word reports"])
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
    elif page == "Resident review":
        _resident_page(state, None)
    elif page == "Word reports":
        _word_reports(state)
