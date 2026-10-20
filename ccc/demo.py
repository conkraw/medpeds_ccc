"""Fictional examples only. This module never reads or writes a repository."""
from __future__ import annotations
from datetime import date
from ccc import annual, model as m


def create_demo_state(today: date | None = None) -> dict:
    today = today or date(2026, 10, 17)
    academic_start = m.current_academic_start(today)
    state = m.new_state("Medicine Pediatrics Residency")
    names = ["Alex Example", "Casey Sample", "Morgan Demo", "Taylor Fiction"]
    for i, name in enumerate(names, 1):
        start = date(academic_start.year - i + 1, 7, 1)
        state = m.save_resident(state, {"name": name, "start_date": start.isoformat(),
                                       "graduation_date": (m.add_months(start, 48)).isoformat(), "coach_name": "Example Faculty Coach",
                                       "aliases": [], "status": "Active"}, "demo")
    for idx, resident in enumerate(state["residents"]):
        day1 = min(today, academic_start.replace(day=15))
        day2 = min(today, m.add_months(academic_start, 2).replace(day=15))
        records = []
        for specialty in ("Medicine", "Pediatrics"):
            for j, observed in enumerate((day1, day2)):
                records.append({"resident_name": resident["name"], "category": "evaluations", "specialty": specialty,
                                "date": observed.isoformat(), "form": f"{specialty} rotation evaluation", "metric": "Faculty evaluation",
                                "value": None, "unit": "", "text": "Fictional example: communicates clearly with the team and follows through on patient care tasks.",
                                "rotation": specialty + " inpatient", "evaluator": "Example Faculty",
                                "questions": [{"number": "1", "text": "Describe a strength.", "response": "Progressing", "comment": "Organizes a clear assessment and plan."},
                                              {"number": "2", "text": "Identify a next learning goal.", "response": "Feedback provided", "comment": "Anticipate discharge needs earlier in the admission."}],
                                "record_mode": "event", "source_locator": "Fictional example row " + str(j + 1)})
                records.append({"resident_name": resident["name"], "category": "milestones", "specialty": specialty,
                                "date": observed.isoformat(), "period_start": academic_start.isoformat(), "period_end": observed.isoformat(),
                                "form": specialty + " milestones", "metric": "PC1", "value": min(4, 1.5 + idx * .5 + j * .5),
                                "unit": "score", "text": "Fictional score for preview only", "record_mode": "snapshot", "source_locator": "Fictional score"})
        records.extend([
            {"resident_name": resident["name"], "category": "self_assessments", "specialty": "Combined", "date": day2.isoformat(),
             "form": "Resident self-assessment", "metric": "Reflection", "value": None, "record_mode": "event",
             "text": "Fictional example reflection.", "questions": [{"number": "1", "text": "What is your next learning goal?", "response": "Improve transitions of care", "comment": "Ask for two observed handoffs and review the feedback with my coach."}]},
            {"resident_name": resident["name"], "category": "procedures", "specialty": "Pediatrics", "date": day2.isoformat(), "form": "Procedure log", "metric": "Lumbar puncture", "value": 1, "unit": "procedure", "record_mode": "event"},
            {"resident_name": resident["name"], "category": "conferences", "specialty": "Combined", "date": day2.isoformat(), "period_start": academic_start.isoformat(), "period_end": day2.isoformat(),
             "form": "Conference attendance", "metric": "Noon conference", "value": 82, "unit": "%", "record_mode": "snapshot", "text": "Fictional attendance percentage"},
            {"resident_name": resident["name"], "category": "duty_hours", "specialty": "Medicine", "date": day2.isoformat(), "form": "Shift log", "metric": "Hours logged", "value": 10, "unit": "hours", "record_mode": "event",
             "rotation": "Medicine inpatient", "values": {"start": day2.isoformat() + "T07:00:00", "end": day2.isoformat() + "T17:00:00"}},
        ])
        # Sources are grouped by kind and specialty just as in the live importer.
        for kind in m.CATEGORIES:
            for specialty in m.SPECIALTIES:
                selected = [r for r in records if r["category"] == kind and r["specialty"] == specialty]
                if not selected:
                    continue
                source = {"name": f"fictional_{idx}_{kind}_{specialty}.csv", "sha256": f"fictional-{idx}-{kind}-{specialty}",
                          "kind": kind, "specialty": specialty, "period_start": academic_start.isoformat(), "period_end": today.isoformat()}
                state, _ = m.import_records(state, selected, source, {}, "demo")
        for pgy in range(1, idx + 2):
            yearly = annual.default_progress(resident, pgy, today)
            current_year = pgy == idx + 1
            yearly["fields"].update({
                "pediatrics_ite_score": 55 + 4 * pgy,
                "pediatrics_ite_expected_pass_rate": 65 + 6 * pgy,
                "medicine_ite_score": 60 + 3 * pgy,
                "medicine_ite_percentile": 48 + 8 * pgy,
                "adult_clinic_count": 24 if current_year else 120 + 25 * pgy,
                "pediatric_clinic_count": 21 if current_year else 105 + 20 * pgy,
                "observed_encounter_1": True,
                "observed_encounter_2": True,
                "observed_encounter_3": False if current_year else True,
                "observed_encounter_4": None if current_year else True,
            })
            state = annual.save_progress(state, resident["id"], yearly, "demo")
        review = {"pgy": idx + 1, "half": "Midyear", "academic_year": m.academic_year(today), "period_start": academic_start.isoformat(), "period_end": today.isoformat(),
                  "review_date": today.isoformat(), "fields": {
                      "overall_impression": "Fictional example: making progress in both specialties, with an ongoing goal of planning transitions earlier.",
                      "strengths": "Organized assessments, clear communication, and reliable follow-through.",
                      "areas_for_growth": "Anticipate discharge needs and communicate contingency plans earlier.",
                      "ccc_decision": "Fictional example: continue the current training plan with focused feedback on transitions of care.",
                      "action_plan": "Complete two directly observed handoffs in each specialty and review the feedback with the faculty coach.",
                      "mentor": "Example Faculty Coach", "follow_up_date": "Next coaching meeting",
                      "coach_name": "Example Faculty Coach", "adult_clinic_count": "24", "pediatric_clinic_count": "21",
                      "resident_comments": "Fictional example: I will bring the handoff feedback to my next coaching meeting."}}
        state = m.save_review(state, resident["id"], review, "demo", finalize=(idx == 0))
    return state
