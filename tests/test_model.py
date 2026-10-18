"""Resident identity, review history, and reporting-period acceptance checks.

All people and events are synthetic. These checks exercise the domain model;
GitHub compare-and-swap protection is covered separately in storage tests.
"""

from __future__ import annotations

import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from ccc import model as m


@pytest.fixture
def roster():
    state = m.new_state("Synthetic Med-Peds Program")
    state = m.save_resident(state, {"id": "alex", "name": "Alex Example", "external_id": "NI-101",
                                     "start_date": "2026-07-01", "aliases": ["Example, Alex"]}, "admin")
    state = m.save_resident(state, {"id": "brooke", "name": "Brooke Sample", "external_id": "NI-202",
                                     "start_date": "2026-07-01", "aliases": ["B. Sample"]}, "admin")
    return state


@pytest.fixture
def name_only_roster():
    state = m.new_state("Synthetic name-only program")
    for resident in [
        {"name": "Alex Example", "start_date": "2026-07-01"},
        {"name": "Brooke Sample", "start_date": "2026-07-01", "aliases": ["B. Sample"]},
    ]:
        state = m.save_resident(state, resident, "admin")
    return state


def event(*, name="Alex Example", external_id="NI-101", day="2026-10-01",
          native_id="eval-001", text="A synthetic observation.", **extra):
    rec = {"resident_name": name, "external_id": external_id, "date": day,
           "category": "evaluations", "specialty": "Medicine", "record_mode": "event",
           "form": "Synthetic faculty evaluation", "evaluator": "Example Faculty", "rotation": "Ward",
           "text": text, "value": 3, "unit": "rating", "questions": []}
    if native_id is not None:
        rec["external_record_id"] = native_id
    return {**rec, **extra}


def source(label="original", *, kind="evaluations", specialty="Medicine", **extra):
    return {"id": m.new_id(), "name": f"synthetic-{label}.csv",
            "sha256": hashlib.sha256(label.encode()).hexdigest(), "kind": kind,
            "specialty": specialty, "period_start": "2026-07-01", "period_end": "2026-12-31", **extra}


def import_events(state, records, *, label="original", **kwargs):
    return m.import_records(state, records, source(label, kind=records[0]["category"],
                                                  specialty=records[0]["specialty"]), {}, "admin", **kwargs)[0]


def review_data(**extra):
    return {"pgy": 1, "half": "Midyear", "academic_year": "2026-2027",
            "period_start": "2026-07-01", "period_end": "2026-12-31", "review_date": "2027-01-05",
            "fields": {"ccc_decision": "Synthetic recorded CCC decision", "action_plan": "Practice a documented learning goal"},
            **extra}


def test_name_only_roster_matches_exact_normalized_names_and_aliases(name_only_roster):
    state = name_only_roster
    alex, brooke = state["residents"]
    assert alex["id"] and brooke["id"] and alex["id"] != brooke["id"]
    assert not any(resident.get("external_id") for resident in state["residents"])
    for name in ("Alex Example", "  EXAMPLE, Alex  ", "Exámple, Alex, MD"):
        assert m.match_resident(state, name) == alex["id"]
    assert m.match_resident(state, "Brooke Sample") == brooke["id"]
    assert m.match_resident(state, "B. Sample") == brooke["id"]
    assert m.validate_state(state) == state


def test_same_name_without_source_ids_requires_an_explicit_resident_choice(name_only_roster):
    state = m.save_resident(name_only_roster, {"name": "Alex Example", "start_date": "2025-07-01"}, "admin")
    chosen_id = state["residents"][-1]["id"]
    record = event(external_id="", native_id=None)
    assert m.match_resident(state, "Example, Alex") is None
    with pytest.raises(m.ValidationError, match="Match every"):
        m.prepare_records(state, [record], {})
    prepared, stats = m.prepare_records(state, [record], {m.mapping_key(record): chosen_id})
    assert prepared[0]["resident_id"] == chosen_id
    assert not prepared[0].get("external_id")
    assert not prepared[0].get("external_record_id")
    assert stats["new_records"] == 1


