"""Adapt the encrypted dashboard data to the program's original Jupyter report.

All Word formatting is supplied by ``notebook_word``. This module only selects
resident-owned, dated data and fills the notebook's existing scalar row fields.
Final reviews use their frozen snapshots. No uploaded file or report is written
to disk, and a report download needs no separate passphrase.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
import html
import json
import math
import re
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup

from ccc import model as m
from ccc import notebook_word as word
from ccc.annual import FIELD_LABELS as ANNUAL_LABELS
from ccc.evaluation_summary import _is_convenience_text, evaluation_rows
from ccc.reports import _blank, _iso, _number, _text


@dataclass
class _Context:
    resident: dict
    period_start: str
    period_end: str
    evidence: list[dict]
    cumulative: list[dict]
    history: list[dict]
    tracking: list[dict]
    narrative_review: dict | None
    explicit_review: bool
    final: bool
    limited_final_history: bool

    @property
    def fields(self) -> dict:
        fields = (self.narrative_review or {}).get("fields", {})
        return fields if isinstance(fields, dict) else {}

    @property
    def pgy(self) -> int:
        value = (self.narrative_review or {}).get("pgy") if self.explicit_review else None
        try:
            return min(4, max(1, int(value)))
        except (TypeError, ValueError):
            return min(4, max(1, m.current_pgy(self.resident, date.fromisoformat(self.period_end))))

    @property
    def half(self) -> str:
        return (self.narrative_review or {}).get("half", "Year end")


def _validate_date(value: Any, label: str) -> str:
    result = _iso(value)
    if not result:
        raise m.ValidationError(f"{label} must be a calendar date.")
    return result


def _owned(records: list[dict], resident_id: str, start: str, end: str) -> list[dict]:
    return [deepcopy(r) for r in records if isinstance(r, dict)
            and r.get("resident_id") == resident_id and r.get("active", True)
            and m.record_in_window(r, start, end)]


def _context(resident: dict, records: list[dict], reviews: list[dict], start: str,
             end: str, review: dict | None, tracking: list[dict] | None) -> _Context:
    resident_id = _text(resident.get("id"))
    if not resident_id:
        raise m.ValidationError("Choose a resident before preparing a CCC summary.")
    if review and review.get("resident_id") != resident_id:
        raise m.ValidationError("The selected review belongs to another resident.")
    final = bool(review and review.get("status") == "Final")
    if final:
        if not isinstance(review.get("snapshot"), list):
            raise m.ValidationError("This finalized review has no preserved evidence snapshot.")
        if not isinstance(review.get("snapshot_resident"), dict):
            raise m.ValidationError("This finalized review is missing its preserved resident details. Reopen and finalize it after confirming the resident and review data.")
        for key in ("cumulative_evidence_snapshot", "annual_progress_snapshot", "history_snapshot"):
            if key in review and not isinstance(review[key], list):
                raise m.ValidationError("This finalized review contains invalid preserved data. Review the saved record before exporting it.")
        start, end = review.get("period_start"), review.get("period_end")
    start = _validate_date(start, "Reporting start")
    end = _validate_date(end, "Evidence cutoff")
    if start > end:
        raise m.ValidationError("The reporting start must be on or before the evidence cutoff.")

    profile = deepcopy(resident)
    if final and isinstance(review.get("snapshot_resident"), dict):
        profile = deepcopy(review["snapshot_resident"])
        if profile.get("id") != resident_id:
            raise m.ValidationError("The preserved resident details belong to another resident.")
    training_start = _validate_date(profile.get("start_date"), "Training start")
    limited = final and not isinstance(review.get("cumulative_evidence_snapshot"), list)
    if final:
        evidence = _owned(review["snapshot"], resident_id, start, end)
        historical_records = review.get("cumulative_evidence_snapshot", review["snapshot"])
        cumulative = _owned(historical_records, resident_id, training_start, end)
        raw_history = review.get("history_snapshot", [])
        annual = review.get("annual_progress_snapshot", [])
    else:
        evidence = _owned(records, resident_id, start, end)
        cumulative = _owned(records, resident_id, training_start, end)
        raw_history = reviews
        annual = tracking or []
    history_cutoff = _iso((review or {}).get("review_date")) or end
    history = [deepcopy(r) for r in raw_history if isinstance(r, dict)
               and r.get("resident_id") == resident_id
               and _iso(r.get("review_date")) and _iso(r.get("review_date")) <= history_cutoff]
    if review:
        history = [r for r in history if r.get("id") != review.get("id")]
        history.append(deepcopy(review))
    history.sort(key=lambda r: (_iso(r.get("review_date")), str(r.get("id", ""))))
    narrative_review = deepcopy(review) if review else (history[-1] if history else None)
    annual = [deepcopy(r) for r in annual if isinstance(r, dict)
              and r.get("resident_id") == resident_id
              and _iso(r.get("as_of")) and _iso(r.get("as_of")) <= end]
    annual.sort(key=lambda r: (_iso(r.get("period_start")), _iso(r.get("as_of"))))
    latest_annual: dict[tuple, dict] = {}
    for entry in annual:
        latest_annual[(entry.get("pgy"), entry.get("academic_year"), entry.get("period_start"), entry.get("period_end"))] = entry
    return _Context(profile, start, end, evidence, cumulative, history,
                    list(latest_annual.values()), narrative_review, review is not None, final, limited)


def _fmt(value: Any, *, decimals: int | None = None) -> str:
    if _blank(value):
        return ""
    number = _number(value)
    if number is None:
        return _text(value)
    if decimals is not None:
        return f"{number:.{decimals}f}"
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _date_display(value: Any) -> str:
    iso = _iso(value)
    if not iso:
        return _text(value)
    parsed = date.fromisoformat(iso)
    return f"{parsed.month}/{parsed.day}/{parsed.year}"


_ANNUAL_MAP = {
    "medicine_ite_score": "med_intran_r{pgy}",
    "medicine_ite_percentile": "med_intranp_r{pgy}",
    "pediatrics_ite_score": "peds_intran_r{pgy}",
    "pediatrics_ite_expected_pass_rate": "peds_intranp_r{pgy}",
}
_DUTY_MAP = {
    "duty_last_log": "dh_last_log",
    "duty_min_days_off": "dh_min_days_off",
    "duty_days_off_window": "dayoff_window",
    "duty_days_off_rotations": "dayoff_rotations",
    "duty_max_hours_4weeks": "eightyhr_max",
    "duty_80_hour_window": "eightyhr_window",
    "duty_80_hour_rotations": "eightyhr_rotations",
}
_NARRATIVE_MAP = {
    "nursing_feedback": "360_rn",
    "overall_impression": "overall_imp",
    "areas_for_growth": "areas_for_growth",
    "patterns_over_time": "patterns",
}
_MILESTONE_DOMAINS = {
    "Medicine": [("pc", 6), ("mk", 3), ("sbp", 3), ("pbli", 2), ("pro", 4), ("ics", 3)],
    "Pediatrics": [("pc", 5), ("mk", 2), ("sbp", 6), ("pbli", 2), ("pro", 4), ("ics", 3)],
}
_LEGACY_FIELD = re.compile(
    r"(?:review_period|subject_status|ccc_meeting_date|rotations|ad_clinic_num|ped_clinic_num|"
    r"360_rn|overall_imp|areas_for_growth|patterns|ccc_concerns|abstract|publications|advocacy|"
    r"dh_last_log|dh_min_days_off|dayoff_window|dayoff_rotations|eightyhr_max|eightyhr_window|"
    r"eightyhr_rotations|dh_comments|mp3|mp5)_r[1-4](?:my|eoy)$|"
    r"(?:pc|mk|sbp|pbli|pro|ics)\d+(?:mm|my|pm|py)_[1-4]$|"
    r"(?:med_intran|med_intranp|peds_intran|peds_intranp)_r[1-4]$|"
    r"obenc[1-4]_[1-4]$|conferences_r[1-4]$|(?:procedures|step1|step2ck|step3)$"
)


def _pgy(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    match = re.fullmatch(r"(?:PGY\s*|R)?([1-4])(?:\.0)?", _text(value), re.I)
    return int(match[1]) if match else None


def _review_target(review: dict) -> str | None:
    pgy = _pgy(review.get("pgy"))
    if pgy is None:
        return None
    return f"r{pgy}" + ("my" if review.get("half") == "Midyear" else "eoy")


def _eligible_history(ctx: _Context) -> list[dict]:
    """A meeting after the evidence cutoff must not import later source data."""
    selected = (ctx.narrative_review or {}).get("id") if ctx.explicit_review else None
    return [review for review in ctx.history
            if (selected and review.get("id") == selected)
            or (_iso(review.get("period_end")) or _iso(review.get("review_date"))) <= ctx.period_end]


def _academic_start(value: Any) -> date | None:
    try:
        year = int(str(value).split("-")[0])
        return date(year, 7, 1)
    except (TypeError, ValueError, OverflowError):
        return None


def _year_entries(ctx: _Context) -> list[dict]:
    """Retain annual identities, with old review fields as a dated fallback."""
    legacy: dict[tuple, dict] = {}
    training_start = date.fromisoformat(ctx.resident["start_date"])
    for review in _eligible_history(ctx):
        pgy = _pgy(review.get("pgy"))
        if pgy is None:
            continue
        academic = review.get("academic_year")
        first = _academic_start(academic) or m.add_months(training_start, 12 * (pgy - 1))
        # A resident who starts outside July keeps that actual training year.
        expected = m.add_months(training_start, 12 * (pgy - 1))
        if m.academic_year(expected) == (academic or m.academic_year(first)):
            first = expected
        last = m.add_months(first, 12) - timedelta(days=1)
        if first.isoformat() > ctx.period_end:
            continue
        identity = (pgy, academic or m.academic_year(first), first.isoformat(), last.isoformat())
        entry = legacy.setdefault(identity, {
            "pgy": pgy, "academic_year": identity[1], "period_start": identity[2],
            "period_end": identity[3], "as_of": "", "fields": {}, "legacy": True,
        })
        entry["fields"].update(deepcopy(review.get("fields", {})))
        entry["as_of"] = _iso(review.get("period_end")) or _iso(review.get("review_date"))

    result = []
    tracked = {(entry.get("pgy"), entry.get("academic_year")) for entry in ctx.tracking}
    for identity, entry in legacy.items():
        if identity[:2] not in tracked:
            result.append(entry)
    for entry in ctx.tracking:
        if _pgy(entry.get("pgy")) is None or (_iso(entry.get("period_start")) or "9999") > ctx.period_end:
            continue
        item = deepcopy(entry)
        fallback = [value for identity, value in legacy.items()
                    if identity[:2] == (entry.get("pgy"), entry.get("academic_year"))]
        # Modern fields, including explicitly cleared blanks, take precedence.
        base = deepcopy(fallback[-1]["fields"]) if fallback else {}
        item["fields"] = {**base, **item.get("fields", {})}
        result.append(item)
    present = {_pgy(entry.get("pgy")) for entry in result}
    for pgy in range(1, ctx.pgy + 1):
        first = m.add_months(training_start, 12 * (pgy - 1))
        if pgy not in present and first.isoformat() <= ctx.period_end:
            result.append({"pgy": pgy, "academic_year": m.academic_year(first),
                           "period_start": first.isoformat(),
                           "period_end": (m.add_months(first, 12) - timedelta(days=1)).isoformat(),
                           "as_of": "", "fields": {}})
    return sorted(result, key=lambda entry: (entry.get("period_start", ""), entry.get("as_of", ""),
                                             entry.get("period_end", ""), entry.get("pgy", 0)))


def _entry_label(entry: dict) -> str:
    label = f"PGY{entry['pgy']}"
    if entry.get("academic_year"):
        label += f" ({entry['academic_year']})"
    if entry.get("period_start") and entry.get("period_end"):
        label += f", {entry['period_start']} to {entry['period_end']}"
    if entry.get("as_of"):
        label += f", recorded through {entry['as_of']}"
    return label


def _training_year_start(ctx: _Context, pgy: int, observed: str, entries: list[dict]) -> date:
    matches = [entry for entry in entries if _pgy(entry.get("pgy")) == pgy
               and (_iso(entry.get("period_start")) or "9999") <= observed
               <= (_iso(entry.get("period_end")) or "")]
    for entry in reversed(matches):
        first, last = date.fromisoformat(entry["period_start"]), date.fromisoformat(entry["period_end"])
        if (last - first).days >= 300:
            return first
    if matches:
        first = _academic_start(matches[-1].get("academic_year"))
        if first:
            return first
    scheduled = m.add_months(date.fromisoformat(ctx.resident["start_date"]), 12 * (pgy - 1))
    if scheduled.isoformat() <= observed < m.add_months(scheduled, 12).isoformat():
        return scheduled
    # Source PGY may describe a repeated/extended year. Never apply the current
    # roster override to older evidence. Retain its observed academic period.
    return _academic_start(m.academic_year(observed)) or scheduled


def _record_year(record: dict, ctx: _Context, entries: list[dict]) -> tuple[int, str]:
    observed = _iso(record.get("date")) or _iso(record.get("period_end"))
    supplied = _pgy(record.get("pgy"))
    matches = [entry for entry in entries if (_iso(entry.get("period_start")) or "9999") <= observed
               <= (_iso(entry.get("period_end")) or "")]
    if supplied is not None:
        matches = [entry for entry in matches if _pgy(entry.get("pgy")) == supplied]
        academic = matches[-1].get("academic_year") if matches else m.academic_year(observed)
        return supplied, str(academic)
    if matches:
        return int(matches[-1]["pgy"]), str(matches[-1].get("academic_year") or m.academic_year(observed))
    historic_profile = {**ctx.resident, "pgy_override": None}
    return min(4, max(1, m.current_pgy(historic_profile, observed))), m.academic_year(observed)


def _target(ctx: _Context, entries: list[dict]) -> str:
    if ctx.explicit_review:
        return _review_target(ctx.narrative_review or {}) or f"r{ctx.pgy}eoy"
    if m.current_pgy(ctx.resident, ctx.period_end) > 4 and not any(
        entry.get("pgy") == 4 and entry.get("fields")
        and entry.get("period_start", "9999") <= ctx.period_end <= entry.get("period_end", "")
        for entry in entries
    ):
        # A report generated after the four-year program retains PGY4 EOY.
        return "r4eoy"
    first = _training_year_start(ctx, ctx.pgy, ctx.period_end, entries)
    half = "my" if ctx.period_end < m.add_months(first, 6).isoformat() else "eoy"
    return f"r{ctx.pgy}{half}"


def _paragraphs(parts: Iterable[Any]) -> str:
    """Escape admin-entered plain text before the notebook's HTML cleanup."""
    return "".join("<p>" + html.escape(_scalar(part)).replace("\n", "<br>") + "</p>"
                   for part in parts if not _blank(part))


