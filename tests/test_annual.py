"""Yearly tracking, historical cutoffs, and frozen review acceptance checks.

All people, scores, and events are fictional. Persistence encryption and
conflict protection are covered by the existing storage and app suites.
"""

from __future__ import annotations

import copy
import hashlib

import pytest

from ccc import annual as a
from ccc import model as m


@pytest.fixture
def roster():
    state = m.new_state("Fictional annual tracking program")
    for identifier, name in (("alex", "Alex Example"), ("brooke", "Brooke Sample")):
        state = m.save_resident(state, {"id": identifier, "name": name,
                                        "start_date": "2026-07-01"}, "admin")
    return state


def values(state, resident_id="alex", pgy=1, *, as_of="2026-10-01", fields=None, **extra):
    data = a.default_progress(m.resident_by_id(state, resident_id), pgy, as_of)
    return {**data, "as_of": as_of, "fields": {**data["fields"], **(fields or {})}, **extra}


def review_data(**extra):
    return {"pgy": 1, "half": "Midyear", "academic_year": "2026-2027",
            "period_start": "2026-07-01", "period_end": "2026-12-31", "review_date": "2027-01-05",
            "fields": {"ccc_decision": "Fictional recorded decision"}, **extra}


def test_defaults_cover_all_four_training_years_and_start_blank(roster):
    resident = m.resident_by_id(roster, "alex")
    for pgy in range(1, 5):
        data = a.default_progress(resident, pgy, "2026-10-01")
        assert data["period_start"] == f"{2025 + pgy}-07-01"
        assert data["period_end"] == f"{2026 + pgy}-06-30"
        assert data["academic_year"] == f"{2025 + pgy}-{2026 + pgy}"
        assert data["period_start"] <= data["as_of"] <= data["period_end"]
        assert set(data["fields"]) == set(a.FIELD_LABELS)
        assert all(value is None for value in data["fields"].values())
    assert a.default_progress(resident, 1, "2031-01-01")["as_of"] == "2027-06-30"


def test_missing_and_zero_are_distinct_for_scores_tallies_and_completion(roster):
    data = values(roster, fields={"pediatrics_ite_score": "0", "pediatrics_ite_expected_pass_rate": "",
                                  "medicine_ite_score": "542.5", "medicine_ite_percentile": "0",
                                  "adult_clinic_count": 0, "pediatric_clinic_count": "  ",
                                  "observed_encounter_1": False, "observed_encounter_2": True})
    saved = a.save_progress(roster, "alex", data, "admin")
    fields = saved["annual_progress"][0]["fields"]
    assert fields["pediatrics_ite_score"] == 0
    assert fields["pediatrics_ite_expected_pass_rate"] is None
    assert fields["medicine_ite_score"] == 542.5
    assert fields["medicine_ite_percentile"] == 0
    assert fields["adult_clinic_count"] == 0 and fields["pediatric_clinic_count"] is None
    assert fields["observed_encounter_1"] is False
    assert fields["observed_encounter_2"] is True
    assert fields["observed_encounter_3"] is None and fields["observed_encounter_4"] is None
    assert a.progress_for(saved, "alex", "2026-10-01")[0]["fields"] == fields


@pytest.mark.parametrize("field,bad", [
    ("pediatrics_ite_expected_pass_rate", -1), ("pediatrics_ite_expected_pass_rate", 100.01),
    ("medicine_ite_percentile", "101"), ("medicine_ite_score", "NaN"),
    ("pediatrics_ite_score", float("inf")), ("medicine_ite_score", -5),
    ("adult_clinic_count", "2.5"), ("pediatric_clinic_count", -1),
    ("adult_clinic_count", True), ("adult_clinic_count", 2**53),
    ("medicine_ite_score", []), ("observed_encounter_1", 1),
    ("observed_encounter_2", "Completed"), ("pediatrics_ite_percentile", 35),
])
def test_invalid_or_mislabeled_values_are_rejected_without_changing_state(roster, field, bad):
    before = copy.deepcopy(roster)
    with pytest.raises(m.ValidationError):
        a.save_progress(roster, "alex", values(roster, fields={field: bad}), "admin")
    assert roster == before


@pytest.mark.parametrize("field", ["pediatrics_ite_expected_pass_rate", "medicine_ite_percentile"])
@pytest.mark.parametrize("value", [0, 100])
def test_percentage_endpoints_are_valid(roster, field, value):
    saved = a.save_progress(roster, "alex", values(roster, fields={field: value}), "admin")
    assert saved["annual_progress"][0]["fields"][field] == value


