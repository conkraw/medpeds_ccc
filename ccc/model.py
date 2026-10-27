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
from collections import defaultdict
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
    "readiness_clinical_reasoning": "Readiness: clinical reasoning",
    "readiness_team_leadership": "Readiness: team leadership",
    "readiness_learner_supervision": "Readiness: learner supervision",
    "readiness_communication": "Readiness: communication",
    "readiness_professionalism": "Readiness: professionalism",
    "readiness_systems_based_practice": "Readiness: systems-based practice",
    "duty_min_days_off": "Fewest days off recorded",
    "duty_days_off_window": "Days-off reporting window",
    "duty_days_off_rotations": "Rotations associated with days-off concerns",
    "duty_max_hours_4weeks": "Maximum hours in a four-week period",
    "duty_80_hour_window": "Hours reporting window",
    "duty_80_hour_rotations": "Rotations associated with hours concerns",
    "duty_resolution_notes": "Duty-hour review and resolution notes",
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


def _validate_cumulative_snapshots(review: dict, resident_id: str, depth: int = 0) -> None:
    if depth > 30:
        raise ValidationError("The preserved review history is too deeply nested.")
    if "cumulative_evidence_snapshot" in review:
        cumulative = review["cumulative_evidence_snapshot"]
        cutoff = review.get("period_end")
        if (review.get("resident_id") != resident_id or not parse_date(cutoff)
                or not isinstance(cumulative, list) or any(
                    not isinstance(x, dict) or x.get("resident_id") != resident_id
                    or x.get("category") not in CATEGORIES or x.get("specialty") not in SPECIALTIES
                    or not parse_date(x.get("date"))
                    or not record_in_window(x, "0001-01-01", cutoff) for x in cumulative)):
            raise ValidationError("A review has invalid, future, or another resident's cumulative evidence.")
    versions = review.get("versions", [])
    if isinstance(versions, list):
        for prior in versions:
            if isinstance(prior, dict):
                _validate_cumulative_snapshots(prior, resident_id, depth + 1)


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
        _validate_cumulative_snapshots(r, r["resident_id"])
    from ccc.annual import validate_state_progress
    validate_state_progress(state)
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


def remove_resident(state: dict, resident_id: str, actor: str) -> dict:
    """Remove one resident and their evidence and reviews from the current state.

    Review snapshots and prior versions belong to their containing review and
    are removed with it. Sources remain intact because one upload can contain
    several residents; their record counts describe the original import.
    Existing audit events, encrypted originals, and older backups are retained.
    The caller must confirm this action and persist the returned candidate
    before replacing its saved state.
    """
    resident_by_id(state, resident_id)
    result = copy.deepcopy(state)
    result["residents"] = [r for r in result["residents"] if r["id"] != resident_id]
    result["records"] = [r for r in result["records"] if r["resident_id"] != resident_id]
    result["reviews"] = [r for r in result["reviews"] if r["resident_id"] != resident_id]
    annual_removed = 0
    if "annual_progress" in result:
        result["annual_progress"] = [r for r in result["annual_progress"] if r["resident_id"] != resident_id]
        annual_removed = len(state["annual_progress"]) - len(result["annual_progress"])
    result = with_audit(result, actor, "Resident removed", resident_id=resident_id,
                        records_removed=len(state["records"]) - len(result["records"]),
                        reviews_removed=len(state["reviews"]) - len(result["reviews"]),
                        **({"annual_records_removed": annual_removed} if annual_removed else {}))
    return validate_state(result)


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
    milestone_period = milestone_snapshot_period(record)
    observed = (parse_date(milestone_period[1]) if milestone_period else
                parse_date(record.get("date")) or parse_date(record.get("period_end")))
    if not low or not high or not observed or low > high or not low <= observed <= high:
        return False
    if record.get("record_mode") == "snapshot":
        first, last = (tuple(parse_date(value) for value in milestone_period) if milestone_period else
                       (parse_date(record.get("period_start")), parse_date(record.get("period_end"))))
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