def _scalar(value: Any) -> str:
    """Keep notebook HTML intact while removing invalid XML control characters."""
    if _blank(value):
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", str(value))


def _join_distinct(parts: Iterable[Any], separator: str = "\n\n") -> str:
    return separator.join(dict.fromkeys(_text(part) for part in parts if not _blank(part)))


def _annual_fields(row: dict, ctx: _Context, entries: list[dict], target: str, notes: list[str]) -> None:
    by_pgy: dict[int, list[dict]] = defaultdict(list)
    for entry in entries:
        by_pgy[int(entry["pgy"])].append(entry)
    for pgy, candidates in by_pgy.items():
        selected = max(candidates, key=lambda entry: (entry.get("as_of", ""), entry.get("period_start", ""),
                                                      entry.get("period_end", "")))
        fields = selected.get("fields", {})
        for modern, legacy in _ANNUAL_MAP.items():
            if modern in fields:
                row[legacy.format(pgy=pgy)] = _fmt(fields[modern])
        checkpoint = target if pgy == ctx.pgy else f"r{pgy}eoy"
        for modern, legacy in (("adult_clinic_count", "ad_clinic_num"), ("pediatric_clinic_count", "ped_clinic_num")):
            if modern in fields:
                row[f"{legacy}_{checkpoint}"] = _fmt(fields[modern])
        for number in range(1, 5):
            key = f"observed_encounter_{number}"
            if key in fields:
                value = fields[key]
                row[f"obenc{number}_{pgy}"] = "1" if value is True else "0" if value is False else ""
                if value is False and len(candidates) == 1:
                    notes.append(f"{_entry_label(selected)}: observed encounter {number} not completed.")
        # The notebook has one cell per PGY. Additional annual identities are
        # preserved in its existing CCC narrative, without changing its grid.
        if len(candidates) > 1 or pgy > ctx.pgy:
            for entry in candidates:
                details = []
                for key, label in ANNUAL_LABELS.items():
                    value = entry.get("fields", {}).get(key)
                    if value is None:
                        continue
                    display = "Completed" if value is True else "Not completed" if value is False else _fmt(value)
                    if display:
                        details.append(f"{label}: {display}")
                if details:
                    notes.append("Annual progress: " + _entry_label(entry) + "\n" + "\n".join(details))
        for entry in candidates:
            old = entry.get("fields", {})
            if not _blank(old.get("pediatrics_ite_percentile")):
                notes.append(f"{_entry_label(entry)}: Pediatrics ITE percentile (legacy entry): "
                             + _fmt(old["pediatrics_ite_percentile"]) + ".")
            if not _blank(old.get("observed_encounters_completed")):
                notes.append(f"{_entry_label(entry)}: observed encounters completed (legacy total): "
                             + _fmt(old["observed_encounters_completed"]) + ".")


