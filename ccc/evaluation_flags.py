"""Review low evaluation ratings and retain explicitly selected comments.

Choices are references into saved, roster-owned evidence. This module never
changes that evidence or stores upload bytes. Its mutations return candidates
for the app's existing encrypted compare-and-swap save.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re
from typing import Any

from ccc import model as m


DEFAULT_CONFIG = {
    "competency_enabled": True,
    "competency_threshold": 2.0,
    "epa_enabled": True,
    "epa_threshold": 2.0,
}
_PEER_FORMS = {"res eval res", "resident evaluation of resident"}
_EPA = re.compile(r"\bepas?\b|\bentrust(?:able|ment)\b", re.I)
_COMPETENCY = re.compile(
    r"\bcompeten(?:cy|cies|ce)\b|\bmilestones?\b|"
    r"\b(?:PC|MK|SBP|PBLI|PROF|PRO|ICS)\s*-?\s*\d+\b|"
    r"\b(?:patient care|medical knowledge|professionalism|communication|"
    r"clinical reasoning|clinical judgment|clinical judgement|teamwork|"
    r"history taking|physical examination|diagnostic reasoning|patient management)\b|"
    r"\b(?:practice[ -]+based learning|systems?[ -]+based practice)\b", re.I,
)
_COUNTS = re.compile(
    r"\b(?:how many|number of|count of|total (?:number|hours|days|patients|evaluations)|"
    r"hours worked|days worked|patient count|evaluation count)\b|"
    r"\b(?:yes\s*(?:/|\bor\b)\s*no|true\s*(?:/|\bor\b)\s*false)\b", re.I,
)
_MISSING = re.compile(
    r"^(?:n\s*/?\s*a|none|not applicable|not observed|not assessed|not evaluated|"
    r"unable to assess|unable to evaluate|cannot assess|no opportunity|did not observe|"
    r"not enough (?:contact|experience)|x|[-–—])\.?$", re.I,
)
_UNASSESSED_LABEL = re.compile(
    r"\b(?:not applicable|not observed|not assessed|not evaluated|unable to assess|"
    r"unable to evaluate|cannot assess|no opportunity|did not observe|n\s*/\s*a)\b", re.I,
)
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_KEY = re.compile(r"[0-9a-f]{64}")


def _plain(value: Any) -> str:
    from ccc.ccc_summary import _feedback_plain
    return _feedback_plain(value)


def _config(data: Any) -> dict:
    if not isinstance(data, dict):
        raise m.ValidationError("Evaluation flag settings must be a valid set of options.")
    result = dict(DEFAULT_CONFIG)
    result.update({key: value for key, value in data.items() if key in DEFAULT_CONFIG})
    for key in ("competency_enabled", "epa_enabled"):
        if type(result[key]) is not bool:
            raise m.ValidationError("Choose whether each evaluation rating flag is enabled.")
    for key in ("competency_threshold", "epa_threshold"):
        value = result[key]
        if isinstance(value, bool):
            raise m.ValidationError("Evaluation score cutoffs must be numbers from 0 to 100.")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise m.ValidationError("Evaluation score cutoffs must be numbers from 0 to 100.") from None
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise m.ValidationError("Evaluation score cutoffs must be numbers from 0 to 100.")
        result[key] = value
    return result


def get_config(state: dict) -> dict:
    return _config(state.get("settings", {}).get("evaluation_flagger", {}))


def save_config(state: dict, data: dict, actor: str) -> dict:
    config = _config(data)
    result = copy.deepcopy(state)
    result.setdefault("settings", {})["evaluation_flagger"] = config
    result["settings"]["evaluation_flagger_updated_at"] = m.now_iso()
    return m.with_audit(result, actor, "Evaluation flag settings saved", **config)


def numeric_rating(value: Any) -> float | None:
    """Read a source rating without extracting numbers from narrative prose."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = _plain(value)
        if not text or _MISSING.fullmatch(text) or _UNASSESSED_LABEL.search(text):
            return None
        direct = re.fullmatch(_NUM, text)
        fraction = re.fullmatch(rf"({_NUM})\s*/\s*({_NUM})", text)
        labelled = re.fullmatch(
            rf"(?:(?:level|rating|score)\s*[:=]?\s*)?({_NUM})\s*(?:(?:[-–—:=)]\s*|\.\s+)\S.*)?",
            text, re.I | re.S,
        )
        if direct:
            number = float(text)
        elif fraction:
            denominator = float(fraction.group(2))
            number = float(fraction.group(1))
            if not math.isfinite(denominator) or denominator <= 0 or number > denominator:
                return None
        elif labelled:
            number = float(labelled.group(1))
        else:
            return None
    else:
        return None
    return number if math.isfinite(number) and 0 <= number <= 100 else None