@pytest.mark.parametrize("change", [
    {"pgy": 0}, {"pgy": 5}, {"pgy": True}, {"pgy": "1"},
    {"as_of": "2026-06-30"}, {"as_of": "2027-07-01"},
    {"period_start": "2027-07-01"}, {"period_end": "2026-01-01"},
    {"as_of": "not a date"}, {"academic_year": "2026-2028"},
])
def test_invalid_training_year_dates_and_identity_are_rejected(roster, change):
    with pytest.raises(m.ValidationError):
        a.save_progress(roster, "alex", values(roster, **change), "admin")


def test_existing_year_is_updated_and_dated_versions_preserve_prior_totals(roster):
    october = a.save_progress(roster, "alex", values(roster, fields={"adult_clinic_count": 0}), "admin")
    before = copy.deepcopy(october)
    # An exact year without an ID finds its saved entry instead of duplicating it.
    december = a.save_progress(october, "alex", values(october, as_of="2026-12-31",
                                                      fields={"adult_clinic_count": 90}), "admin")
    june = a.save_progress(december, "alex", {**december["annual_progress"][0], "as_of": "2027-06-30",
                                              "fields": {"adult_clinic_count": 220}}, "admin")
    assert len(june["annual_progress"]) == 1
    assert len(june["annual_progress"][0]["versions"]) == 2
    assert a.progress_for(june, "alex", "2026-09-30") == []
    assert a.progress_for(june, "alex", "2026-10-01")[0]["fields"]["adult_clinic_count"] == 0
    assert a.progress_for(june, "alex", "2026-12-31")[0]["fields"]["adult_clinic_count"] == 90
    assert a.progress_for(june, "alex", "2027-06-30")[0]["fields"]["adult_clinic_count"] == 220
    assert a.progress_for(june, "brooke", "2027-06-30") == []
    assert october == before
    june["annual_progress"][0]["versions"][0]["fields"]["adult_clinic_count"] = 999
    assert october == before


def test_same_date_correction_wins_and_explicit_blank_does_not_revert_to_old_value(roster):
    saved = a.save_progress(roster, "alex", values(roster, fields={"adult_clinic_count": 4,
                                                                 "pediatric_clinic_count": 3}), "admin")
    revised = a.save_progress(saved, "alex", {**saved["annual_progress"][0],
                                              "fields": {"adult_clinic_count": 0}}, "admin")
    assert a.progress_for(revised, "alex", "2026-10-01")[0]["fields"]["adult_clinic_count"] == 0
    assert revised["annual_progress"][0]["fields"]["pediatric_clinic_count"] == 3
    cleared = a.save_progress(revised, "alex", {**revised["annual_progress"][0],
                                               "fields": {"adult_clinic_count": ""}}, "admin")
    assert a.progress_for(cleared, "alex", "2026-10-01")[0]["fields"]["adult_clinic_count"] is None


def test_repeated_pgy_years_and_other_residents_remain_separate(roster):
    saved = a.save_progress(roster, "alex", values(roster, fields={"adult_clinic_count": 20}), "admin")
    repeated = values(saved, pgy=1, period_start="2027-07-01", period_end="2028-06-30",
                      academic_year="2027-2028", as_of="2027-12-31", fields={"adult_clinic_count": 50})
    saved = a.save_progress(saved, "alex", repeated, "admin")
    saved = a.save_progress(saved, "brooke", values(saved, "brooke", fields={"adult_clinic_count": 200}), "admin")
    own = a.progress_for(saved, "alex", "2028-06-30")
    assert [(item["pgy"], item["academic_year"], item["fields"]["adult_clinic_count"]) for item in own] == [
        (1, "2026-2027", 20), (1, "2027-2028", 50)]
    assert a.progress_for(saved, "brooke", "2028-06-30")[0]["fields"]["adult_clinic_count"] == 200
    assert len({item["id"] for item in saved["annual_progress"]}) == 3


def test_entry_cannot_be_moved_or_overwrite_a_different_years_identity(roster):
    saved = a.save_progress(roster, "alex", values(roster), "admin")
    with pytest.raises(m.ValidationError, match="another resident"):
        a.save_progress(saved, "brooke", saved["annual_progress"][0], "admin")
    with pytest.raises(m.ValidationError, match="already has"):
        a.save_progress(saved, "alex", {**saved["annual_progress"][0], "id": "different-id"}, "admin")
    with pytest.raises(m.ValidationError, match="Choose a resident"):
        a.save_progress(saved, "missing", values(saved), "admin")


