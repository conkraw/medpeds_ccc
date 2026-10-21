"""Prepare a complete upload batch without writing files or persistent state."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from ccc import model as m


@dataclass
class UploadBatchResult:
    candidate: dict
    saved_source_ids: list[str]
    skipped_source_ids: list[str]
    stats: dict[str, int]


def evaluation_import_status(state: dict) -> dict[str, dict]:
    """Describe persisted evaluation history, without treating it as coverage.

    Dates come only from active saved evidence. The last update is a successful
    upload that still contributes evidence, not a preview, failed save, review
    edit, or duplicate-only check. No archive migration is needed.
    """
    statuses = {specialty: {
        "specialty": specialty, "last_updated_at": "", "latest_completed_date": "",
        "latest_rotation_end_date": "", "earliest_evaluation_date": "", "latest_evaluation_date": "",
        "evaluation_count": 0, "resident_count": 0, "upload_count": 0,
    } for specialty in ("Medicine", "Pediatrics")}
    sources = {source["id"]: source for source in state.get("sources", []) if source.get("active", True)}
    dates = {specialty: [] for specialty in statuses}
    residents = {specialty: set() for specialty in statuses}
    uploads = {specialty: set() for specialty in statuses}
    saved_times: dict[str, datetime] = {}

    def valid_time(value: object) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    for record in state.get("records", []):
        source = sources.get(record.get("source_id"))
        if source is None or not record.get("active", True) or record.get("category") != "evaluations":
            continue
        specialty = record.get("specialty")
        if specialty not in statuses:
            specialty = source.get("specialty")
        if specialty not in statuses:
            rotation = str(record.get("rotation") or "").strip()
            if not rotation:
                continue
            specialty = "Pediatrics" if re.search(r"peds|pa?ediatr", rotation, re.I) else "Medicine"
        status = statuses[specialty]
        status["evaluation_count"] += 1
        if record.get("resident_id"):
            residents[specialty].add(record["resident_id"])
        uploads[specialty].add(source["id"])
        saved_at = source.get("uploaded_at") or record.get("imported_at")
        parsed_time = valid_time(saved_at)
        if parsed_time is not None and (specialty not in saved_times or parsed_time > saved_times[specialty]):
            saved_times[specialty] = parsed_time
            status["last_updated_at"] = str(saved_at)
        if record.get("date_is_fallback") or record.get("date_source") == "selected reporting period end":
            continue
        actual = m.parse_date(record.get("date"))
        if actual:
            dates[specialty].append(actual.isoformat())
        completed = m.source_export_date(record.get("completed_date"))
        if not completed and record.get("date_source") == "completion date" and actual:
            completed = actual.isoformat()
        if not completed:
            raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
            for key, value in raw.items():
                if re.sub(r"[^a-z0-9]", "", str(key).casefold()) in {"datecompleted", "completeddate", "completiondate"}:
                    completed = m.source_export_date(value)
                    if completed:
                        break
        status["latest_completed_date"] = max(status["latest_completed_date"], completed)
        _, period_end = m.evaluation_source_period(record)
        status["latest_rotation_end_date"] = max(status["latest_rotation_end_date"], period_end)
    for specialty, status in statuses.items():
        if dates[specialty]:
            status["earliest_evaluation_date"] = min(dates[specialty])
            status["latest_evaluation_date"] = max(dates[specialty])
        status["resident_count"] = len(residents[specialty])
        status["upload_count"] = len(uploads[specialty])
    return statuses


def prepare_upload_batch(
    state: dict,
    included: list[dict],
    mappings: dict[str, str],
    actor: str,
    *,
    replacements: dict[str, str | None] | None = None,
    allow_possible_updates: bool = False,
) -> UploadBatchResult:
    """Return an independent candidate for only the files with new evidence.

    Each preview supplies ``source``, ``records``, and optional ``warnings`` and
    ``errors``. Source IDs from the preview are retained. An add-as-new file with
    only saved duplicates or explicitly skipped residents creates no source.
    Later files are checked against the candidate including earlier new files.
    Re-uploading the same file can complete previously skipped resident entries;
    only new evidence receives a new source record.

    A selected replacement follows the model's replacement path, even when its
    evidence matches the old upload. It cannot skip resident rows. Parser errors,
    unresolved residents, and unconfirmed revisions abort the batch. The caller
    receives no partial candidate on error, and none of the inputs are changed.
    """
    candidate = copy.deepcopy(state)
    saved_source_ids: list[str] = []
    skipped_source_ids: list[str] = []
    totals = {"new_records": 0, "duplicates": 0, "possible_updates": 0, "explicitly_skipped": 0}
    replacements = replacements or {}

    for preview in included:
        if preview.get("errors"):
            raise m.ValidationError("An included file has parsing errors. Remove it from the batch or correct it before saving.")
        records = preview.get("records", [])
        if not records:
            raise m.ValidationError("An included file has no parsed records. Process the file again before saving.")
        source = copy.deepcopy(preview["source"])
        if not source.get("id"):
            source["id"] = m.new_id()
        source_id = source["id"]
        source["warnings"] = copy.deepcopy(preview.get("warnings", source.get("warnings", [])))
        replace_id = replacements.get(source_id)
        if replace_id in (None, "", "__none__"):
            replace_id = None
        explicitly_skipped = sum(mappings.get(m.mapping_key(record)) == "__skip__" for record in records)

        if replace_id:
            if explicitly_skipped:
                raise m.ValidationError("A complete replacement cannot skip resident entries. Match every resident or add this file as a new upload.")
            candidate, stats = m.import_records(
                candidate, records, source, mappings, actor,
                replace_source_id=replace_id,
                allow_possible_updates=allow_possible_updates,
            )
        else:
            prepared, stats = m.prepare_records(candidate, records, mappings)
            if not prepared:
                skipped_source_ids.append(source_id)
                totals["duplicates"] += stats["duplicates"]
                totals["explicitly_skipped"] += explicitly_skipped
                continue
            # This batch already owns a deep copy. Reuse preparation without
            # copying and scanning the entire growing history a second time.
            candidate, stats = m._import_records_into_candidate(
                candidate, records, source, mappings, actor,
                allow_possible_updates=allow_possible_updates,
                allow_partial_reimport=True,
                prepared_result=(prepared, stats),
            )
        saved_source_ids.append(source_id)
        for key in ("new_records", "duplicates", "possible_updates"):
            totals[key] += stats[key]
        totals["explicitly_skipped"] += explicitly_skipped

    return UploadBatchResult(candidate, saved_source_ids, skipped_source_ids, totals)