def source_export_date(value: Any) -> str:
    """Read an existing export date without substituting an upload date."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(value) and 59 <= value <= 100_000:
            return (datetime(1899, 12, 30) + timedelta(days=value)).date().isoformat()
        return ""
    text = str(value or "").strip()
    if not text or len(text) > 80:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def evaluation_source_period(record: dict) -> tuple[str, str]:
    """Return source-supplied assessment dates, never selected upload fallbacks.

    Older encrypted records retain their original date columns in raw. Using
    those columns also makes raw TAB and converted CSV exports equivalent.
    """
    raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
    normalized = {re.sub(r"[^a-z0-9]", "", str(key).casefold()): value for key, value in raw.items()}

    def first(*keys: str) -> Any:
        return next((normalized[key] for key in keys if str(normalized.get(key) or "").strip()), "")

    report_range = str(first("reportdates", "reportperiod"))
    dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}", report_range)
    ranged = [source_export_date(value) for value in dates[:2]] if len(dates) >= 2 else ["", ""]
    result = []
    for index, part in enumerate(("start", "end")):
        value = source_export_date(record.get(f"source_period_{part}"))
        if not value:
            value = source_export_date(first(f"period{part}", f"evaluation{part}date", f"{part}date")) or ranged[index]
        if not value and (record.get(f"period_{part}_is_fallback") is False
                          or record.get("date_source") == f"source period {part}"):
            value = source_export_date(record.get(f"period_{part}"))
        result.append(value)
    return result[0], result[1]


def _milestone_grid_source_period(record: dict) -> tuple[str, str] | None:
    """Identify only the explicit cells emitted by the NI grid importer."""
    if (record.get("category") != "milestones"
            or record.get("specialty") not in ("Medicine", "Pediatrics")
            or record.get("record_mode") != "snapshot"
            or record.get("form") != "Milestone assessment"
            or record.get("date_source") != "source period end"
            or not re.fullmatch(r"PDF page \d+, table \d+, label row \d+, score row \d+, column \d+",
                                str(record.get("source_locator", "")))):
        return None
    first, last = evaluation_source_period(record)
    if (not first or not last or first > last
            or source_export_date(record.get("period_start")) != first
            or source_export_date(record.get("period_end")) != last
            or source_export_date(record.get("date")) != last):
        return None
    return first, last


def milestone_snapshot_period(record: dict) -> tuple[str, str] | None:
    """Return the covered interval for a known NI milestone grid snapshot.

    New Innovations can print a full training-year date range while the scores
    only reflect the day the report was produced. Keep that original range in
    saved evidence and fingerprints, but use its printed report date to decide
    when the snapshot was available. Older grid imports have no printed report
    date; an import timestamp inside that same source period proves only that
    the scores were available by that later day. Other report formats and
    malformed metadata retain the ordinary snapshot rules.
    """
    source_period = _milestone_grid_source_period(record)
    if source_period is None:
        return None
    first, last = source_period
    if "source_report_date" in record:
        observed = source_export_date(record["source_report_date"])
        if not observed or observed < first:
            return None
    else:
        observed = source_export_date(record.get("imported_at"))
        if not first <= observed <= last:
            return None
    return first, min(last, observed)


def _canonical_question(question: Any) -> Any:
    """Ignore conversion-only rating spelling without changing saved evidence."""
    if not isinstance(question, dict):
        return question
    result = dict(question)
    response = question.get("response")
    if isinstance(response, (str, int, float)) and not isinstance(response, bool):
        numeric = re.fullmatch(r"([+-]?)(\d+)(?:\.(\d*))?|([+-]?)\.(\d+)", str(response).strip())
        if numeric:
            sign = numeric.group(1) or numeric.group(4) or ""
            integer = (numeric.group(2) or "0").lstrip("0") or "0"
            fraction = (numeric.group(3) or numeric.group(5) or "").rstrip("0")
            sign = "-" if sign == "-" and (integer != "0" or fraction) else ""
            result["response"] = sign + integer + ("." + fraction if fraction else "")
    # The original notebook's pandas conversion turns literal n/a into blank.
    # Treat that exact missing-comment marker alike; keep all prose verbatim.
    if isinstance(question.get("comment"), str) and question["comment"].strip().casefold() == "n/a":
        result["comment"] = ""
    return result


def _derived_assessment_text(questions: list, overall_comment: Any) -> str | None:
    """Reconstruct the importer's aggregate only from known comment fields."""
    if not isinstance(overall_comment, str) or any(not isinstance(question, dict) for question in questions):
        return None
    comments = [question.get("comment") for question in questions] + [overall_comment]
    if any(not isinstance(comment, str) for comment in comments):
        return None
    return "\n\n".join(dict.fromkeys(comment.strip() for comment in comments if comment.strip()))


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
    result = {k: record.get(k) for k in keys}
    if record.get("category") == "milestones" and isinstance(result.get("values"), dict):
        # The canonical metric identifies the milestone. PDF header spacing
        # and notebook CSV labels are provenance, not another assessment.
        # Keep the original values dictionary unchanged in saved evidence.
        result["values"] = {key: value for key, value in result["values"].items() if key != "source_label"}
    if record.get("category") in {"evaluations", "self_assessments"}:
        # Two evaluations completed on one day may cover different rotations.
        # Preserve that distinction while ignoring a later upload's fallback
        # reporting range, which is not part of an evaluation's identity.
        result["source_period_start"], result["source_period_end"] = evaluation_source_period(record)
        if not identity_only and isinstance(result.get("questions"), list):
            questions = result["questions"]
            result["questions"] = [_canonical_question(question) for question in questions]
            if any(isinstance(question, dict) and question.get("comment") != normalized.get("comment")
                   for question, normalized in zip(questions, result["questions"])):
                original_text = _derived_assessment_text(questions, record.get("overall_comment"))
                if original_text is not None and result.get("text") == original_text:
                    # An exact match proves the aggregate is importer-derived.
                    # Never remove a marker embedded in a longer comment or
                    # rewrite independently authored aggregate narrative.
                    result["text"] = _derived_assessment_text(result["questions"], record.get("overall_comment"))
    return result