def test_new_reviews_do_not_reset_yearly_values_and_finals_freeze_them(roster):
    saved = a.save_progress(roster, "alex", values(roster, fields={"pediatrics_ite_score": 310,
                                                                 "pediatrics_ite_expected_pass_rate": 85,
                                                                 "observed_encounter_1": True}), "admin")
    annual_before = copy.deepcopy(saved["annual_progress"])
    draft = m.save_review(saved, "alex", review_data(), "reviewer")
    assert draft["annual_progress"] == annual_before
    finalized = m.save_review(draft, "alex", draft["reviews"][0], "reviewer", finalize=True)
    frozen = copy.deepcopy(finalized["reviews"][0])
    later = a.save_progress(finalized, "alex", {**finalized["annual_progress"][0], "as_of": "2026-12-31",
                                                "fields": {"pediatrics_ite_score": 360}}, "admin")
    final_rows = a.progress_for(later, "alex", "2026-12-31", review=later["reviews"][0])
    assert final_rows[0]["fields"]["pediatrics_ite_score"] == 310
    assert a.progress_for(later, "alex", "2026-12-31")[0]["fields"]["pediatrics_ite_score"] == 360
    assert later["reviews"][0] == frozen
    reopened = m.reopen_review(later, frozen["id"], "admin", "Fictional correction")
    current = reopened["reviews"][0]
    assert "annual_progress_snapshot" not in current
    assert "cumulative_evidence_snapshot" not in current
    assert current["versions"][0]["annual_progress_snapshot"] == frozen["annual_progress_snapshot"]
    assert a.progress_for(reopened, "alex", "2026-12-31", review=current)[0]["fields"]["pediatrics_ite_score"] == 360
    refinalized = m.save_review(reopened, "alex", current, "reviewer", finalize=True)
    assert refinalized["reviews"][0]["annual_progress_snapshot"][0]["fields"]["pediatrics_ite_score"] == 360
    assert refinalized["reviews"][0]["versions"][0]["annual_progress_snapshot"] == frozen["annual_progress_snapshot"]


def test_finalization_uses_historical_annual_values_through_its_evidence_cutoff(roster):
    state = a.save_progress(roster, "alex", values(roster, fields={"adult_clinic_count": 40}), "admin")
    state = a.save_progress(state, "alex", {**state["annual_progress"][0], "as_of": "2027-06-30",
                                            "fields": {"adult_clinic_count": 140}}, "admin")
    finalized = m.save_review(state, "alex", review_data(), "reviewer", finalize=True)
    snapshot = finalized["reviews"][0]["annual_progress_snapshot"]
    assert snapshot[0]["as_of"] == "2026-10-01"
    assert snapshot[0]["fields"]["adult_clinic_count"] == 40
    assert "versions" not in snapshot[0]


@pytest.mark.parametrize("legacy", [False, True])
def test_final_without_recorded_annual_values_never_uses_later_live_values(roster, legacy):
    finalized = m.save_review(roster, "alex", review_data(fields={"ccc_decision": "Fictional decision",
                                                                "pediatrics_ite_percentile": "62"}), "reviewer", finalize=True)
    if legacy:
        finalized["reviews"][0].pop("annual_progress_snapshot")
        finalized["reviews"][0].pop("cumulative_evidence_snapshot")
    state = a.save_progress(finalized, "alex", values(finalized, fields={"pediatrics_ite_expected_pass_rate": 91}), "admin")
    assert a.progress_for(state, "alex", "2026-12-31", review=state["reviews"][0]) == []
    assert state["reviews"][0]["fields"]["pediatrics_ite_percentile"] == "62"
    assert "pediatrics_ite_expected_pass_rate" not in state["reviews"][0]["fields"]
    assert m.validate_state(state) == state


def test_old_v1_state_still_loads_without_annual_extension(roster):
    before = copy.deepcopy(roster)
    assert "annual_progress" not in roster
    assert a.progress_for(roster, "alex", "2026-12-31") == []
    assert m.validate_state(roster) == roster
    assert roster == before


def test_removal_cascades_annual_versions_and_preserves_other_residents(roster):
    state = a.save_progress(roster, "alex", values(roster, fields={"adult_clinic_count": 4}), "admin")
    state = a.save_progress(state, "alex", {**state["annual_progress"][0], "fields": {"adult_clinic_count": 5}}, "admin")
    state = a.save_progress(state, "brooke", values(state, "brooke", fields={"adult_clinic_count": 6}), "admin")
    before = copy.deepcopy(state)
    removed = m.remove_resident(state, "alex", "admin")
    assert removed["annual_progress"] == [before["annual_progress"][1]]
    assert removed["audit"][-1]["details"]["annual_records_removed"] == 1
    assert state == before
    assert m.validate_state(removed) == removed