def _rating_kind(question: dict, record: dict) -> str | None:
    prompt = _plain(question.get("text"))
    response_type = _plain(question.get("response_type") or question.get("question_type") or question.get("type")).casefold()
    if (any(word in response_type for word in ("boolean", "yes/no", "count", "date", "text", "narrative", "essay"))
            or _COUNTS.search(prompt)
            or (re.search(r"^(?:please\s+)?(?:comments?|feedback|describe|explain|elaborate|strengths?|goals?)\b", prompt, re.I)
                and not re.search(r"\b(?:rate|rating|score|level|scale)\b", prompt, re.I))):
        return None
    metadata = " ".join(_plain(question.get(key)) for key in ("category", "domain", "competency", "epa", "kind"))
    if _EPA.search(" ".join((metadata, prompt))):
        return "EPA"
    if _COMPETENCY.search(" ".join((metadata, prompt))):
        return "Competency"
    form = _plain(record.get("form"))
    if _EPA.search(form):
        return "EPA"
    if _COMPETENCY.search(form):
        return "Competency"
    return "Other rating"


def evaluation_metadata(record: dict) -> dict:
    """Use the report's specialty routing and peer-evaluator privacy rule."""
    from ccc.ccc_summary import evaluation_specialty
    form = _plain(record.get("form"))
    evaluator = _plain(record.get("evaluator"))
    status = _plain(record.get("evaluator_status"))
    if " ".join(form.split()).casefold() in _PEER_FORMS:
        evaluator = ""
    elif status and not evaluator.casefold().endswith(f"[{status}]".casefold()):
        evaluator = f"{evaluator} [{status}]".strip()
    return {
        "record_id": str(record.get("id", "")), "date": str(record.get("date", "")),
        "specialty": evaluation_specialty(record), "evaluator": evaluator,
        "rotation": _plain(record.get("rotation")), "form": form,
    }


def _comment(value: Any) -> str:
    from ccc.ccc_summary import _without_comment_labels
    text = _without_comment_labels(value)
    if not text or _MISSING.fullmatch(text):
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if all(_MISSING.fullmatch(line)
           or re.fullmatch(rf"(?:{_NUM})(?:\s*/\s*{_NUM}|%)?", line)
           or re.fullmatch(rf"(?:level|rating|score)\s*[:=]?\s*{_NUM}", line, re.I)
           for line in lines):
        return ""
    return text


def comment_candidates(record: dict) -> list[dict]:
    """Return independently selectable authored comments, never question stems."""
    from ccc.ccc_summary import _is_convenience_text, _legacy_feedback, _written_response
    if record.get("category") != "evaluations":
        return []
    questions = record.get("questions") if isinstance(record.get("questions"), list) else []
    feedback, comments = [], []
    for question in questions:
        if not isinstance(question, dict):
            continue
        written = _comment(_written_response(question))
        comment = _comment(question.get("comment"))
        if written:
            feedback.append(written)
        if comment:
            feedback.append(comment)
            comments.append(comment)
    values = record.get("values") if isinstance(record.get("values"), dict) else {}
    overall = list(dict.fromkeys(value for value in (
        _comment(record.get("overall_comment")), _comment(values.get("overall_comments")),
    ) if value))
    independent = _comment(_legacy_feedback(record.get("text")))
    parts = []
    if (independent and not _is_convenience_text(independent, feedback, overall)
            and not _is_convenience_text(independent, comments, overall)):
        # A legacy aggregate has no reliable individual field boundaries.
        parts.append(independent)
    parts.extend(feedback)
    parts.extend(overall)
    return [{"key": hashlib.sha256(text.encode("utf-8")).hexdigest(), "text": text}
            for text in dict.fromkeys(parts) if text]


