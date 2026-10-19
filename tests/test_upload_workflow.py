"""Single-file and incremental uploads using fictional names and memory only."""

from __future__ import annotations

import copy
import hashlib

import pytest

from ccc import model as m
from ccc.upload_workflow import prepare_upload_batch


@pytest.fixture
def roster():
    return m.save_resident(m.new_state("Fictional upload program"),
                           {"name": "Alex Example", "start_date": "2026-07-01"}, "admin")


def preview(label, *, kind="evaluations", name="Alex Example", text="A fictional observation."):
    return {
        "source": {"id": f"source-{label}", "name": f"{label}.csv", "sha256": hashlib.sha256(label.encode()).hexdigest(),
                   "kind": kind, "specialty": "Medicine", "period_start": "2026-07-01", "period_end": "2026-12-31"},
        "records": [{"resident_name": name, "category": kind, "specialty": "Medicine", "date": "2026-10-01",
                     "record_mode": "event", "form": "Fictional report", "metric": "Fictional observation",
                     "value": 1, "unit": "count", "text": text, "questions": []}],
        "warnings": ["Fictional source warning retained for review."],
        "errors": [],
    }


def saved_state(roster):
    return prepare_upload_batch(roster, [preview("original")], {}, "admin").candidate


def test_single_file_needs_no_other_categories_and_keeps_inputs_unchanged(roster):
    included = [preview("single")]
    before_state, before_previews = copy.deepcopy(roster), copy.deepcopy(included)
    result = prepare_upload_batch(roster, included, {}, "admin")
    assert result.saved_source_ids == ["source-single"]
    assert result.skipped_source_ids == []
    assert result.stats == {"new_records": 1, "duplicates": 0, "possible_updates": 0, "explicitly_skipped": 0}
    assert len(result.candidate["records"]) == len(result.candidate["sources"]) == 1
    record = result.candidate["records"][0]
    assert record["resident_id"] == roster["residents"][0]["id"]
    assert not record.get("external_id") and not record.get("external_record_id")
    assert roster == before_state and included == before_previews
    result.candidate["sources"][0]["warnings"].append("Candidate-only edit.")
    assert included == before_previews
    m.validate_state(result.candidate)


def test_later_single_category_upload_preserves_saved_history(roster):
    first = saved_state(roster)
    before = copy.deepcopy(first)
    result = prepare_upload_batch(first, [preview("procedures", kind="procedures")], {}, "admin")
    assert result.saved_source_ids == ["source-procedures"]
    assert result.stats["new_records"] == 1
    assert result.candidate["records"][:1] == before["records"]
    assert result.candidate["sources"][:1] == before["sources"]
    assert {record["category"] for record in result.candidate["records"]} == {"evaluations", "procedures"}
    assert first == before
    m.validate_state(result.candidate)


@pytest.mark.parametrize("duplicate_first", [True, False])
def test_duplicate_only_reexport_does_not_block_a_new_file(roster, duplicate_first):
    first = saved_state(roster)
    duplicate, new = preview("reexport"), preview("procedures", kind="procedures")
    included = [duplicate, new] if duplicate_first else [new, duplicate]
    result = prepare_upload_batch(first, included, {}, "admin")
    assert result.saved_source_ids == ["source-procedures"]
    assert result.skipped_source_ids == ["source-reexport"]
    assert result.stats == {"new_records": 1, "duplicates": 1, "possible_updates": 0, "explicitly_skipped": 0}
    assert len(result.candidate["records"]) == len(result.candidate["sources"]) == 2
    assert all(source["id"] != "source-reexport" for source in result.candidate["sources"])


def test_all_duplicate_or_explicitly_skipped_files_leave_state_unchanged(roster):
    first = saved_state(roster)
    before = copy.deepcopy(first)
    skipped = preview("skipped", name="Person outside this roster")
    mappings = {m.mapping_key(skipped["records"][0]): "__skip__"}
    result = prepare_upload_batch(first, [preview("reexport"), skipped], mappings, "admin")
    assert result.saved_source_ids == []
    assert result.skipped_source_ids == ["source-reexport", "source-skipped"]
    assert result.stats == {"new_records": 0, "duplicates": 1, "possible_updates": 0, "explicitly_skipped": 1}
    assert result.candidate == first == before


def test_repeated_files_in_a_new_batch_are_checked_incrementally(roster):
    result = prepare_upload_batch(roster, [preview("first"), preview("same-evidence")], {}, "admin")
    assert result.saved_source_ids == ["source-first"]
    assert result.skipped_source_ids == ["source-same-evidence"]
    assert result.stats["new_records"] == result.stats["duplicates"] == 1
    assert len(result.candidate["records"]) == 1