def _milestone_fields(row: dict, ctx: _Context, entries: list[dict], target: str, notes: list[str]) -> None:
    grouped: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
    current = int(target[1])
    visible = {f"{base}{suffix}_{pgy}"
               for specialty, domains in _MILESTONE_DOMAINS.items()
               for pgy, suffix, _ in word.get_milestone_rows_for_target(target, specialty.lower())
               for domain, count in domains for base in [f"{domain}{i}" for i in range(1, count + 1)]}
    for record in sorted(ctx.cumulative, key=lambda item: (item.get("date", ""), item.get("id", ""))):
        specialty = record.get("specialty")
        if record.get("category") != "milestones" or specialty not in _MILESTONE_DOMAINS:
            continue
        pgy, academic = _record_year(record, ctx, entries)
        observed = _iso(record.get("date")) or _iso(record.get("period_end"))
        first = _training_year_start(ctx, pgy, observed, entries)
        half_value = _text(record.get("half") or record.get("values", {}).get("half")).casefold()
        if half_value in {"midyear", "my", "mid-year"}:
            half = "my"
        elif half_value in {"year end", "end of year", "eoy", "end-of-year"}:
            half = "eoy"
        else:
            half = "my" if observed < m.add_months(first, 6).isoformat() else "eoy"
        metric = re.sub(r"[\s_-]+", "", _text(record.get("metric"))).lower()
        metric = re.sub(r"^prof(?=\d)", "pro", metric)
        allowed = {f"{domain}{i}" for domain, count in _MILESTONE_DOMAINS[specialty] for i in range(1, count + 1)}
        value = record.get("value")
        if _blank(value):
            value = record.get("values", {}).get("assessment_status", "")
        display = _fmt(value)
        suffix = ("mm" if half == "my" else "my") if specialty == "Medicine" else ("pm" if half == "my" else "py")
        field = f"{metric}{suffix}_{pgy}"
        label = f"{specialty} PGY{pgy} {half.upper()} ({academic}), {observed}, {metric.upper()}"
        if metric in allowed and pgy <= current:
            grouped[field].append((display, label, record))
        elif display:
            notes.append(label + ": " + display)
        if _text(record.get("text")):
            notes.append(label + " comment: " + _text(record["text"]))
    for field, values in grouped.items():
        # A later dated score updates its slot. Multiple source values are
        # retained below; the notebook still receives one scalar per milestone.
        row[field] = values[-1][0]
        if len(values) > 1 or field not in visible:
            for display, label, _ in values:
                if display:
                    notes.append(label + ": " + display)


