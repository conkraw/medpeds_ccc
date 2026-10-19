"""Prepare a complete upload batch without writing files or persistent state."""

from __future__ import annotations

import copy
from dataclasses import dataclass

from ccc import model as m


@dataclass
class UploadBatchResult:
    candidate: dict
    saved_source_ids: list[str]
    skipped_source_ids: list[str]
    stats: dict[str, int]


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
        else:
            prepared, stats = m.prepare_records(candidate, records, mappings)
            if not prepared:
                skipped_source_ids.append(source_id)
                totals["duplicates"] += stats["duplicates"]
                totals["explicitly_skipped"] += explicitly_skipped
                continue

        candidate, stats = m.import_records(
            candidate, records, source, mappings, actor,
            replace_source_id=replace_id,
            allow_possible_updates=allow_possible_updates,
            allow_partial_reimport=replace_id is None,
        )
        saved_source_ids.append(source_id)
        for key in ("new_records", "duplicates", "possible_updates"):
            totals[key] += stats[key]
        totals["explicitly_skipped"] += explicitly_skipped

    return UploadBatchResult(candidate, saved_source_ids, skipped_source_ids, totals)
