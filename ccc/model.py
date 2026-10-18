"""Pure record operations. Persistence is a separate, encrypted transaction.

Every mutation returns a new state. The UI must not replace its saved state
until GitHub confirms the compare-and-swap write. Imported evidence is never
used to invent a CCC decision or an ACGME compliance conclusion.
"""
from __future__ import annotations

import calendar
import copy
import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

SCHEMA_VERSION = 1
CATEGORIES = {
    "evaluations": "Evaluations",
    "self_assessments": "Self-assessments",
    "milestones": "Milestones",
    "procedures": "Procedures",
    "conferences": "Conference attendance",
    "duty_hours": "Duty hours",
    "duty_violations": "Duty-hour concerns",
}
SPECIALTIES = ["Medicine", "Pediatrics", "Combined"]
HALVES = ["Midyear", "Year end"]
REVIEW_TEXT_FIELDS = {
    "coach_name": "Faculty coach",
    "rotation_summary": "Rotations and learning experiences",
    "observed_encounters_notes": "Observed encounter feedback",
    "abstracts": "Abstracts and presentations",
    "publications": "Publications and scholarship",
    "advocacy": "Advocacy and service",
    "nursing_feedback": "Nursing and team feedback",
    "overall_impression": "Overall progress",
    "strengths": "Strengths",
    "areas_for_growth": "Areas for growth",
    "patterns_over_time": "Patterns over time",
    "ccc_summary": "CCC discussion",
    "ccc_decision": "CCC assessment or decision",
    "action_plan": "Action plan and how progress will be assessed",
    "mentor": "Person supporting the action plan",
    "follow_up_date": "Action plan follow-up date",
    "next_review_date": "Next review date",
    "resident_comments": "Resident comments",
}
REVIEW_VALUE_FIELDS = {
    "adult_clinic_count": "Adult continuity clinic visits",
    "pediatric_clinic_count": "Pediatric continuity clinic visits",
    "observed_encounters_completed": "Observed encounters completed",
    "medicine_ite_score": "Medicine ITE score",
    "medicine_ite_percentile": "Medicine ITE percentile",
    "pediatrics_ite_score": "Pediatrics ITE score",
    "pediatrics_ite_percentile": "Pediatrics ITE percentile",
    "usmle_step1": "USMLE Step 1",
    "usmle_step2ck": "USMLE Step 2 CK",
    "usmle_step3": "USMLE Step 3",
}