def test_same_file_can_complete_a_previously_skipped_resident_then_becomes_noop(roster):
    original = preview("multi-resident")
    original["records"].append({**original["records"][0], "resident_name": "Brooke Sample"})
    original_before = copy.deepcopy(original)
    skip_brooke = {m.mapping_key(original["records"][1]): "__skip__"}
    first = prepare_upload_batch(roster, [original], skip_brooke, "admin")
    assert first.stats["new_records"] == first.stats["explicitly_skipped"] == 1
    assert len(first.candidate["records"]) == 1
    alex_record = copy.deepcopy(first.candidate["records"][0])
    alex_source = copy.deepcopy(first.candidate["sources"][0])

    with_brooke = m.save_resident(first.candidate, {"name": "Brooke Sample", "start_date": "2026-07-01"}, "admin")
    before_completion = copy.deepcopy(with_brooke)
    reupload = copy.deepcopy(original)
    # The file bytes, filename, category, specialty, and reporting period stay
    # the same; processing the later upload creates a fresh preview source ID.
    reupload["source"]["id"] = "source-multi-resident-completion"
    completed = prepare_upload_batch(with_brooke, [reupload], {}, "admin")
    assert completed.saved_source_ids == ["source-multi-resident-completion"]
    assert completed.skipped_source_ids == []
    assert completed.stats == {"new_records": 1, "duplicates": 1, "possible_updates": 0, "explicitly_skipped": 0}
    assert completed.candidate["records"][0] == alex_record
    assert completed.candidate["sources"][0] == alex_source
    assert len(completed.candidate["records"]) == len(completed.candidate["sources"]) == 2
    brooke_record = completed.candidate["records"][-1]
    assert brooke_record["resident_id"] == m.match_resident(completed.candidate, "Brooke Sample")
    assert alex_record["source_id"] == "source-multi-resident"
    assert brooke_record["source_id"] == "source-multi-resident-completion"
    assert {source["sha256"] for source in completed.candidate["sources"]} == {original["source"]["sha256"]}
    assert {source["name"] for source in completed.candidate["sources"]} == {original["source"]["name"]}
    assert all(source["active"] and source["record_count"] == 1 for source in completed.candidate["sources"])
    assert with_brooke == before_completion and original == original_before

    repeated_preview = copy.deepcopy(reupload)
    repeated_preview["source"]["id"] = "source-multi-resident-repeat"
    repeated = prepare_upload_batch(completed.candidate, [repeated_preview], {}, "admin")
    assert repeated.saved_source_ids == []
    assert repeated.skipped_source_ids == ["source-multi-resident-repeat"]
    assert repeated.stats["new_records"] == 0 and repeated.stats["duplicates"] == 2
    assert repeated.candidate == completed.candidate
    m.validate_state(completed.candidate)


def test_two_new_files_detect_changed_content_in_the_sequential_dry_run(roster):
    included = [preview("first-new"), preview("second-new", text="A changed fictional assessment.")]
    before_state, before_previews = copy.deepcopy(roster), copy.deepcopy(included)
    with pytest.raises(m.ValidationError, match="revise saved evidence"):
        prepare_upload_batch(roster, included, {}, "admin")

    dry_run = prepare_upload_batch(roster, included, {}, "admin", allow_possible_updates=True)
    assert dry_run.saved_source_ids == ["source-first-new", "source-second-new"]
    assert dry_run.stats == {"new_records": 2, "duplicates": 0, "possible_updates": 1, "explicitly_skipped": 0}
    assert dry_run.candidate["records"][-1]["possible_revision"] is True
    assert roster == before_state and included == before_previews


@pytest.mark.parametrize("problem,error", [
    ("parser", "parsing errors"),
    ("unresolved", "Match every"),
    ("revision", "revise saved evidence"),
])
def test_failed_later_file_leaves_original_state_and_previews_intact(roster, problem, error):
    first = saved_state(roster)
    bad = preview("bad", text="A changed fictional assessment.")
    if problem == "parser":
        bad["errors"] = ["A fictional malformed row; only part of the file could be parsed."]
    elif problem == "unresolved":
        bad["records"][0]["resident_name"] = "Unmatched fictional resident"
    included = [preview("new-procedures", kind="procedures"), bad]
    before_state, before_previews = copy.deepcopy(first), copy.deepcopy(included)
    with pytest.raises(m.ValidationError, match=error):
        prepare_upload_batch(first, included, {}, "admin")
    assert first == before_state and included == before_previews


def test_explicit_confirmation_preserves_revision_behavior(roster):
    first = saved_state(roster)
    changed = preview("revision", text="A changed fictional assessment.")
    result = prepare_upload_batch(first, [changed], {}, "admin", allow_possible_updates=True)
    assert result.saved_source_ids == ["source-revision"]
    assert result.stats["new_records"] == result.stats["possible_updates"] == 1
    assert len(m.active_records(result.candidate)) == 2
    assert result.candidate["records"][-1]["possible_revision"] is True


def test_complete_replacement_is_imported_even_if_evidence_matches_old_upload(roster):
    first = saved_state(roster)
    result = prepare_upload_batch(first, [preview("replacement")], {}, "admin",
                                  replacements={"source-replacement": "source-original"})
    assert result.saved_source_ids == ["source-replacement"]
    assert result.skipped_source_ids == []
    assert result.candidate["sources"][0]["active"] is False
    assert result.candidate["sources"][-1]["replaces"] == "source-original"
    assert len(m.active_records(result.candidate)) == 1
    assert first["sources"][0]["active"] is True
    m.validate_state(result.candidate)


def test_replacement_cannot_silently_skip_part_of_the_new_file(roster):
    first = saved_state(roster)
    before = copy.deepcopy(first)
    replacement = preview("replacement")
    skipped_row = {**replacement["records"][0], "resident_name": "Person outside this roster"}
    replacement["records"].append(skipped_row)
    mappings = {m.mapping_key(skipped_row): "__skip__"}
    with pytest.raises(m.ValidationError, match="complete replacement cannot skip"):
        prepare_upload_batch(first, [replacement], mappings, "admin",
                             replacements={"source-replacement": "source-original"})
    assert first == before and first["sources"][0]["active"] is True