def _range(start: Any, end: Any) -> tuple[str, str]:
    start = m.iso_date(start, "Evaluation start date")
    end = m.iso_date(end, "Evaluation end date")
    if start > end:
        raise m.ValidationError("The evaluation date range starts after it ends.")
    return start, end


def _selected_keys(state: dict, resident_id: str) -> set[tuple[str, str]]:
    return {(item["record_id"], item["comment_key"]) for item in state.get("evaluation_comment_selections", [])
            if item.get("resident_id") == resident_id}


def _rating_questions(record: dict) -> list[dict]:
    questions = record.get("questions") if isinstance(record.get("questions"), list) else []
    result = [question for question in questions if isinstance(question, dict)]
    # Standardized long exports can carry one real rating outside questions.
    # An NI assessment count, including its normal value=1, is never a score.
    unit = _plain(record.get("unit")).casefold()
    metric = _plain(record.get("metric"))
    count_units = {"assessment", "assessments", "count", "counts", "evaluation", "evaluations",
                   "day", "days", "hour", "hours", "patient", "patients"}
    explicit_rating_unit = re.fullmatch(r"(?:(?:competency|milestone) )?(?:scores?|ratings?|levels?|points?)", unit)
    if unit not in count_units and (explicit_rating_unit or (not unit and (_COMPETENCY.search(metric) or _EPA.search(metric)))):
        result.append({"text": metric, "response": record.get("value"), "response_type": "rating"})
    return result


def review_evaluations(state: dict, resident_id: str, start: Any, end: Any) -> list[dict]:
    m.resident_by_id(state, resident_id)
    start, end = _range(start, end)
    config = get_config(state)
    selected = _selected_keys(state, resident_id)
    result = []
    for record in m.records_for(state, resident_id, start, end):
        if record.get("category") != "evaluations":
            continue
        flags, unclassified = [], []
        for question in _rating_questions(record):
            score = numeric_rating(question.get("response"))
            kind = _rating_kind(question, record) if score is not None else None
            if kind is None:
                continue
            group = "epa" if kind == "EPA" else "competency"
            flag = {"kind": kind, "prompt": _plain(question.get("text")),
                    "score": score, "threshold": config[f"{group}_threshold"]}
            if kind == "Other rating":
                unclassified.append(flag)
            if config[f"{group}_enabled"] and score <= config[f"{group}_threshold"]:
                flags.append(flag)
        metadata = evaluation_metadata(record)
        candidates = [{**comment, "selected": (record["id"], comment["key"]) in selected}
                      for comment in comment_candidates(record)]
        result.append({**metadata, "flagged": bool(flags), "flags": flags,
                       "comments": candidates, "unclassified_ratings": unclassified})
    return result


def save_comment_choices(state: dict, resident_id: str, choices: dict[str, list[str]], actor: str) -> dict:
    """Replace choices for supplied evaluations, keeping all hidden choices."""
    m.resident_by_id(state, resident_id)
    if not isinstance(choices, dict):
        raise m.ValidationError("Choose the evaluation comments to include.")
    eligible = {record["id"]: record for record in m.active_records(state)
                if record.get("resident_id") == resident_id and record.get("category") == "evaluations"}
    selected = []
    for record_id, keys in choices.items():
        if record_id not in eligible:
            raise m.ValidationError("A selected evaluation is unavailable or belongs to another resident. Reload before saving.")
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)):
            raise m.ValidationError("Choose each comment only once.")
        allowed = {comment["key"] for comment in comment_candidates(eligible[record_id])}
        if set(keys) - allowed:
            raise m.ValidationError("A selected comment has changed or is unavailable. Reload before saving.")
        selected.extend({"resident_id": resident_id, "record_id": record_id, "comment_key": key,
                         "selected_at": m.now_iso(), "selected_by": str(actor)} for key in keys)
    result = copy.deepcopy(state)
    result["evaluation_comment_selections"] = [
        item for item in result.get("evaluation_comment_selections", [])
        if not (item.get("resident_id") == resident_id and item.get("record_id") in choices)
    ] + selected
    result["evaluation_comment_selections_updated_at"] = m.now_iso()
    return m.with_audit(result, actor, "Evaluation comment selections saved", resident_id=resident_id,
                        evaluations_reviewed=len(choices), selected_comments=len(selected))