def test_name_edit_keeps_internal_identity_evidence_and_final_review(name_only_roster):
    resident_id = m.match_resident(name_only_roster, "Alex Example")
    original_event = event(external_id="", native_id=None)
    saved = import_events(name_only_roster, [original_event], label="before-name-edit")
    finalized = m.save_review(saved, resident_id, review_data(), "reviewer", finalize=True)
    original_records = copy.deepcopy(finalized["records"])
    original_review = copy.deepcopy(finalized["reviews"][0])
    resident = m.resident_by_id(finalized, resident_id)
    renamed = m.save_resident(finalized, {**resident, "name": "Alex Sample-Example",
                                        "aliases": [*resident["aliases"], resident["name"]]}, "admin")
    assert len(renamed["residents"]) == len(finalized["residents"])
    assert m.resident_by_id(renamed, resident_id)["name"] == "Alex Sample-Example"
    assert renamed["records"] == original_records
    assert renamed["reviews"][0] == original_review
    assert original_review["snapshot_resident"]["name"] == "Alex Example"
    assert m.match_resident(renamed, "Sample-Example, Alex") == resident_id
    assert m.match_resident(renamed, "Example, Alex") == resident_id

    # Repeated exports still deduplicate after the roster name changes, and a
    # later name-only event joins the same history without a source event ID.
    prepared, stats = m.prepare_records(renamed, [original_event], {})
    assert prepared == [] and stats["duplicates"] == 1
    updated = import_events(renamed, [event(name="Alex Sample-Example", external_id="", native_id=None,
                                           day="2026-11-01")], label="after-name-edit")
    history = m.records_for(updated, resident_id, "2026-07-01", "2026-12-31")
    assert len(history) == 2
    assert {record["resident_id"] for record in history} == {resident_id}
    assert updated["reviews"][0] == original_review
    assert m.validate_state(updated) == updated


def test_same_names_require_identifier_or_explicit_resolution(roster):
    state = m.save_resident(roster, {"id": "other-alex", "name": "Alex Example", "external_id": "NI-303",
                                     "start_date": "2026-07-01", "aliases": []}, "admin")
    assert m.match_resident(state, "Alex Example") is None
    assert m.match_resident(state, "Alex Example", "NI-101") == "alex"
    assert m.match_resident(state, "Alex Example", "NI-303") == "other-alex"


def test_surname_order_accents_and_credentials_match_without_sorting_tokens(roster):
    assert m.match_resident(roster, "Exámple, Alex, MD") == "alex"
    assert m.match_resident(roster, "B. Sample") == "brooke"
    assert m.normalize_name("Yu, Alex") != m.normalize_name("Alex, Yu")
    assert m.normalize_name("Alex Yu") != m.normalize_name("Yu Alex")


def test_alias_collision_is_rejected_without_mutating_roster(roster):
    before = copy.deepcopy(roster)
    update = {**m.resident_by_id(roster, "brooke"), "aliases": ["Example, Alex"]}
    with pytest.raises(m.ValidationError, match="alias"):
        m.save_resident(roster, update, "admin")
    assert roster == before


def test_duplicate_external_id_is_rejected(roster):
    with pytest.raises(m.ValidationError, match="already assigned"):
        m.save_resident(roster, {"name": "Another Example", "external_id": "NI-101",
                                "start_date": "2026-07-01"}, "admin")


def test_conflicting_unknown_external_id_cannot_be_overridden_by_name(roster):
    assert m.match_resident(roster, "Alex Example", "NI-999") is None
    with pytest.raises(m.ValidationError, match="Match every"):
        m.prepare_records(roster, [event(external_id="NI-999")], {})


def test_known_external_id_is_the_primary_resident_identifier(roster):
    assert m.match_resident(roster, "A changed name in the export", "NI-202") == "brooke"


def test_explicit_name_mapping_resolves_an_unrecognized_variation(roster):
    record = event(name="Unrecognized Export Name", external_id="")
    result, stats = m.prepare_records(roster, [record], {m.mapping_key(record): "alex"})
    assert result[0]["resident_id"] == "alex"
    assert result[0]["resident_name"] == "Unrecognized Export Name"
    assert stats["new_records"] == 1


