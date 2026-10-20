"""Dated annual progress, independent of individual CCC review forms.

Annual entries remain in the encrypted dashboard state. Each update preserves
the preceding values so reports can select the values recorded through their
cutoff. A finalized review supplies its own frozen annual snapshot instead.
"""

from __future__ import annotations

import copy
import math
import re
from datetime import date
from typing import Any

from ccc import model as m


NUMERIC_FIELDS = {
    "pediatrics_ite_score": "Pediatrics ITE score",
    "pediatrics_ite_expected_pass_rate": "Pediatrics expected pass rate (%)",
    "medicine_ite_score": "Medicine ITE score",
    "medicine_ite_percentile": "Medicine ITE percentile",
    "adult_clinic_count": "Adult clinic patient tally",
    "pediatric_clinic_count": "Pediatric clinic patient tally",
}
ENCOUNTER_FIELDS = {f"observed_encounter_{n}": f"Observed encounter {n}" for n in range(1, 5)}
FIELD_LABELS = {**NUMERIC_FIELDS, **ENCOUNTER_FIELDS}
PERCENT_FIELDS = frozenset({"pediatrics_ite_expected_pass_rate", "medicine_ite_percentile"})
COUNT_FIELDS = frozenset({"adult_clinic_count", "pediatric_clinic_count"})
_IDENTITY_FIELDS = ("resident_id", "pgy", "academic_year", "period_start", "period_end")


def _key(item: dict) -> tuple:
    return tuple(item.get(key) for key in _IDENTITY_FIELDS)


def _numeric(key: str, value: Any, *, stored: bool = False) -> int | float | None:
    label = FIELD_LABELS[key]
    if value is None or (not stored and isinstance(value, str) and not value.strip()):
        return None
    if type(value) is bool or not isinstance(value, (str, int, float)):
        raise m.ValidationError(f"{label} must be a number, or blank if unavailable.")
    if stored and type(value) not in (int, float):
        raise m.ValidationError(f"The saved {label.lower()} is invalid.")
    if isinstance(value, str) and len(value) > 64:
        raise m.ValidationError(f"{label} is too long. Enter only the recorded number.")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise m.ValidationError(f"{label} must be a number, or blank if unavailable.") from None
    if not math.isfinite(number) or number < 0:
        raise m.ValidationError(f"{label} must be a finite, nonnegative number.")
    if key in PERCENT_FIELDS and number > 100:
        raise m.ValidationError(f"{label} must be between 0 and 100.")
    if key in COUNT_FIELDS:
        if not number.is_integer() or (stored and type(value) is not int):
            raise m.ValidationError(f"{label} must be a whole number, or blank if unavailable.")
        # Above this limit floating-point conversion cannot preserve all integer
        # inputs, and no useful yearly patient tally is remotely this large.
        if number > 2**53 - 1:
            raise m.ValidationError(f"{label} exceeds the supported whole-number range.")
        return int(number)
    return int(number) if number.is_integer() and number <= 2**53 - 1 else number


def _fields(values: Any, *, stored: bool = False) -> dict:
    if not isinstance(values, dict) or any(key not in FIELD_LABELS for key in values):
        raise m.ValidationError("The annual progress fields are unsupported. No data were changed.")
    result = {}
    for key in FIELD_LABELS:
        value = values.get(key)
        if key in ENCOUNTER_FIELDS:
            if value is not None and type(value) is not bool:
                raise m.ValidationError("Choose Not recorded, Not completed, or Completed for each observed encounter.")
            result[key] = value
        else:
            result[key] = _numeric(key, value, stored=stored)
    return result