class ValidationError(ValueError):
    """Safe, actionable model validation failure."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def iso_date(value: Any, label: str = "Date") -> str:
    parsed = parse_date(value)
    if parsed is None:
        raise ValidationError(f"{label} must be a valid date.")
    return parsed.isoformat()


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def academic_year(value: Any) -> str:
    parsed = parse_date(value)
    if not parsed:
        raise ValidationError("Choose a date to identify the academic year.")
    start = parsed.year if parsed.month >= 7 else parsed.year - 1
    return f"{start}-{start + 1}"


def current_academic_start(today: date | None = None) -> date:
    today = today or date.today()
    return date(today.year if today.month >= 7 else today.year - 1, 7, 1)


def normalize_name(value: Any) -> str:
    # Commas express explicit family/given ordering; never sort name tokens.
    text = str(value or "").strip()
    text = re.sub(r",?\s+(?:M\.?D\.?|D\.?O\.?|MBBS|Ph\.?D\.?)\s*$", "", text, flags=re.I)
    if "," in text:
        family, given = text.split(",", 1)
        text = f"{given} {family}"
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", text, flags=re.UNICODE))


def new_state(program_name: str = "Medicine Pediatrics Residency") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "updated_at": now_iso(),
        "settings": {"program_name": program_name, "timezone": "America/New_York"},
        "residents": [], "records": [], "sources": [], "reviews": [], "audit": [],
    }


def validate_state(state: Any) -> dict:
    """Validate backups before accepting them as the live database."""
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("This archive has an unsupported format. No data were changed.")
    for key in ("residents", "records", "sources", "reviews", "audit"):
        if not isinstance(state.get(key), list):
            raise ValidationError("This archive is incomplete. No data were changed.")
    if not isinstance(state.get("settings"), dict):
        raise ValidationError("This archive has no valid program settings.")
    for key in ("residents", "records", "sources", "reviews"):
        ids = [item.get("id") for item in state[key] if isinstance(item, dict)]
        if len(ids) != len(state[key]) or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValidationError(f"This archive has invalid or duplicate {key} identifiers.")
    resident_ids = {r["id"] for r in state["residents"]}
    source_ids = {s["id"] for s in state["sources"]}
    for r in state["residents"]:
        if not str(r.get("name", "")).strip() or not parse_date(r.get("start_date")):
            raise ValidationError("Every resident needs a name and a training start date.")
        if not isinstance(r.get("aliases", []), list):
            raise ValidationError("Resident name aliases must be a list.")
    ext_ids = [str(r.get("external_id", "")).strip() for r in state["residents"] if r.get("external_id")]
    if len(ext_ids) != len(set(ext_ids)):
        raise ValidationError("A New Innovations resident ID belongs to more than one resident.")
    for r in state["records"]:
        if r.get("resident_id") not in resident_ids or r.get("source_id") not in source_ids:
            raise ValidationError("Some evidence has no matching resident or upload.")
        if r.get("category") not in CATEGORIES or r.get("specialty") not in SPECIALTIES:
            raise ValidationError("Some evidence has an unsupported category or specialty.")
        if not parse_date(r.get("date")):
            raise ValidationError("Some evidence has no valid date.")
    for r in state["reviews"]:
        if r.get("resident_id") not in resident_ids or r.get("status") not in {"Draft", "Final"}:
            raise ValidationError("Some reviews have no matching resident or valid review status.")
        if not isinstance(r.get("fields"), dict) or type(r.get("pgy")) is not int or not 1 <= r["pgy"] <= 4:
            raise ValidationError("Some reviews have invalid review fields or training year.")
        if r.get("half") not in HALVES or not parse_date(r.get("period_start")) or not parse_date(r.get("period_end")):
            raise ValidationError("Some reviews have invalid review periods.")
        if r["period_start"] > r["period_end"]:
            raise ValidationError("A review starts after its end date.")
        if r["status"] == "Final":
            if not isinstance(r.get("snapshot"), list):
                raise ValidationError("A finalized review has no preserved evidence snapshot.")
            if any(not isinstance(x, dict) or x.get("resident_id") != r["resident_id"] or x.get("category") not in CATEGORIES
                   or x.get("specialty") not in SPECIALTIES or not parse_date(x.get("date")) for x in r["snapshot"]):
                raise ValidationError("A finalized review contains another resident's evidence.")
    try:
        json.dumps(state, allow_nan=False)
    except (ValueError, TypeError):
        raise ValidationError("The archive contains unsupported data values.") from None
    return state


def with_audit(state: dict, actor: str, action: str, **details: Any) -> dict:
    state["revision"] = int(state.get("revision", 0)) + 1
    state["updated_at"] = now_iso()
    state["audit"].append({"id": new_id(), "at": state["updated_at"], "actor": actor,
                           "action": action, "details": details})
    return state


def resident_by_id(state: dict, resident_id: str) -> dict:
    for item in state["residents"]:
        if item["id"] == resident_id:
            return item
    raise ValidationError("Choose a resident from the roster.")


def match_resident(state: dict, name: str, external_id: str = "") -> str | None:
    external_id = str(external_id or "").strip()
    if external_id:
        matches = [r["id"] for r in state["residents"] if str(r.get("external_id", "")).strip() == external_id]
        if len(matches) == 1:
            return matches[0]
        # A conflicting known ID cannot be silently overridden by a name.
        if matches:
            return None
    normalized = normalize_name(name)
    if not normalized:
        return None
    matches = []
    for r in state["residents"]:
        if external_id and r.get("external_id") and str(r["external_id"]).strip() != external_id:
            continue
        choices = [r["name"], *r.get("aliases", [])]
        if normalized in {normalize_name(x) for x in choices}:
            matches.append(r["id"])
    return matches[0] if len(matches) == 1 else None


def save_resident(state: dict, data: dict, actor: str) -> dict:
    result = copy.deepcopy(state)
    item = copy.deepcopy(data)
    item["name"] = str(item.get("name", "")).strip()
    if not item["name"]:
        raise ValidationError("Enter the resident's name.")
    item["start_date"] = iso_date(item.get("start_date"), "Training start date")
    item["external_id"] = str(item.get("external_id", "")).strip()
    item["aliases"] = list(dict.fromkeys(str(a).strip() for a in item.get("aliases", []) if str(a).strip()))
    if item.get("status", "Active") not in {"Active", "Graduated", "Inactive"}:
        raise ValidationError("Choose an available resident status.")
    item.setdefault("status", "Active")
    item.setdefault("id", new_id())
    if item.get("external_id") and any(r["id"] != item["id"] and r.get("external_id") == item["external_id"] for r in result["residents"]):
        raise ValidationError("That New Innovations ID is already assigned to another resident.")
    for r in result["residents"]:
        if r["id"] == item["id"]:
            continue
        existing = {normalize_name(x) for x in [r["name"], *r.get("aliases", [])]}
        requested = {normalize_name(x) for x in item["aliases"]}
        if requested & existing:
            raise ValidationError("A saved name alias already belongs to another resident.")
    if item.get("graduation_date"):
        item["graduation_date"] = iso_date(item["graduation_date"], "Graduation date")
    if item.get("pgy_override") not in (None, "", 0):
        if not 1 <= int(item["pgy_override"]) <= 4:
            raise ValidationError("Training year must be PGY1 through PGY4.")
    item["updated_at"] = now_iso()
    old = next((i for i, r in enumerate(result["residents"]) if r["id"] == item["id"]), None)
    if old is None:
        result["residents"].append(item)
    else:
        result["residents"][old] = {**result["residents"][old], **item}
    return with_audit(result, actor, "Resident saved", resident_id=item["id"])


def current_pgy(resident: dict, as_of: Any) -> int:
    cutoff = parse_date(as_of)
    start = parse_date(resident.get("start_date"))
    if not cutoff or not start or cutoff < start:
        return 0
    override = resident.get("pgy_override")
    if override:
        return int(override)
    for year in range(1, 5):
        if cutoff < add_months(start, 12 * year):
            return year
    return 5


def checkpoint_defaults(resident: dict, pgy: int, half: str) -> dict:
    if pgy not in range(1, 5) or half not in HALVES:
        raise ValidationError("Choose PGY1 through PGY4 and a review checkpoint.")
    start = add_months(parse_date(resident["start_date"]), (pgy - 1) * 12)
    end = add_months(start, 6 if half == "Midyear" else 12) - timedelta(days=1)
    return {"pgy": pgy, "half": half, "academic_year": academic_year(start),
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "review_date": end.isoformat()}


def record_in_window(record: dict, start: Any, end: Any) -> bool:
    low, high = parse_date(start), parse_date(end)
    observed = parse_date(record.get("date")) or parse_date(record.get("period_end"))
    if not low or not high or not observed or low > high or not low <= observed <= high:
        return False
    if record.get("record_mode") == "snapshot":
        first, last = parse_date(record.get("period_start")), parse_date(record.get("period_end"))
        if first and first < low:
            return False
        if last and (last > high or (first and last < first)):
            return False
    return True


def active_records(state: dict) -> list[dict]:
    sources = {s["id"] for s in state["sources"] if s.get("active", True)}
    return [r for r in state["records"] if r["source_id"] in sources and r.get("active", True)]


def records_for(state: dict, resident_id: str, start: Any, end: Any) -> list[dict]:
    return sorted([r for r in active_records(state) if r["resident_id"] == resident_id and record_in_window(r, start, end)],
                  key=lambda r: (r.get("date", ""), r.get("category", ""), r.get("id", "")))


def coverage_exclusions(state: dict, resident_id: str, start: Any, end: Any) -> list[dict]:
    low, high = parse_date(start), parse_date(end)
    if not low or not high or low > high:
        raise ValidationError("Choose a valid reporting date range.")
    return [r for r in active_records(state) if r["resident_id"] == resident_id
            and r.get("record_mode") == "snapshot" and parse_date(r.get("date"))
            and low <= parse_date(r["date"]) <= high and not record_in_window(r, start, end)]


def _canonical(record: dict, *, identity_only: bool = False) -> dict:
    if identity_only and record.get("external_record_id"):
        return {k: record.get(k) for k in ("resident_id", "category", "specialty", "external_record_id")}
    keys = ["resident_id", "category", "specialty", "date", "record_mode", "form", "metric", "evaluator", "rotation"]
    if record.get("record_mode") == "snapshot":
        keys.extend(["period_start", "period_end"])
    if not identity_only:
        keys.extend(["value", "unit", "text", "questions", "values"])
    # Native export event IDs protect two otherwise identical real encounters.
    if record.get("external_record_id"):
        keys.append("external_record_id")
    return {k: record.get(k) for k in keys}


def record_fingerprint(record: dict, *, identity_only: bool = False) -> str:
    return hashlib.sha256(json.dumps(_canonical(record, identity_only=identity_only), sort_keys=True,
                                     ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def mapping_key(record: dict) -> str:
    return json.dumps([str(record.get("resident_name", "")).strip(), str(record.get("external_id", "")).strip()], ensure_ascii=False)


def prepare_records(state: dict, records: list[dict], mappings: dict[str, str]) -> tuple[list[dict], dict]:
    prepared = []
    resident_ids = {r["id"] for r in state["residents"]}
    saved = active_records(state)
    fingerprints = Counter(record_fingerprint(r) for r in saved)
    identities = {record_fingerprint(r, identity_only=True) for r in saved}
    native_seen = {}
    exact_duplicates = possible_updates = 0
    for item in records:
        rec = copy.deepcopy(item)
        target = mappings.get(mapping_key(rec)) or match_resident(state, rec.get("resident_name", ""), rec.get("external_id", ""))
        if target == "__skip__":
            continue
        if target not in resident_ids:
            raise ValidationError("Match every resident name or choose to skip it before saving.")
        rec["resident_id"] = target
        rec["date"] = iso_date(rec.get("date"), "Evidence date")
        if rec.get("category") not in CATEGORIES or rec.get("specialty") not in SPECIALTIES:
            raise ValidationError("Choose a supported upload category and specialty.")
        rec.setdefault("record_mode", "event")
        for key in ("period_start", "period_end"):
            if rec.get(key):
                rec[key] = iso_date(rec[key], "Evidence reporting period")
        if rec.get("period_start") and rec.get("period_end") and rec["period_start"] > rec["period_end"]:
            raise ValidationError("An evidence reporting period starts after it ends.")
        fp = record_fingerprint(rec)
        native = record_fingerprint(rec, identity_only=True) if rec.get("external_record_id") else None
        if native and native in native_seen:
            if native_seen[native] == fp:
                exact_duplicates += 1
                continue
            raise ValidationError("An upload contains conflicting entries for the same New Innovations event ID. Correct the file before importing.")
        if native:
            native_seen[native] = fp
        if fingerprints[fp] > 0:
            exact_duplicates += 1
            fingerprints[fp] -= 1
            continue
        identity = record_fingerprint(rec, identity_only=True)
        if identity in identities:
            possible_updates += 1
            rec["possible_revision"] = True
        # Do not collapse identical events inside a new file: two procedures or
        # shifts may be genuinely separate. The importer may use native IDs.
        prepared.append(rec)
    return prepared, {"duplicates": exact_duplicates, "possible_updates": possible_updates,
                      "new_records": len(prepared)}


def import_records(state: dict, records: list[dict], source: dict, mappings: dict[str, str], actor: str,
                   *, replace_source_id: str | None = None, allow_possible_updates: bool = False) -> tuple[dict, dict]:
    result = copy.deepcopy(state)
    # Hash+context identifies the same uploaded report; different reporting dates
    # remain visible as an explicit potential re-import instead of silent reuse.
    for old in result["sources"]:
        if old.get("active", True) and all(old.get(k) == source.get(k) for k in ("sha256", "kind", "specialty", "period_start", "period_end")):
            raise ValidationError("This exact file and reporting period are already saved.")
    if replace_source_id:
        previous = next((s for s in result["sources"] if s["id"] == replace_source_id and s.get("active", True)), None)
        if previous is None:
            raise ValidationError("The upload selected for replacement is no longer active.")
        if previous.get("kind") != source.get("kind") or previous.get("specialty") != source.get("specialty"):
            raise ValidationError("A replacement must have the same category and specialty.")
        previous["active"] = False
        previous["replaced_at"] = now_iso()
    prepared, stats = prepare_records(result, records, mappings)
    if not prepared:
        raise ValidationError("There are no new records to save after matching and duplicate checks.")
    if stats["possible_updates"] and not allow_possible_updates:
        raise ValidationError("Some entries may revise saved evidence. Review the overlaps and confirm before saving.")
    source = copy.deepcopy(source)
    source.setdefault("id", new_id())
    source.update({"active": True, "uploaded_at": now_iso(), "uploaded_by": actor, "record_count": len(prepared)})
    source["replaces"] = replace_source_id
    for rec in prepared:
        rec["id"] = new_id()
        rec["source_id"] = source["id"]
        rec["source_name"] = source.get("name", "")
        rec["imported_at"] = source["uploaded_at"]
        rec["active"] = True
        result["records"].append(rec)
    result["sources"].append(source)
    return with_audit(result, actor, "Upload saved", source_id=source["id"], records=len(prepared),
                      replaces=replace_source_id, skipped_duplicates=stats["duplicates"]), stats


def deactivate_source(state: dict, source_id: str, actor: str, reason: str) -> dict:
    if not reason.strip():
        raise ValidationError("Enter why this upload should be excluded.")
    result = copy.deepcopy(state)
    source = next((s for s in result["sources"] if s["id"] == source_id), None)
    if not source:
        raise ValidationError("Choose an existing upload.")
    source.update({"active": False, "excluded_reason": reason.strip(), "excluded_at": now_iso()})
    return with_audit(result, actor, "Upload excluded", source_id=source_id, reason=reason.strip())


def save_review(state: dict, resident_id: str, data: dict, actor: str, *, finalize: bool = False) -> dict:
    resident_by_id(state, resident_id)
    result = copy.deepcopy(state)
    item = copy.deepcopy(data)
    old = next((r for r in result["reviews"] if r["id"] == item.get("id")), None)
    if old and old["status"] == "Final":
        raise ValidationError("This review is finalized. Reopen it with a reason before editing.")
    item["resident_id"] = resident_id
    if old and old["resident_id"] != resident_id:
        raise ValidationError("A review cannot be moved to another resident.")
    item["pgy"] = int(item["pgy"])
    if item["pgy"] not in range(1, 5) or item.get("half") not in HALVES:
        raise ValidationError("Choose a training year and review checkpoint.")
    for key in ("period_start", "period_end", "review_date"):
        item[key] = iso_date(item.get(key), key.replace("_", " ").capitalize())
    if item["period_start"] > item["period_end"]:
        raise ValidationError("The review period must end on or after its start date.")
    if item["review_date"] < item["period_end"]:
        raise ValidationError("The evidence cutoff cannot be after the meeting date.")
    item.setdefault("academic_year", academic_year(item["period_start"]))
    if not re.fullmatch(r"\d{4}-\d{4}", item["academic_year"]):
        raise ValidationError("Enter the academic year as 2026-2027.")
    duplicate = next((r for r in result["reviews"] if r["resident_id"] == resident_id and r["pgy"] == item["pgy"]
                      and r["half"] == item["half"] and r["academic_year"] == item["academic_year"]
                      and r["id"] != item.get("id")), None)
    if duplicate:
        raise ValidationError("This resident already has that checkpoint in that academic year. Open the existing review.")
    item["fields"] = {str(k): str(v).strip() for k, v in item.get("fields", {}).items() if v is not None}
    for key in ("adult_clinic_count", "pediatric_clinic_count", "observed_encounters_completed"):
        if item["fields"].get(key) and (not item["fields"][key].isdigit()):
            raise ValidationError("Clinic and encounter counts must be whole numbers, or blank if unavailable.")
    for key in ("medicine_ite_percentile", "pediatrics_ite_percentile"):
        if item["fields"].get(key):
            try:
                n = float(item["fields"][key])
                if not math.isfinite(n) or not 0 <= n <= 100:
                    raise ValueError()
            except ValueError:
                raise ValidationError("ITE percentiles must be between 0 and 100, or blank.") from None
    if finalize and not item["fields"].get("ccc_decision"):
        raise ValidationError("Enter the CCC assessment or decision before finalizing.")
    item.setdefault("id", new_id())
    item.setdefault("created_by", actor)
    item.setdefault("created_at", now_iso())
    item["updated_at"] = now_iso()
    item["updated_by"] = actor
    item["status"] = "Final" if finalize else "Draft"
    if finalize:
        item["snapshot"] = copy.deepcopy(records_for(result, resident_id, item["period_start"], item["period_end"]))
        item["snapshot_resident"] = copy.deepcopy(resident_by_id(result, resident_id))
        item["excluded_coverage_count"] = len(coverage_exclusions(result, resident_id, item["period_start"], item["period_end"]))
        item["finalized_at"] = now_iso()
        item["finalized_by"] = actor
        history = [r for r in result["reviews"] if r["resident_id"] == resident_id and r["id"] != item["id"] and r["review_date"] <= item["review_date"]]
        item["history_snapshot"] = [{k: copy.deepcopy(r.get(k)) for k in ("id", "resident_id", "pgy", "half", "academic_year", "review_date", "status", "fields")} for r in [*history, item]]
    if old:
        result["reviews"][result["reviews"].index(old)] = item
    else:
        result["reviews"].append(item)
    return with_audit(result, actor, "Review finalized" if finalize else "Review draft saved",
                      resident_id=resident_id, review_id=item["id"])


def reopen_review(state: dict, review_id: str, actor: str, reason: str) -> dict:
    if not reason.strip():
        raise ValidationError("Enter the reason for reopening this finalized review.")
    result = copy.deepcopy(state)
    review = next((r for r in result["reviews"] if r["id"] == review_id), None)
    if not review or review["status"] != "Final":
        raise ValidationError("Choose a finalized review.")
    archived = {k: copy.deepcopy(v) for k, v in review.items() if k != "versions"}
    review.setdefault("versions", []).append(archived)
    review.update({"status": "Draft", "reopened_at": now_iso(), "reopened_by": actor,
                   "reopen_reason": reason.strip(), "updated_at": now_iso()})
    # Preserved final snapshot lives in versions; draft displays current evidence.
    review.pop("snapshot", None)
    return with_audit(result, actor, "Review reopened", review_id=review_id, reason=reason.strip())


def resident_label(resident: dict) -> str:
    external = str(resident.get("external_id", "")).strip()
    return f"{resident['name']} · {external or resident['id'][:8]}"


def latest_snapshots(records: list[dict]) -> list[dict]:
    """Keep the most recent record per resident/specialty/form/metric/period.

    Different reporting intervals remain separate; percentages are not summed.
    Tied, conflicting values are retained so a reviewer can resolve them.
    """
    groups: dict[tuple, list[dict]] = {}
    for rec in records:
        key = (rec.get("resident_id"), rec.get("specialty"), rec.get("category"), rec.get("form"), rec.get("metric"),
               rec.get("period_start"), rec.get("period_end"))
        groups.setdefault(key, []).append(rec)
    out = []
    for items in groups.values():
        latest = max(r.get("date", "") for r in items)
        out.extend(r for r in items if r.get("date", "") == latest)
    return out


def summary_counts(records: list[dict]) -> dict:
    result = {key: len([r for r in records if r["category"] == key]) for key in CATEGORIES}
    result["medicine_evaluations"] = sum(r["category"] == "evaluations" and r["specialty"] == "Medicine" for r in records)
    result["pediatrics_evaluations"] = sum(r["category"] == "evaluations" and r["specialty"] == "Pediatrics" for r in records)
    result["latest_evidence"] = max((r.get("date", "") for r in records), default="")
    return result