@pytest.mark.parametrize("corruption", ["collection", "resident", "duplicate_id", "duplicate_year", "fields",
                                         "date", "academic_year", "score", "version_resident", "nested_versions",
                                         "snapshot_resident", "snapshot_future", "prior_snapshot_resident"])
def test_invalid_annual_restores_and_cross_resident_snapshots_are_rejected(roster, corruption):
    state = a.save_progress(roster, "alex", values(roster, fields={"adult_clinic_count": 4}), "admin")
    state = a.save_progress(state, "alex", {**state["annual_progress"][0], "fields": {"adult_clinic_count": 5}}, "admin")
    state = m.save_review(state, "alex", review_data(), "reviewer", finalize=True)
    entry = state["annual_progress"][0]
    if corruption == "collection":
        state["annual_progress"] = None
    elif corruption == "resident":
        entry["resident_id"] = "missing"
    elif corruption == "duplicate_id":
        state["annual_progress"].append(copy.deepcopy(entry))
    elif corruption == "duplicate_year":
        state["annual_progress"].append({**copy.deepcopy(entry), "id": "second-id", "versions": []})
    elif corruption == "fields":
        entry["fields"]["pediatrics_ite_percentile"] = 12
    elif corruption == "date":
        entry["as_of"] = "2026-99-99"
    elif corruption == "academic_year":
        del entry["academic_year"]
    elif corruption == "score":
        entry["fields"]["adult_clinic_count"] = "5"
    elif corruption == "version_resident":
        entry["versions"][0]["resident_id"] = "brooke"
    elif corruption == "nested_versions":
        entry["versions"][0]["versions"] = []
    elif corruption == "snapshot_resident":
        state["reviews"][0]["annual_progress_snapshot"][0]["resident_id"] = "brooke"
    elif corruption == "snapshot_future":
        state["reviews"][0]["annual_progress_snapshot"][0]["as_of"] = "2027-01-01"
    else:
        state = m.reopen_review(state, state["reviews"][0]["id"], "admin", "Fictional correction")
        state["reviews"][0]["versions"][0]["annual_progress_snapshot"][0]["resident_id"] = "brooke"
    with pytest.raises(m.ValidationError):
        m.validate_state(state)


def test_selected_review_must_belong_to_the_requested_resident(roster):
    state = m.save_review(roster, "brooke", review_data(), "reviewer", finalize=True)
    with pytest.raises(m.ValidationError, match="another resident"):
        a.progress_for(state, "alex", "2026-12-31", review=state["reviews"][0])


def test_cumulative_evidence_is_owned_dated_and_frozen_independently_of_review_window(roster):
    records = []
    for name, day, identifier in [("Alex Example", "2026-08-01", "prior"),
                                  ("Alex Example", "2026-11-01", "current"),
                                  ("Alex Example", "2027-01-01", "future"),
                                  ("Brooke Sample", "2026-11-01", "other-resident")]:
        records.append({"resident_name": name, "date": day, "category": "procedures",
                        "specialty": "Medicine", "record_mode": "event", "external_record_id": identifier,
                        "metric": "Fictional observed procedure", "value": 1, "unit": "count"})
    source = {"id": m.new_id(), "name": "fictional-procedures.csv", "kind": "procedures",
              "specialty": "Medicine", "sha256": hashlib.sha256(b"fictional").hexdigest(),
              "period_start": "2026-07-01", "period_end": "2027-01-31"}
    state, _ = m.import_records(roster, records, source, {}, "admin")
    state = m.save_review(state, "alex", review_data(period_start="2026-10-01"), "reviewer", finalize=True)
    review = state["reviews"][0]
    assert [record["external_record_id"] for record in review["snapshot"]] == ["current"]
    assert [record["external_record_id"] for record in review["cumulative_evidence_snapshot"]] == ["prior", "current"]
    frozen = copy.deepcopy(review)
    excluded = m.deactivate_source(state, source["id"], "admin", "Fictional source correction")
    assert m.active_records(excluded) == []
    assert excluded["reviews"][0] == frozen
    assert m.validate_state(excluded) == excluded
    for identifier in ("future", "other-resident"):
        corrupt = copy.deepcopy(state)
        corrupt["reviews"][0]["cumulative_evidence_snapshot"].append(
            next(record for record in state["records"] if record["external_record_id"] == identifier))
        with pytest.raises(m.ValidationError):
            m.validate_state(corrupt)
        reopened = m.reopen_review(state, state["reviews"][0]["id"], "admin", "Fictional correction")
        reopened["reviews"][0]["versions"][0]["cumulative_evidence_snapshot"].append(
            next(record for record in state["records"] if record["external_record_id"] == identifier))
        with pytest.raises(m.ValidationError):
            m.validate_state(reopened)