def _period(item: dict, *, stored: bool = False) -> dict:
    pgy = item.get("pgy")
    if type(pgy) is not int or pgy not in range(1, 5):
        raise m.ValidationError("Choose PGY1 through PGY4 for annual progress.")
    result = {"pgy": pgy}
    for key in ("period_start", "period_end", "as_of"):
        value = item.get(key)
        if stored and (not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise m.ValidationError("The annual progress dates are invalid.")
        result[key] = m.iso_date(value, key.replace("_", " ").capitalize())
    if not result["period_start"] <= result["as_of"] <= result["period_end"]:
        raise m.ValidationError("The annual progress date must fall within its training-year dates.")
    academic_year = item.get("academic_year") if stored else item.get("academic_year", m.academic_year(result["period_start"]))
    if (not isinstance(academic_year, str) or not re.fullmatch(r"\d{4}-\d{4}", academic_year)
            or int(academic_year[5:]) != int(academic_year[:4]) + 1):
        raise m.ValidationError("Enter the annual progress academic year as consecutive years, such as 2026-2027.")
    result["academic_year"] = academic_year
    return result


def default_progress(resident: dict, pgy: int, as_of: Any = None) -> dict:
    """Return a blank training year with a valid default progress cutoff."""
    if type(pgy) is not int or pgy not in range(1, 5):
        raise m.ValidationError("Choose PGY1 through PGY4 for annual progress.")
    period = m.checkpoint_defaults(resident, pgy, "Year end")
    cutoff = m.parse_date(as_of) if as_of is not None else date.today()
    if cutoff is None:
        raise m.ValidationError("Choose a valid annual progress date.")
    low, high = m.parse_date(period["period_start"]), m.parse_date(period["period_end"])
    cutoff = max(low, min(high, cutoff))
    return {"pgy": pgy, "academic_year": period["academic_year"],
            "period_start": period["period_start"], "period_end": period["period_end"],
            "as_of": cutoff.isoformat(), "fields": {key: None for key in FIELD_LABELS}}


def _validate_entry(entry: Any, resident_id: str, *, versions: bool = True) -> None:
    if (not isinstance(entry, dict) or entry.get("resident_id") != resident_id
            or not isinstance(entry.get("id"), str) or not entry["id"]):
        raise m.ValidationError("Some annual progress has no matching resident or valid identifier.")
    _period(entry, stored=True)
    _fields(entry.get("fields"), stored=True)
    if not versions and "versions" in entry:
        raise m.ValidationError("An annual progress snapshot contains unsupported nested versions.")
    prior = entry.get("versions", [])
    if not isinstance(prior, list):
        raise m.ValidationError("The annual progress value history is invalid.")
    if versions:
        for saved in prior:
            _validate_entry(saved, resident_id, versions=False)
            if saved["id"] != entry["id"]:
                raise m.ValidationError("An annual progress version belongs to another entry.")


def _validate_snapshot(review: dict, resident_id: str) -> None:
    if "annual_progress_snapshot" in review:
        snapshot = review["annual_progress_snapshot"]
        if not isinstance(snapshot, list):
            raise m.ValidationError("A review has an invalid annual progress snapshot.")
        ids, identities = set(), set()
        cutoff = m.iso_date(review.get("period_end"), "Review evidence cutoff")
        for entry in snapshot:
            _validate_entry(entry, resident_id, versions=False)
            if entry["id"] in ids or _key(entry) in identities or entry["as_of"] > cutoff:
                raise m.ValidationError("A review contains duplicate or future annual progress.")
            ids.add(entry["id"])
            identities.add(_key(entry))
    # Reopened reviews retain their finalized snapshots in prior review versions.
    for prior in review.get("versions", []) if isinstance(review.get("versions", []), list) else []:
        if isinstance(prior, dict) and "annual_progress_snapshot" in prior:
            if prior.get("resident_id") != resident_id:
                raise m.ValidationError("A previous review contains another resident's annual progress.")
            _validate_snapshot(prior, resident_id)


def validate_state_progress(state: dict) -> None:
    """Validate the optional v1 extension, including frozen review snapshots."""
    entries = state.get("annual_progress", [])
    if not isinstance(entries, list):
        raise m.ValidationError("The annual progress archive is incomplete.")
    resident_ids = {r["id"] for r in state["residents"]}
    ids, identities = set(), set()
    for entry in entries:
        resident_id = entry.get("resident_id") if isinstance(entry, dict) else None
        if resident_id not in resident_ids:
            raise m.ValidationError("Some annual progress has no matching resident.")
        _validate_entry(entry, resident_id)
        if entry["id"] in ids or _key(entry) in identities:
            raise m.ValidationError("The annual progress archive contains duplicate entries.")
        ids.add(entry["id"])
        identities.add(_key(entry))
    for review in state["reviews"]:
        _validate_snapshot(review, review["resident_id"])


def save_progress(state: dict, resident_id: str, data: dict, actor: str) -> dict:
    """Save or update a dated annual entry without changing any CCC review.

    An existing ID edits that entry. Without an ID, an exact training-year
    identity updates its existing entry; other years create separate entries.
    Omitted fields retain their saved values; an explicit blank clears a value.
    """
    m.resident_by_id(state, resident_id)
    if not isinstance(data, dict):
        raise m.ValidationError("Enter annual progress using the yearly tracking form.")
    validate_state_progress(state)
    result = copy.deepcopy(state)
    entries = result.setdefault("annual_progress", [])
    identifier = data.get("id")
    if identifier is not None and (not isinstance(identifier, str) or not identifier):
        raise m.ValidationError("Choose an existing annual progress entry or add a new training year.")
    old = next((entry for entry in entries if entry["id"] == identifier), None) if identifier else None
    if old and old["resident_id"] != resident_id:
        raise m.ValidationError("Annual progress cannot be moved to another resident.")
    item = {"resident_id": resident_id, **_period(data)}
    same_year = next((entry for entry in entries if _key(entry) == _key(item)), None)
    if same_year and identifier and same_year["id"] != identifier:
        raise m.ValidationError("This training year already has an annual progress entry. Open that entry to update it.")
    if old is None and same_year is not None:
        old = same_year
    supplied = data.get("fields", {})
    if not isinstance(supplied, dict):
        raise m.ValidationError("The annual progress fields are unsupported. No data were changed.")
    item["fields"] = _fields({**(old["fields"] if old else {}), **supplied})
    stamp = m.now_iso()
    item.update({"id": old["id"] if old else identifier or m.new_id(),
                 "created_at": old.get("created_at", stamp) if old else stamp,
                 "created_by": old.get("created_by", actor) if old else actor,
                 "updated_at": stamp, "updated_by": actor,
                 "versions": copy.deepcopy(old.get("versions", [])) if old else []})
    if old:
        # The former record is a complete dated value set, not a shallow view
        # into the current entry. Nested copies of the history are unnecessary.
        item["versions"].append({key: copy.deepcopy(value) for key, value in old.items() if key != "versions"})
        entries[entries.index(old)] = item
    else:
        entries.append(item)
    result = m.with_audit(result, actor, "Annual progress saved", resident_id=resident_id,
                          annual_progress_id=item["id"], pgy=item["pgy"], academic_year=item["academic_year"],
                          as_of=item["as_of"])
    return m.validate_state(result)


def progress_for(state: dict, resident_id: str, as_of: Any, *, review: dict | None = None) -> list[dict]:
    """Return each year's last value set dated on or before the report cutoff.

    Final reviews use only their own frozen snapshot, including an empty
    snapshot. Older final reviews with no annual snapshot return no annual
    rows; their legacy review fields remain available to the report renderer.
    """
    m.resident_by_id(state, resident_id)
    cutoff = m.iso_date(as_of, "Report evidence cutoff")
    if review is not None and review.get("resident_id") != resident_id:
        raise m.ValidationError("The selected review belongs to another resident.")
    if review is not None and review.get("status") == "Final":
        _validate_snapshot(review, resident_id)
        rows = copy.deepcopy(review.get("annual_progress_snapshot", []))
        return sorted([row for row in rows if row["as_of"] <= cutoff],
                      key=lambda row: (row["period_start"], row["period_end"], row["pgy"], row["academic_year"]))
    validate_state_progress(state)
    rows = []
    for entry in state.get("annual_progress", []):
        if entry["resident_id"] != resident_id:
            continue
        candidates = [*entry.get("versions", []), entry]
        eligible = [(index, candidate) for index, candidate in enumerate(candidates) if candidate["as_of"] <= cutoff]
        if not eligible:
            continue
        _, selected = max(eligible, key=lambda pair: (pair[1]["as_of"], pair[0]))
        rows.append({key: copy.deepcopy(value) for key, value in selected.items() if key != "versions"})
    return sorted(rows, key=lambda row: (row["period_start"], row["period_end"], row["pgy"], row["academic_year"]))