def _snapshot_rows(review: dict, resident_id: str, start: str, end: str) -> list[dict]:
    if review.get("resident_id") != resident_id:
        raise m.ValidationError("The selected review belongs to another resident.")
    _validate_snapshot(review, resident_id)
    return [copy.deepcopy(row) for row in review.get("evaluation_comment_snapshot", [])
            if start <= row["date"] <= end]


def selected_for_report(state: dict, resident_id: str, start: Any, end: Any, review: dict | None = None) -> list[dict]:
    m.resident_by_id(state, resident_id)
    start, end = _range(start, end)
    if review and review.get("resident_id") != resident_id:
        raise m.ValidationError("The selected review belongs to another resident.")
    if review and review.get("status") == "Final":
        return _snapshot_rows(review, resident_id, start, end)
    selected = _selected_keys(state, resident_id)
    rows = []
    for record in m.records_for(state, resident_id, start, end):
        if record.get("category") != "evaluations":
            continue
        metadata = evaluation_metadata(record)
        for comment in comment_candidates(record):
            if (record["id"], comment["key"]) in selected:
                rows.append({"resident_id": resident_id, "record_id": record["id"],
                             "comment_key": comment["key"], "date": record["date"],
                             "specialty": metadata["specialty"], "evaluator": metadata["evaluator"],
                             "comment": comment["text"]})
    return rows


def _validate_snapshot(review: dict, resident_id: str, depth: int = 0) -> None:
    if depth > 30:
        raise m.ValidationError("The preserved comment history is too deeply nested.")
    if "evaluation_comment_snapshot" in review:
        rows = review["evaluation_comment_snapshot"]
        first, last = review.get("period_start"), review.get("period_end")
        if not isinstance(rows, list) or not m.parse_date(first) or not m.parse_date(last):
            raise m.ValidationError("A saved review has invalid selected evaluation comments.")
        seen = set()
        for row in rows:
            if (not isinstance(row, dict) or row.get("resident_id") != resident_id
                    or not isinstance(row.get("record_id"), str) or not row["record_id"]
                    or not isinstance(row.get("comment_key"), str) or not _KEY.fullmatch(row["comment_key"])
                    or not m.parse_date(row.get("date")) or not first <= row["date"] <= last
                    or row.get("specialty") not in {"Medicine", "Pediatrics"}
                    or not isinstance(row.get("evaluator"), str)
                    or not isinstance(row.get("comment"), str) or not row["comment"].strip()
                    or hashlib.sha256(row["comment"].encode("utf-8")).hexdigest() != row["comment_key"]):
                raise m.ValidationError("A saved review has invalid or another resident's selected comments.")
            key = (row["record_id"], row["comment_key"])
            if key in seen:
                raise m.ValidationError("A saved review repeats a selected evaluation comment.")
            seen.add(key)
    for prior in review.get("versions", []):
        if isinstance(prior, dict):
            if prior.get("resident_id") != resident_id:
                raise m.ValidationError("A prior review belongs to another resident.")
            _validate_snapshot(prior, resident_id, depth + 1)


def validate_state_flags(state: dict) -> None:
    get_config(state)
    selections = state.get("evaluation_comment_selections", [])
    if not isinstance(selections, list):
        raise m.ValidationError("Saved evaluation comment choices must be a list.")
    residents = {resident["id"] for resident in state["residents"]}
    records = {record["id"]: record for record in state["records"]}
    seen = set()
    for item in selections:
        if not isinstance(item, dict):
            raise m.ValidationError("A saved evaluation comment choice is invalid.")
        resident_id, record_id, key = item.get("resident_id"), item.get("record_id"), item.get("comment_key")
        if (not isinstance(resident_id, str) or resident_id not in residents
                or not isinstance(record_id, str) or record_id not in records
                or records[record_id].get("resident_id") != resident_id
                or records[record_id].get("category") != "evaluations"
                or not isinstance(key, str) or not _KEY.fullmatch(key)
                or not isinstance(item.get("selected_at"), str)
                or not m.source_export_date(item["selected_at"])
                or not isinstance(item.get("selected_by"), str)):
            raise m.ValidationError("A saved evaluation comment choice has no matching resident or evaluation.")
        identity = (resident_id, record_id, key)
        if identity in seen:
            raise m.ValidationError("A saved evaluation comment choice appears more than once.")
        seen.add(identity)
    for review in state["reviews"]:
        _validate_snapshot(review, review["resident_id"])