def record_fingerprint(record: dict, *, identity_only: bool = False) -> str:
    return hashlib.sha256(json.dumps(_canonical(record, identity_only=identity_only), sort_keys=True,
                                     ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def mapping_key(record: dict) -> str:
    return json.dumps([str(record.get("resident_name", "")).strip(), str(record.get("external_id", "")).strip()], ensure_ascii=False)


def _milestone_comparison_bucket(record: dict) -> str:
    """Distinguish actual MY scores inside an otherwise EOY annual range.

    The original fingerprint remains stable for saved archives and equivalent
    historical CSV/PDF exports. Only a known annual grid's MY snapshot needs
    another comparison bucket, so the same score can be recorded again at EOY.
    """
    period = milestone_snapshot_period(record)
    if period is None:
        return ""
    first, last = _milestone_grid_source_period(record)
    if not (first[5:7] == "07" and last[5:7] == "06" and int(last[:4]) == int(first[:4]) + 1):
        return ""
    values = record.get("values") if isinstance(record.get("values"), dict) else {}
    half = str(record.get("half") or values.get("half") or "").strip().casefold()
    if half in {"year end", "end of year", "eoy", "end-of-year"}:
        return ""
    if half in {"midyear", "my", "mid-year"} or period[1] < f"{last[:4]}-01-01":
        return "annual-grid-midyear"
    return ""


def _milestone_file_fingerprint(record: dict, fingerprint: str) -> tuple[str, str] | None:
    digest = record.get("file_sha256")
    if (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
            and _milestone_grid_source_period(record) is not None):
        return digest, fingerprint
    return None


def prepare_records(state: dict, records: list[dict], mappings: dict[str, str]) -> tuple[list[dict], dict]:
    prepared = []
    resident_ids = {r["id"] for r in state["residents"]}
    saved = active_records(state)
    fingerprints: dict[tuple[str, str], list[int]] = defaultdict(list)
    same_files: dict[tuple[str, str], list[int]] = defaultdict(list)
    identities = set()
    for index, record in enumerate(saved):
        fingerprint = record_fingerprint(record)
        bucket = _milestone_comparison_bucket(record)
        fingerprints[(fingerprint, bucket)].append(index)
        identities.add((record_fingerprint(record, identity_only=True), bucket))
        file_key = _milestone_file_fingerprint(record, fingerprint)
        if file_key is not None:
            same_files[file_key].append(index)
    consumed = set()

    def consume_duplicate(candidates: list[int]) -> bool:
        # The two indexes share one pool of saved rows. A legacy same-file
        # match must not consume a second copy through its checkpoint bucket.
        while candidates and candidates[-1] in consumed:
            candidates.pop()
        if not candidates:
            return False
        consumed.add(candidates.pop())
        return True
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
        bucket = _milestone_comparison_bucket(rec)
        native = record_fingerprint(rec, identity_only=True) if rec.get("external_record_id") else None
        if native and native in native_seen:
            if native_seen[native] == fp:
                exact_duplicates += 1
                continue
            raise ValidationError("An upload contains conflicting entries for the same New Innovations event ID. Correct the file before importing.")
        if native:
            native_seen[native] = fp
        file_key = _milestone_file_fingerprint(rec, fp)
        if ((file_key is not None and consume_duplicate(same_files[file_key]))
                or consume_duplicate(fingerprints[(fp, bucket)])):
            exact_duplicates += 1
            continue
        identity = (record_fingerprint(rec, identity_only=True), bucket)
        if identity in identities:
            possible_updates += 1
            rec["possible_revision"] = True
        # Do not collapse identical events inside a new file: two procedures or
        # shifts may be genuinely separate. The importer may use native IDs.
        prepared.append(rec)
    return prepared, {"duplicates": exact_duplicates, "possible_updates": possible_updates,
                      "new_records": len(prepared)}


def import_records(state: dict, records: list[dict], source: dict, mappings: dict[str, str], actor: str,
                   *, replace_source_id: str | None = None, allow_possible_updates: bool = False,
                   allow_partial_reimport: bool = False) -> tuple[dict, dict]:
    """Save new evidence, with an explicit additive path for a partial import.

    Completing the same file and reporting period requires the partial-import
    option and a fresh source identifier. Existing duplicate and revision
    checks still apply to every row. Replacements retain exact-file rejection.
    """
    return _import_records_into_candidate(
        copy.deepcopy(state), records, source, mappings, actor,
        replace_source_id=replace_source_id, allow_possible_updates=allow_possible_updates,
        allow_partial_reimport=allow_partial_reimport,
    )


def _import_records_into_candidate(result: dict, records: list[dict], source: dict,
                                   mappings: dict[str, str], actor: str, *,
                                   replace_source_id: str | None = None,
                                   allow_possible_updates: bool = False,
                                   allow_partial_reimport: bool = False,
                                   prepared_result: tuple[list[dict], dict] | None = None) -> tuple[dict, dict]:
    """Append to a private candidate owned by the caller, never saved state.

    Bulk additive imports may reuse preparation against this exact candidate.
    The candidate is discarded if any file or subsequent persistence fails.
    Public imports copy first; replacements always run fresh preparation.
    """
    if prepared_result is not None and replace_source_id is not None:
        raise ValidationError("A replacement must be checked against the active evidence again.")
    # Hash+context identifies the same uploaded report; different reporting dates
    # remain visible as an explicit potential re-import instead of silent reuse.
    for old in result["sources"]:
        if old.get("active", True) and all(old.get(k) == source.get(k) for k in ("sha256", "kind", "specialty", "period_start", "period_end")):
            if not allow_partial_reimport or replace_source_id is not None:
                raise ValidationError("This exact file and reporting period are already saved.")
    if replace_source_id:
        previous = next((s for s in result["sources"] if s["id"] == replace_source_id and s.get("active", True)), None)
        if previous is None:
            raise ValidationError("The upload selected for replacement is no longer active.")
        if previous.get("kind") != source.get("kind") or previous.get("specialty") != source.get("specialty"):
            raise ValidationError("A replacement must have the same category and specialty.")
        previous["active"] = False
        previous["replaced_at"] = now_iso()
    prepared, stats = prepared_result if prepared_result is not None else prepare_records(result, records, mappings)
    if not prepared:
        raise ValidationError("There are no new records to save after matching and duplicate checks.")
    if stats["possible_updates"] and not allow_possible_updates:
        raise ValidationError("Some entries may revise saved evidence. Review the overlaps and confirm before saving.")
    if allow_partial_reimport and any(s["id"] == source.get("id") for s in result["sources"]):
        raise ValidationError("This upload identifier is already saved. Process the file again before adding its remaining entries.")
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
        from ccc.annual import progress_for
        item["snapshot"] = copy.deepcopy(records_for(result, resident_id, item["period_start"], item["period_end"]))
        item["snapshot_resident"] = copy.deepcopy(resident_by_id(result, resident_id))
        item["cumulative_evidence_snapshot"] = copy.deepcopy(records_for(
            result, resident_id, item["snapshot_resident"]["start_date"], item["period_end"]))
        item["annual_progress_snapshot"] = progress_for(result, resident_id, item["period_end"])
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
    review.pop("cumulative_evidence_snapshot", None)
    review.pop("annual_progress_snapshot", None)
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