def test_skip_mapping_never_imports_evidence_to_another_resident(roster):
    skipped = event(name="Not in this program", external_id="NI-999")
    prepared, stats = m.prepare_records(roster, [skipped], {m.mapping_key(skipped): "__skip__"})
    assert prepared == [] and stats["new_records"] == 0


def test_training_year_and_four_year_boundaries_are_inclusive(roster):
    resident = m.resident_by_id(roster, "alex")
    assert m.current_pgy(resident, "2026-06-30") == 0
    assert m.current_pgy(resident, "2026-07-01") == 1
    assert m.current_pgy(resident, "2027-06-30") == 1
    assert m.current_pgy(resident, "2027-07-01") == 2
    assert m.current_pgy(resident, "2030-06-30") == 4
    assert m.current_pgy(resident, "2030-07-01") == 5
    midyear = m.checkpoint_defaults(resident, 1, "Midyear")
    year_end = m.checkpoint_defaults(resident, 1, "Year end")
    graduation = m.checkpoint_defaults(resident, 4, "Year end")
    assert (midyear["period_start"], midyear["period_end"]) == ("2026-07-01", "2026-12-31")
    assert (year_end["period_start"], year_end["period_end"]) == ("2026-07-01", "2027-06-30")
    assert (graduation["period_start"], graduation["period_end"]) == ("2029-07-01", "2030-06-30")


def test_off_cycle_and_leap_year_periods_use_calendar_months(roster):
    resident = {**m.resident_by_id(roster, "alex"), "start_date": "2025-10-15"}
    checkpoint = m.checkpoint_defaults(resident, 1, "Midyear")
    assert checkpoint["period_end"] == "2026-04-14"
    assert m.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert m.add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert m.add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)


@pytest.mark.parametrize("day,included", [("2026-06-30", False), ("2026-07-01", True),
                                         ("2026-12-31", True), ("2027-01-01", False)])
def test_evidence_date_range_includes_both_endpoints(day, included):
    assert m.record_in_window(event(day=day), "2026-07-01", "2026-12-31") is included


def test_cumulative_summary_is_excluded_if_it_extends_before_selected_period(roster):
    snapshot = event(day="2026-12-31", native_id=None, category="conferences", record_mode="snapshot",
                     period_start="2026-07-01", period_end="2026-12-31", metric="Attendance", value=80, unit="percent")
    state = import_events(roster, [snapshot])
    assert m.records_for(state, "alex", "2026-10-01", "2026-12-31") == []
    assert len(m.coverage_exclusions(state, "alex", "2026-10-01", "2026-12-31")) == 1
    assert len(m.records_for(state, "alex", "2026-07-01", "2026-12-31")) == 1


def test_invalid_window_has_an_actionable_validation_error(roster):
    with pytest.raises(m.ValidationError):
        m.coverage_exclusions(roster, "alex", "not-a-date", "2026-12-31")


def test_reimported_file_is_rejected_before_any_mutation(roster):
    src = source()
    saved, _ = m.import_records(roster, [event()], src, {}, "admin")
    before = copy.deepcopy(saved)
    with pytest.raises(m.ValidationError, match="already saved"):
        m.import_records(saved, [event()], src, {}, "admin")
    assert saved == before


def test_overlapping_export_skips_saved_events_but_keeps_new_native_ids(roster):
    saved = import_events(roster, [event(native_id="event-1")])
    prepared, stats = m.prepare_records(saved, [event(native_id="event-1"), event(native_id="event-2")], {})
    assert len(prepared) == 1
    assert prepared[0]["external_record_id"] == "event-2"
    assert stats["duplicates"] == 1
    assert stats["possible_updates"] == 0