def _procedure_rows(ctx: _Context, notes: list[str]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for record in ctx.cumulative:
        if record.get("category") == "procedures":
            grouped[(_text(record.get("specialty")), _text(record.get("metric")),
                     record.get("record_mode", "event"))].append(record)
    rows = []
    for (specialty, metric, mode), records in sorted(grouped.items(),
            key=lambda pair: max(record.get("date", "") for record in pair[1]), reverse=True):
        duplicate_name = len({key[0] for key in grouped if key[1] == metric}) > 1
        label = metric + (f" ({specialty})" if duplicate_name else "")
        if mode == "snapshot":
            for record in sorted(records, key=lambda item: item.get("date", "")):
                period = ""
                if record.get("period_start") and record.get("period_end"):
                    period = f"\n{record['period_start']} to {record['period_end']}"
                rows.append({"Procedure": label + " (source summary)" + period,
                             "last_date_logged": _date_display(record.get("date")),
                             "number_logged": _fmt(record.get("value"))})
        else:
            rows.append({"Procedure": label,
                         "last_date_logged": _date_display(max(record.get("date", "") for record in records)),
                         "number_logged": str(len(records))})
        for record in records:
            if _text(record.get("text")):
                notes.append(f"Procedure: {label}, {record.get('date', '')}\n{_text(record['text'])}")
    return rows


def _conference_rows(ctx: _Context, entries: list[dict], notes: list[str]) -> list[dict]:
    grouped: dict[tuple, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for record in sorted(ctx.cumulative, key=lambda item: (item.get("date", ""), item.get("id", ""))):
        if record.get("category") != "conferences":
            continue
        pgy, academic = _record_year(record, ctx, entries)
        unit = _text(record.get("unit"))
        percentage = unit.casefold() in {"%", "percent", "percentage"}
        value = _fmt(record.get("value"), decimals=2 if percentage else None)
        if value and _number(record.get("value")) is not None and percentage:
            value += "%"
        elif value and unit:
            value += " " + unit
        elif not value:
            value = _text(record.get("values", {}).get("status"))
        first, last = _iso(record.get("period_start")), _iso(record.get("period_end"))
        if first and last:
            value += f"\n{first} to {last}"
        elif record.get("date"):
            value += f"\nAs of {record['date']}"
        specialty, metric = _text(record.get("specialty")), _text(record.get("metric"))
        if pgy <= ctx.pgy:
            grouped[(specialty, metric)][pgy].append(value)
        else:
            notes.append(f"Conference attendance: {specialty}, {metric}, PGY{pgy} ({academic})\n{value}")
        if _text(record.get("text")):
            notes.append(f"Conference attendance: {specialty}, {metric}\n{_text(record['text'])}")
    return [{"Category": f"{specialty} :: {metric}" if specialty else metric,
             **{f"PGY{pgy}": "\n\n".join(values) for pgy, values in years.items()}}
            for (specialty, metric), years in sorted(grouped.items())]


def _duty_fields(row: dict, ctx: _Context, target: str, notes: list[str]) -> None:
    shifts = [record for record in ctx.evidence if record.get("category") == "duty_hours"]
    calculated = duty_deep_dive(shifts)
    if calculated:
        notes.append(DUTY_SOURCE_METHOD_NOTE)
    for key, value in calculated.items():
        if key == "calculation_note":
            if value:
                notes.append(_text(value))
        else:
            row[f"{key}_{target}"] = _fmt(value)
    for modern, legacy in _DUTY_MAP.items():
        if not _blank(ctx.fields.get(modern)):
            row[f"{legacy}_{target}"] = _fmt(ctx.fields[modern])
    manual = _text(ctx.fields.get("duty_resolution_notes"))
    if manual:
        row[f"dh_comments_{target}"] = manual
    rows = []
    for record in sorted(ctx.evidence, key=lambda item: (item.get("date", ""), item.get("id", ""))):
        if record.get("category") != "duty_violations":
            continue
        values = record.get("values") if isinstance(record.get("values"), dict) else {}
        detail = _join_distinct([record.get("text"), values.get("description"), values.get("comments")])
        if values.get("work_type"):
            detail += ("\n" if detail else "") + "Work type: " + _text(values["work_type"])
        if not _blank(record.get("value")):
            detail += ("\n" if detail else "") + "Source value: " + _fmt(record["value"])
            if record.get("unit"):
                detail += " " + _text(record["unit"])
        if manual:
            detail += ("\n\n" if detail else "") + "Committee: " + manual
        rows.append({"review_period": f"PGY{ctx.pgy} {'MY' if target.endswith('my') else 'EOY'}\n"
                     + (_iso(record.get("date")) or ""),
                     "rotation": _text(record.get("rotation") or values.get("training_location")),
                     "violation": _text(record.get("metric") or values.get("rule")), "notes": detail})
    row["duty_summary_rows"] = json.dumps(rows, ensure_ascii=False)
    # Summary rows do not have the shifts needed by the original rolling-window
    # calculation. Keep their supplied values visible as source summaries.
    for record in shifts:
        values = record.get("values") if isinstance(record.get("values"), dict) else {}
        if record.get("record_mode") == "snapshot":
            summary = _fmt(record.get("value"))
            if record.get("unit"):
                summary += " " + _text(record["unit"])
            notes.append(f"Duty-hour source summary: {record.get('date', '')}, {record.get('metric', '')}\n{summary}")
        if _text(record.get("text")):
            notes.append(f"Duty-hour log: {record.get('date', '')}\n{_text(record['text'])}")


def _prepare_row(ctx: _Context) -> pd.Series:
    entries = _year_entries(ctx)
    target = _target(ctx, entries)
    row: dict[str, str] = {}
    history = _eligible_history(ctx)
    # Already supplied legacy scalar fields can be used without reformatting.
    # Metadata fields are always overwritten from the selected resident/review.
    sources = [ctx.resident, *[review.get("fields", {}) for review in history]]
    for source in sources:
        if isinstance(source, dict):
            for key, value in source.items():
                if _LEGACY_FIELD.fullmatch(str(key)) and not isinstance(value, (dict, list)):
                    row[key] = _scalar(value)
    for pgy in range(1, 5):
        for half in ("my", "eoy"):
            row[f"review_period_r{pgy}{half}"] = ""
    row.update(subject_name=_text(ctx.resident.get("name")),
               coach_name=_text(ctx.fields.get("coach_name") or ctx.resident.get("coach_name")),
               graduation_date=_text(ctx.resident.get("graduation_date")))
    row[f"subject_status_{target}"] = f"R{ctx.pgy}"
    row[f"review_period_{target}"] = f"{ctx.period_start} to {ctx.period_end}"
    row[f"ccc_meeting_date_{target}"] = _text((ctx.narrative_review or {}).get("review_date"))
    rotations = _text(ctx.fields.get("rotation_summary"))
    if not rotations:
        rotations = _join_distinct((record.get("rotation") for record in ctx.evidence), "; ")
    row[f"rotations_{target}"] = rotations
    notes: list[str] = []
    if ctx.limited_final_history:
        notes.append("Historical data limited to this saved review. The finalized review has no separate cumulative snapshot.")
    _annual_fields(row, ctx, entries, target, notes)
    _milestone_fields(row, ctx, entries, target, notes)
    procedure_rows = _procedure_rows(ctx, notes)
    if procedure_rows:
        row["procedures"] = procedures_html(procedure_rows)
    conference_rows = _conference_rows(ctx, entries, notes)
    if conference_rows:
        row[f"conferences_r{ctx.pgy}"] = conferences_html(conference_rows, ctx.pgy)
    _duty_fields(row, ctx, target, notes)

    # Cumulative source fields retain separate checkpoint entries in the
    # notebook's own scholarship and advocacy tables, including repeated PGYs.
    allowed_targets = set(word.get_cumulative_targets(target))
    grouped: dict[tuple, list[str]] = defaultdict(list)
    for review in history:
        checkpoint = _review_target(review)
        if checkpoint is None:
            continue
        for modern, legacy in (("abstracts", "abstract"), ("publications", "publications"), ("advocacy", "advocacy")):
            value = _text(review.get("fields", {}).get(modern))
            if value:
                destination = checkpoint if checkpoint in allowed_targets else target
                grouped[(legacy, destination)].append(value)
    for (legacy, checkpoint), values in grouped.items():
        row[f"{legacy}_{checkpoint}"] = _join_distinct(values)
    for modern, legacy in _NARRATIVE_MAP.items():
        if modern in ctx.fields:
            row[f"{legacy}_{target}"] = _paragraphs([ctx.fields[modern]])
    if _text(ctx.fields.get("strengths")):
        current = row.get(f"overall_imp_{target}", "")
        row[f"overall_imp_{target}"] = current + _paragraphs(["Strengths: " + _text(ctx.fields["strengths"])])

    direct = {"coach_name", "rotation_summary", "strengths", "abstracts", "publications", "advocacy",
              "ccc_summary", "duty_resolution_notes", *_NARRATIVE_MAP, *_DUTY_MAP, *_ANNUAL_MAP,
              "adult_clinic_count", "pediatric_clinic_count", "pediatrics_ite_percentile",
              "observed_encounters_completed", *[f"observed_encounter_{i}" for i in range(1, 5)],
              "usmle_step1", "usmle_step2ck", "usmle_step3"}
    committee = [_text(ctx.fields.get("ccc_summary"))]
    labels = {**m.REVIEW_TEXT_FIELDS, **m.REVIEW_VALUE_FIELDS, **ANNUAL_LABELS}
    for key, value in ctx.fields.items():
        if key in direct or _LEGACY_FIELD.fullmatch(str(key)) or _blank(value):
            continue
        label = labels.get(key, str(key).replace("_", " ").capitalize())
        committee.append(f"{label}\n{_text(value)}")
    for review in history:
        for modern, legacy in (("usmle_step1", "step1"), ("usmle_step2ck", "step2ck"), ("usmle_step3", "step3")):
            fields = review.get("fields", {})
            if modern in fields:
                row[legacy] = _fmt(fields[modern])
    committee.extend(notes)
    if any(committee):
        row[f"ccc_concerns_{target}"] = row.get(f"ccc_concerns_{target}", "") + _paragraphs(committee)

    for specialty, legacy, title, color in (
        ("Medicine", "mp5", "MEDICINE EVALUATION SUMMARY", "#D9EAF7"),
        ("Pediatrics", "mp3", "PEDIATRIC EVALUATION SUMMARY", "#BFEDD2"),
    ):
        records = [record for record in ctx.evidence
                   if record.get("category") == "evaluations" and evaluation_specialty(record) == specialty]
        if records:
            row[f"{legacy}_{target}"] = evaluations_html(records, title, color)
    # The original renderer calls pd.notna on each field, so every value must
    # remain a scalar string. "0" is recorded data; "" is genuinely missing.
    return pd.Series({key: _scalar(value) for key, value in row.items()}, dtype=object)


def prepare_notebook_row(
    resident: dict,
    records: list[dict],
    reviews: list[dict],
    *,
    period_start: str,
    period_end: str,
    review: dict | None = None,
    tracking: list[dict] | None = None,
) -> pd.Series:
    """Return the exact old REDCap-style row expected by the user's notebook."""
    ctx = _context(resident, records, reviews, period_start, period_end, review, tracking)
    return _prepare_row(ctx)


def build_ccc_summary(
    resident: dict,
    records: list[dict],
    reviews: list[dict],
    *,
    period_start: str,
    period_end: str,
    review: dict | None = None,
    program_name: str = "Medicine Pediatrics Residency",
    tracking: list[dict] | None = None,
) -> bytes:
    """Build an ordinary editable DOCX using the actual Jupyter Word functions.

    The original formatting, table order, numerical descriptions, name cleanup,
    and Medicine/Pediatrics page breaks are retained. Recorded USMLE scores use
    the notebook's existing optional table. Evaluation sections follow the
    rotation name and show narrative feedback only. Self-assessments use the
    same evaluation layout after the original report.
    """
    ctx = _context(resident, records, reviews, period_start, period_end, review, tracking)
    row = _prepare_row(ctx)
    doc = word.create_doc(row, 0)
    if doc is None:
        raise m.ValidationError("Choose a valid review checkpoint before preparing the CCC summary.")
    if any(word.has_value(row, key) for key in ("step1", "step2ck", "step3")):
        # The call is commented out in the source notebook. When scores were
        # entered, use that existing helper in its original intended position.
        scholarly = next((table for table in doc.tables if table.cell(0, 0).text == "SCHOLARLY ACTIVITIES"), None)
        word.add_usmle_scores_table(doc, row)
        if scholarly is not None:
            scholarly._tbl.addprevious(doc.tables[-1]._tbl)
    self_assessments = [record for record in ctx.evidence if record.get("category") == "self_assessments"]
    if self_assessments:
        word.add_page_break(doc)
        source = evaluations_html(self_assessments, "RESIDENT SELF-ASSESSMENT", "#D9EAF7")
        word.add_html_table_to_doc(doc, word.clean_eval_html_keep_formatting(source, row["subject_name"]), table_width=7.6)
    output = BytesIO()
    doc.save(output)
    return output.getvalue()




# Original HTML builders from the supplied processing notebooks.
# Inlined so this update requires no additional deployed helper module.

def make_procedure_html(group):
    rows = []

    # Header row
    rows.append(
        '<tr style="height:24.8611px;">'
        '<td style="background-color:#ecf0f1;text-align:center;" colspan="4">PROCEDURE</td>'
        '<td style="background-color:#ecf0f1;text-align:center;" colspan="3">LAST DATE LOGGED</td>'
        '<td style="background-color:#ecf0f1;text-align:center;" colspan="3">NUMBER LOGGED</td>'
        '</tr>'
    )

    # Data rows
    for _, row in group.iterrows():
        procedure = html.escape(str(row["Procedure"]))
        last_date = html.escape(str(row["last_date_logged"]))
        number_logged = html.escape(str(row["number_logged"]))

        rows.append(
            '<tr>'
            f'<td style="background-color:#ffffff;text-align:center;" colspan="4">{procedure}</td>'
            f'<td style="background-color:#ffffff;text-align:center;" colspan="3">{last_date}</td>'
            f'<td style="background-color:#ffffff;text-align:center;" colspan="3">{number_logged}</td>'
            '</tr>'
        )

    table = (
        '<table style="border-collapse:collapse;width:100%;height:24.8611px;" border="1">'
        '<colgroup>'
        '<col style="width:10.0369%;"><col style="width:10.0369%;"><col style="width:10.0369%;">'
        '<col style="width:10.0369%;"><col style="width:10.0369%;"><col style="width:10.0369%;">'
        '<col style="width:10.0369%;"><col style="width:10.0369%;"><col style="width:10.0369%;">'
        '<col style="width:9.66814%;">'
        '</colgroup>'
        '<tbody>'
        + "".join(rows) +
        '</tbody></table><p>&nbsp;</p>'
    )

    return table


def make_conference_table(group, max_pgy):
    pgy_cols = [f"PGY{i}" for i in range(1, max_pgy + 1)]

    # Always keep PGY1-PGY4 structure for REDCap/table consistency
    display_pgy_cols = ["PGY1", "PGY2", "PGY3", "PGY4"]

    group = group.copy().fillna("")

    colgroup = (
        '<colgroup>'
        '<col style="width: 10.0369%;">'
        '<col style="width: 10.0369%;">'
        '<col style="width: 10.0369%;">'
        '<col style="width: 10.0369%;">'
        '<col style="width: 10.0369%;">'
        '<col style="width: 10.0369%;">'
        '<col style="width: 10.0369%;">'
        '<col style="width: 10.0369%;">'
        '<col style="width: 10.0369%;">'
        '<col style="width: 9.66814%;">'
        '</colgroup>'
    )

    html = (
        '<table style="border-collapse: collapse; width: 100%;" border="1">'
        f'{colgroup} '
        '<tbody>'
        '<tr>'
        '<td style="text-align: center; background-color: #ecf0f1;" colspan="6">CATEGORY</td>'
        '<td style="text-align: center; background-color: #ecf0f1;">PGY1</td>'
        '<td style="text-align: center; background-color: #ecf0f1;">PGY2</td>'
        '<td style="text-align: center; background-color: #ecf0f1;">PGY3</td>'
        '<td style="text-align: center; background-color: #ecf0f1;">PGY4</td>'
        '</tr>'
    )

    for _, r in group.iterrows():
        category = r.get("Category", "") or "&nbsp;"

        html += (
            '<tr>'
            f'<td style="text-align: center; background-color: #ffffff;" colspan="6">{category}</td>'
        )

        for col in display_pgy_cols:
            value = r.get(col, "")

            # Hide PGYs above current level
            if col not in pgy_cols:
                value = ""

            if value == "":
                value = "&nbsp;"

            html += (
                f'<td style="text-align: center; background-color: #ffffff;">{value}</td>'
            )

        html += '</tr>'

    html += '</tbody></table> <p>&nbsp;</p>'

    return html


def clean_html_text(value):
    value = str(value).strip()
    if value.lower() in ["nan", "na", "n/a", "none"]:
        return ""
    value = html.escape(value)
    value = value.replace("\n", "<br>")
    return value


def build_eval_html(eval_rows, title, header_color):
    """
    Creates a REDCap-ready HTML table.
    Only includes evaluations with at least one useful value.
    """

    usable_rows = []

    for r in eval_rows:
        date = str(r.get("date", "")).strip()
        rotation = str(r.get("rotation", "")).strip()
        evaluator = str(r.get("evaluator", "")).strip()
        comments = str(r.get("comments", "")).strip()

        if date or rotation or evaluator or comments:
            usable_rows.append({
                "date": date,
                "rotation": rotation,
                "evaluator": evaluator,
                "comments": comments,
            })

    if not usable_rows:
        return ""

    html_parts = []

    html_parts.append(
        '<table style="border-collapse: collapse; width: 100%;" border="1">'
    )
    html_parts.append(
        '<colgroup>'
        '<col style="width: 10.3877%;">'
        '<col style="width: 4.34542%;">'
        '<col style="width: 16.6893%;">'
        '<col style="width: 8.47936%;">'
        '<col style="width: 17.8902%;">'
        '<col style="width: 8.43152%;">'
        '<col style="width: 8.43152%;">'
        '<col style="width: 8.43152%;">'
        '<col style="width: 8.43152%;">'
        '<col style="width: 8.48187%;">'
        '</colgroup>'
    )
    html_parts.append("<tbody>")

    html_parts.append(
        f'<tr style="height: 25px;">'
        f'<td style="text-align: center; background-color: {header_color};" colspan="10">'
        f'{title}'
        f'</td>'
        f'</tr>'
    )

    for r in usable_rows:
        date = clean_html_text(r["date"])
        rotation = clean_html_text(r["rotation"])
        evaluator = clean_html_text(r["evaluator"])
        comments = clean_html_text(r["comments"])

        html_parts.append(
            '<tr style="height: 25px;">'
            f'<td style="background-color: #ffffff; text-align: left;" colspan="2">{date}</td>'
            f'<td style="background-color: #ffffff; text-align: center;" colspan="2">'
            f'<span style="color: #e03e2d;">{rotation}</span>'
            f'</td>'
            f'<td style="background-color: #ffffff; text-align: center;" colspan="6">{evaluator}</td>'
            f'</tr>'
        )

        if comments:
            html_parts.append(
                '<tr style="height: 25px;">'
                f'<td style="background-color: #ffffff; text-align: left;" colspan="10">'
                f'{comments}'
                f'</td>'
                f'</tr>'
            )

    html_parts.append("</tbody></table>")

    return "".join(html_parts)


def _display(value: Any) -> str:
    """Keep prepared scalar text, including zero, while blanking missing values."""
    if value is None or (pd.api.types.is_scalar(value) and pd.isna(value)):
        return ""
    return str(value)


def procedures_html(summary_rows: Iterable[Mapping[str, Any]]) -> str:
    """Render prepared procedure summaries with the notebook's 4/3/3 columns.

    Each row supplies ``Procedure``, ``last_date_logged``, and ``number_logged``.
    Date labels, units, and counts are passed through as supplied by the caller.
    Empty source data leave the legacy ``procedures`` field blank.
    """
    columns = ("Procedure", "last_date_logged", "number_logged")
    rows = [{column: _display(row.get(column)) for column in columns} for row in summary_rows]
    if not rows:
        return ""
    return make_procedure_html(pd.DataFrame(rows, columns=columns))


def conferences_html(summary_rows: Iterable[Mapping[str, Any]], max_pgy: int) -> str:
    """Render prepared category/PGY summaries in the original ten-column table.

    Each row supplies ``Category`` and any of ``PGY1`` through ``PGY4``. The
    original renderer leaves years above ``max_pgy`` blank. Source values are
    escaped before its literal interpolation, so their text remains in its own
    cell even when a category or unit contains HTML punctuation.
    """
    columns = ("Category", "PGY1", "PGY2", "PGY3", "PGY4")
    rows = [
        {column: html.escape(_display(row.get(column))) for column in columns}
        for row in summary_rows
    ]
    if not rows:
        return ""
    return make_conference_table(pd.DataFrame(rows, columns=columns), max_pgy)


def evaluation_specialty(record: dict) -> str:
    """Route a Word evaluation by its rotation, without editing saved evidence.

    PEDS, Pediatric and Paediatric rotation names take priority over an upload's
    specialty label. All other named rotations go to Medicine. For a missing
    rotation only, retain an explicitly saved Pediatrics label.
    """
    rotation = _scalar(record.get("rotation")).strip()
    if rotation:
        return "Pediatrics" if re.search(r"peds|pa?ediatr", rotation, re.I) else "Medicine"
    specialty = _scalar(record.get("specialty")).strip()
    return "Pediatrics" if re.search(r"peds|pa?ediatr", specialty, re.I) else "Medicine"


_COMMENT_PREFIX = re.compile(r"^\s*(?:(?:overall|additional|general)\s+)?comments?\s*:\s*", re.I)
_QUESTION_PREFIX = re.compile(r"^\s*Question(?:\s+[\w.-]+)?\s*(?::\s*(.*))?$", re.I)
_RESPONSE_PREFIX = re.compile(r"^\s*(?:response|answer|rating|score)\s*:\s*(.*)$", re.I)
_NARRATIVE_PROMPT = re.compile(
    r"\b(?:comments?|feedback|strengths?|improv\w*|growth|goals?|recommend\w*|suggest\w*|"
    r"describe|elaborate|explain|narrative|free[ -]?text|follow[ -]?up)\b", re.I
)


def _feedback_plain(value: Any) -> str:
    """Read rich-text feedback as plain text, preserving paragraph boundaries."""
    text = _scalar(value).replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"</?(?:p|br|div|li|ul|ol|span|b|strong|i|em|script|style)\b", text, re.I):
        soup = BeautifulSoup(text, "html.parser")
        for hidden in soup.find_all(["script", "style"]):
            hidden.decompose()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for block in soup.find_all(["p", "div", "li"]):
            block.insert_after("\n\n")
        text = soup.get_text()
    text = html.unescape(text).replace("\u00a0", " ")
    text = "\n".join(line.strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _without_comment_labels(value: Any) -> str:
    lines = []
    for line in _feedback_plain(value).splitlines():
        while _COMMENT_PREFIX.match(line):
            line = _COMMENT_PREFIX.sub("", line, count=1)
        lines.append(line)
    return "\n".join(lines).strip()


def _written_response(question: dict) -> str:
    """Retain actual prose answers to narrative prompts, excluding rating items."""
    value = question.get("response")
    if not isinstance(value, str):
        return ""
    response = _without_comment_labels(value)
    if not response or re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?|%)?", response):
        return ""
    kind = _scalar(question.get("response_type") or question.get("question_type") or question.get("type")).casefold()
    if any(label in kind for label in ("rating", "scale", "choice", "likert", "number", "boolean")):
        return ""
    prompt = _feedback_plain(question.get("text"))
    explicit_text = any(label in kind for label in ("text", "narrative", "comment", "essay"))
    if explicit_text or (_NARRATIVE_PROMPT.search(prompt)
                         and not re.search(r"\b(?:rate|rating|select|scale)\b", prompt, re.I)):
        return _RESPONSE_PREFIX.sub(r"\1", response, count=1)
    return ""


def _legacy_feedback(value: Any) -> str:
    """Extract feedback from older text blocks containing question/answer labels.

    Structured question comments never go through this parser. It is only for
    a record's convenience/legacy text field. Multiple-line prompts and rating
    responses are omitted, while narrative responses and comments are kept.
    """
    text = _feedback_plain(value)
    blocks: list[str] = []
    buffer: list[str] = []
    mode = "feedback"
    prompt = ""

    def flush() -> None:
        block = "\n".join(buffer).strip()
        if mode == "response":
            block = _written_response({"text": prompt, "response": block})
        if block:
            blocks.append(block)
        buffer.clear()

    for line in text.splitlines():
        question = _QUESTION_PREFIX.match(line)
        response = _RESPONSE_PREFIX.match(line)
        if question:
            flush()
            mode, prompt = "question", question.group(1) or ""
        elif _COMMENT_PREFIX.match(line):
            flush()
            mode = "feedback"
            buffer.append(_without_comment_labels(line))
        elif response:
            flush()
            mode = "response"
            buffer.append(response.group(1))
        elif re.match(r"^\s*Source value(?:\s*\([^)]*\))?\s*:", line, re.I):
            flush()
            mode = "rating"
        elif mode == "question":
            prompt += " " + line
        elif mode in {"feedback", "response"}:
            buffer.append(line)
    flush()
    return "\n\n".join(blocks)


def evaluation_comment_text(record: dict) -> str:
    """Return the author's feedback with no question wording or field labels.

    Preserve the full comments and their original order. Actual repeated
    comments remain present, but an importer's convenience copy is not added a
    second time. Source fields are not modified by this display-only cleanup.
    """
    questions = record.get("questions") if isinstance(record.get("questions"), list) else []
    feedback: list[str] = []
    comments: list[str] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        written = _written_response(question)
        comment = _without_comment_labels(question.get("comment"))
        if written and written != comment:
            feedback.append(written)
        if comment:
            feedback.append(comment)
            comments.append(comment)
    values = record.get("values") if isinstance(record.get("values"), dict) else {}
    overall = list(dict.fromkeys(value for value in (
        _without_comment_labels(record.get("overall_comment")),
        _without_comment_labels(values.get("overall_comments")),
    ) if value))
    independent = _legacy_feedback(record.get("text"))
    parts: list[str] = []
    if (independent and not _is_convenience_text(independent, feedback, overall)
            and not _is_convenience_text(independent, comments, overall)):
        parts.append(independent)
    parts.extend(feedback)
    parts.extend(overall)
    return "\n\n".join(parts)


def evaluations_html(records: list[dict], title: str, header_color: str) -> str:
    """Use the notebook's table layout with narrative feedback in each body row."""
    prepared = [{**record, "text": evaluation_comment_text(record), "questions": [],
                 "overall_comment": "", "values": {}, "value": None}
                for record in records]
    rows = [
        {
            "date": row["date"],
            "rotation": row["rotation"],
            "evaluator": row["evaluator"],
            "comments": row["narrative"],
        }
        for row in evaluation_rows(prepared)
    ]
    # The notebook's Word converter reads cells with get_text("\n"). Character
    # references preserve blank lines between comments through that conversion.
    return build_eval_html(rows, html.escape(str(title)), header_color).replace("<br>", "&#10;")


# Rolling-window calculation adapted from the supplied duty-hours notebook.

DUTY_SOURCE_METHOD_NOTE = (
    "Duty calculations follow the uploaded log and the original notebook: "
    "dates without a logged shift are treated as days off. Incomplete logs "
    "can understate hours and overstate days off."
)


def duty_deep_dive(records: list[dict]) -> dict:
    """Return un-suffixed legacy duty fields for prefiltered resident records.

    Caller owns resident/date filtering.  Summary snapshots, missing datetimes,
    and nonnumeric hours never become shifts.  An empty dict denotes absence of
    usable shift data; an actual calculated zero remains a numeric zero.  The
    original notebook's undefined extrema windows (``None to None``) become
    blank strings so that missing dates are not presented as actual windows.
    """
    shifts = []
    for record in records:
        if (not isinstance(record, dict)
                or record.get("category") != "duty_hours"
                or record.get("record_mode") == "snapshot"
                or not record.get("active", True)):
            continue
        values = record.get("values")
        if not isinstance(values, dict):
            continue
        start_raw = values.get("start_datetime") or values.get("start")
        end_raw = values.get("end_datetime") or values.get("end")
        if not start_raw or not end_raw:
            continue
        try:
            start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
            hours_raw = values.get("reported_hours")
            if hours_raw is None:
                hours_raw = record.get("value")
            if isinstance(hours_raw, bool):
                continue
            hours = float(hours_raw)
            if end < start or not math.isfinite(hours) or hours < 0:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        rotation = record.get("rotation")
        rotation = "" if rotation is None else str(rotation)
        is_non_work = bool(re.search("VACATION|LEAVE OF ABSENCE", rotation.upper()))
        shifts.append((start.date(), end.date(), hours, rotation, is_non_work))

    if not shifts:
        return {}

    first_day = min(shift[0] for shift in shifts)
    last_day = max(shift[1] for shift in shifts)
    worked_days = set()
    non_work_days = set()
    daily_hours = defaultdict(float)
    daily_rotations = defaultdict(set)
    for start, end, hours, rotation, is_non_work in shifts:
        if is_non_work:
            day = start
            while day <= end:
                non_work_days.add(day)
                day += timedelta(days=1)
        else:
            if hours > 0:
                worked_days.add(start)
            daily_hours[start] += hours
            if rotation:
                daily_rotations[start].add(rotation)

    worst_days_off = 28
    worst_window = ""
    worst_rotations = set()
    maximum_average = 0.0
    maximum_window = ""
    maximum_rotations = set()
    start_day = first_day
    while start_day <= last_day:
        end_day = start_day + timedelta(days=27)
        window = [start_day + timedelta(days=offset) for offset in range(28)]
        days_off = sum(day in non_work_days or day not in worked_days for day in window)
        rolling_average = sum(daily_hours[day] for day in window) / 4.0
        if days_off < worst_days_off:
            worst_days_off = days_off
            worst_window = f"{start_day.isoformat()} to {end_day.isoformat()}"
            worst_rotations = set().union(*(daily_rotations[day] for day in window))
        if rolling_average > maximum_average:
            maximum_average = rolling_average
            maximum_window = f"{start_day.isoformat()} to {end_day.isoformat()}"
            maximum_rotations = set().union(*(daily_rotations[day] for day in window))
        start_day += timedelta(days=1)

    return {
        "dh_last_log": max(shift[0] for shift in shifts).isoformat(),
        "dh_min_days_off": worst_days_off,
        "dayoff_window": worst_window,
        "dayoff_rotations": "; ".join(sorted(worst_rotations)),
        "eightyhr_max": round(maximum_average, 1),
        "eightyhr_window": maximum_window,
        "eightyhr_rotations": "; ".join(sorted(maximum_rotations)),
    }