def test_no_native_id_preserves_two_genuinely_identical_new_events(roster):
    record = event(native_id=None, category="procedures", metric="Synthetic procedure", value=1, unit="count")
    prepared, stats = m.prepare_records(roster, [record, copy.deepcopy(record)], {})
    assert len(prepared) == 2 and stats["duplicates"] == 0
    saved = import_events(roster, [record])
    next_batch, stats = m.prepare_records(saved, [record, copy.deepcopy(record)], {})
    assert len(next_batch) == 1 and stats["duplicates"] == 1


def test_duplicate_native_id_inside_a_file_is_counted_once(roster):
    record = event()
    prepared, stats = m.prepare_records(roster, [record, copy.deepcopy(record)], {})
    assert len(prepared) == 1 and stats["duplicates"] == 1


def test_conflicting_native_event_id_inside_file_is_rejected(roster):
    with pytest.raises(m.ValidationError, match="conflicting"):
        m.prepare_records(roster, [event(), event(text="Changed assessment")], {})


def test_native_event_id_remains_stable_if_its_date_changes(roster):
    with pytest.raises(m.ValidationError, match="conflicting"):
        m.prepare_records(roster, [event(day="2026-10-01"), event(day="2026-10-02")], {})


def test_native_id_revision_from_later_file_is_flagged_even_after_date_edit(roster):
    saved = import_events(roster, [event(day="2026-10-01")])
    prepared, stats = m.prepare_records(saved, [event(day="2026-10-02", text="Corrected date and feedback")], {})
    assert len(prepared) == 1 and stats["possible_updates"] == 1
    assert prepared[0]["possible_revision"] is True


def test_revised_evidence_requires_deliberate_confirmation(roster):
    saved = import_events(roster, [event()])
    changed = event(text="A materially changed assessment")
    before = copy.deepcopy(saved)
    with pytest.raises(m.ValidationError, match="revise"):
        import_events(saved, [changed], label="changed")
    assert saved == before
    confirmed = import_events(saved, [changed], label="changed", allow_possible_updates=True)
    assert len(m.records_for(confirmed, "alex", "2026-07-01", "2026-12-31")) == 2


def test_source_exclusion_does_not_change_finalized_snapshot(roster):
    saved = import_events(roster, [event(), event(name="Brooke Sample", external_id="NI-202", native_id="eval-202")])
    finalized = m.save_review(saved, "alex", review_data(), "reviewer", finalize=True)
    original_snapshot = copy.deepcopy(finalized["reviews"][0]["snapshot"])
    assert len(original_snapshot) == 1 and original_snapshot[0]["resident_id"] == "alex"
    excluded = m.deactivate_source(finalized, finalized["sources"][0]["id"], "admin", "Synthetic correction")
    assert m.records_for(excluded, "alex", "2026-07-01", "2026-12-31") == []
    assert excluded["reviews"][0]["snapshot"] == original_snapshot
    assert finalized["sources"][0]["active"] is True


def test_complete_source_replacement_preserves_final_record(roster):
    saved = import_events(roster, [event()])
    finalized = m.save_review(saved, "alex", review_data(), "reviewer", finalize=True)
    old_snapshot = copy.deepcopy(finalized["reviews"][0]["snapshot"])
    replacement = import_events(finalized, [event(text="Corrected source report")], label="replacement",
                                replace_source_id=finalized["sources"][0]["id"])
    current = m.records_for(replacement, "alex", "2026-07-01", "2026-12-31")
    assert len(current) == 1 and current[0]["text"] == "Corrected source report"
    assert replacement["reviews"][0]["snapshot"] == old_snapshot


def test_finalized_review_cannot_be_directly_changed(roster):
    finalized = m.save_review(roster, "alex", review_data(), "reviewer", finalize=True)
    review = copy.deepcopy(finalized["reviews"][0])
    review["fields"]["ccc_decision"] = "A replacement decision"
    with pytest.raises(m.ValidationError, match="finalized"):
        m.save_review(finalized, "alex", review, "reviewer")


def test_reopen_keeps_previous_final_version_and_separate_new_evidence(roster):
    saved = import_events(roster, [event()])
    finalized = m.save_review(saved, "alex", review_data(), "reviewer", finalize=True)
    review_id = finalized["reviews"][0]["id"]
    prior = copy.deepcopy(finalized["reviews"][0])
    reopened = m.reopen_review(finalized, review_id, "admin", "Correct a documented issue")
    current = reopened["reviews"][0]
    assert current["status"] == "Draft" and "snapshot" not in current
    assert current["versions"][0]["snapshot"] == prior["snapshot"]
    updated = import_events(reopened, [event(native_id="eval-002", day="2026-11-01")], label="additional")
    refinalized = m.save_review(updated, "alex", updated["reviews"][0], "reviewer", finalize=True)
    new_review = refinalized["reviews"][0]
    assert len(new_review["snapshot"]) == 2
    assert len(new_review["versions"][0]["snapshot"]) == 1
    assert finalized["reviews"][0] == prior


def test_review_cannot_be_moved_to_another_resident(roster):
    draft = m.save_review(roster, "alex", review_data(), "reviewer")
    with pytest.raises(m.ValidationError, match="another resident"):
        m.save_review(draft, "brooke", draft["reviews"][0], "reviewer")


def test_final_snapshot_cannot_contain_other_residents_evidence(roster):
    saved = import_events(roster, [event(), event(name="Brooke Sample", external_id="NI-202", native_id="eval-202")])
    finalized = m.save_review(saved, "alex", review_data(), "reviewer", finalize=True)
    corrupt = copy.deepcopy(finalized)
    foreign = next(r for r in corrupt["records"] if r["resident_id"] == "brooke")
    corrupt["reviews"][0]["snapshot"].append(foreign)
    with pytest.raises(m.ValidationError):
        m.validate_state(corrupt)


@pytest.mark.parametrize("scenario", ["missing_collection", "duplicate_resident_id", "duplicate_external_id",
                                     "bad_start_date", "bad_evidence_reference", "invalid_pgy", "nondict_snapshot",
                                     "bad_snapshot_date", "wrong_review_resident", "nonfinite_data"])
def test_invalid_restores_are_rejected_with_domain_errors(roster, scenario):
    saved = import_events(roster, [event()])
    corrupt = m.save_review(saved, "alex", review_data(), "reviewer", finalize=True)
    if scenario == "missing_collection":
        del corrupt["sources"]
    elif scenario == "duplicate_resident_id":
        corrupt["residents"][1]["id"] = "alex"
    elif scenario == "duplicate_external_id":
        corrupt["residents"][1]["external_id"] = "NI-101"
    elif scenario == "bad_start_date":
        corrupt["residents"][0]["start_date"] = "2026-07-01INVALID"
    elif scenario == "bad_evidence_reference":
        corrupt["records"][0]["source_id"] = "nonexistent"
    elif scenario == "invalid_pgy":
        corrupt["reviews"][0]["pgy"] = "invalid"
    elif scenario == "nondict_snapshot":
        corrupt["reviews"][0]["snapshot"] = ["invalid"]
    elif scenario == "bad_snapshot_date":
        corrupt["reviews"][0]["snapshot"][0]["date"] = "invalid"
    elif scenario == "wrong_review_resident":
        corrupt["reviews"][0]["resident_id"] = "nonexistent"
    else:
        corrupt["records"][0]["value"] = float("inf")
    with pytest.raises(m.ValidationError):
        m.validate_state(corrupt)


def test_parallel_candidates_do_not_mutate_the_shared_input_state(roster):
    before = copy.deepcopy(roster)

    def draft(label):
        return m.save_review(roster, "alex", review_data(fields={"ccc_decision": label}), label)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(draft, ["Reviewer A", "Reviewer B"]))
    assert roster == before and not roster["reviews"]
    assert first["reviews"][0]["fields"]["ccc_decision"] == "Reviewer A"
    assert second["reviews"][0]["fields"]["ccc_decision"] == "Reviewer B"
    first["residents"][0]["name"] = "Changed only in the first candidate"
    assert second["residents"][0]["name"] == "Alex Example"
    assert roster["residents"][0]["name"] == "Alex Example"
