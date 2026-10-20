"""Fictional evaluation narratives; no original resident data or documents."""

from __future__ import annotations

import copy
import csv
import io

from ccc.evaluation_summary import evaluation_rows
from ccc.importers import parse_upload


def assessment(**extra):
    return {
        "id": "fictional-evaluation", "category": "evaluations", "specialty": "Medicine",
        "date": "2026-10-01", "rotation": "Fictional ward", "evaluator": "Example Faculty",
        "evaluator_status": "Faculty", "form": "Fictional faculty evaluation",
        "metric": "Evaluation", "value": 1, "unit": "assessments", "questions": [],
        "text": "", "source_locator": "Fictional sheet, row 2", **extra,
    }


def test_parsed_question_rows_render_all_wording_responses_and_distinct_comments_once():
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["Subject Name", "Subject Status", "Session Name", "Start Date", "Evaluator",
                     "Question", "Response", "Comment", "Overall Comments"])
    for question, response, comment in [
        ("How did the resident explain the plan?", "4", "Explained the next steps clearly."),
        ("What follow-up would help?", "Direct observation", "Arrange an observed handoff."),
    ]:
        writer.writerow(["Alex Example", "R2", "Fictional faculty evaluation", "2026-10-01", "Example Faculty",
                         question, response, comment, "Continue discussing uncertainty openly."])
    parsed = parse_upload("fictional.csv", stream.getvalue().encode(), "evaluations", "Medicine")
    assert not parsed.errors
    assert len(parsed.records) == 1
    before = copy.deepcopy(parsed.records)
    rows = evaluation_rows(parsed.records)
    assert len(rows) == 1
    narrative = rows[0]["narrative"]
    for original in ["How did the resident explain the plan?", "Response: 4", "Explained the next steps clearly.",
                     "What follow-up would help?", "Response: Direct observation", "Arrange an observed handoff.",
                     "Continue discussing uncertainty openly."]:
        assert narrative.count(original) == 1
    assert "Source value" not in narrative
    assert parsed.records == before


def test_repeated_source_comments_remain_under_each_question_and_overall_field():
    repeated = "A source-authored repeated comment."
    record = assessment(questions=[
        {"number": 1, "text": "First question?", "response": "3", "comment": repeated},
        {"number": 2, "text": "Second question?", "response": "4", "comment": repeated},
    ], overall_comment=repeated, text=repeated)
    narrative = evaluation_rows([record])[0]["narrative"]
    assert narrative.count(repeated) == 3
    assert "Question 1: First question?" in narrative
    assert "Question 2: Second question?" in narrative


def test_self_assessment_preserves_zero_false_and_nonnumeric_responses():
    record = assessment(category="self_assessments", metric="Self-assessment", questions=[
        {"number": 1, "text": "Numeric source response?", "response": 0, "comment": ""},
        {"number": 2, "text": "Boolean source response?", "response": False, "comment": ""},
        {"number": 3, "text": "Development description?", "response": "Not yet observed", "comment": "Please arrange observation."},
    ])
    narrative = evaluation_rows([record])[0]["narrative"]
    assert "Response: 0" in narrative
    assert "Response: False" in narrative
    assert "Response: Not yet observed" in narrative
    assert "Please arrange observation." in narrative
    assert "Source value" not in narrative


def test_independent_narrative_and_overall_paragraphs_are_retained_in_full():
    question_comment = "A specific source observation."
    overall = "First overall paragraph.\n\nSecond overall paragraph."
    independent = "Independent narrative preceding the question details."
    record = assessment(text=independent, overall_comment=overall, questions=[
        {"number": "Q-A", "text": "Original wording?", "response": "Developing", "comment": question_comment},
    ])
    narrative = evaluation_rows([record])[0]["narrative"]
    assert independent in narrative
    assert question_comment in narrative
    assert overall in narrative
    assert "Question Q-A: Original wording?" in narrative


def test_multiline_derived_text_is_not_repeated_but_original_paragraphs_are_kept():
    comment = "First comment paragraph.\n\nSecond comment paragraph."
    overall = "First overall paragraph.\n\nSecond overall paragraph."
    record = assessment(text=comment + "\n\n" + overall, overall_comment=overall, questions=[
        {"text": "Original question?", "response": "3", "comment": comment},
    ])
    narrative = evaluation_rows([record])[0]["narrative"]
    assert narrative.count(comment) == 1
    assert narrative.count(overall) == 1


def test_no_narrative_truncation_name_scrubbing_or_limit_on_distinct_evaluations():
    long_comment = "Alex Example and Example Faculty discussed the source observation.\n" + ("Complete narrative text. " * 3000) + "FINAL SOURCE SENTENCE."
    records = [assessment(id=f"fictional-{i}", text=long_comment) for i in range(65)]
    rows = evaluation_rows(records)
    assert len(rows) == 65
    assert all(row["narrative"] == long_comment for row in rows)
    assert all(row["evaluator"] == "Example Faculty [Faculty]" for row in rows)


def test_chronology_is_stable_and_incomplete_assessments_are_not_dropped():
    records = [
        assessment(id="later", date="2026-11-01", text="Later evidence."),
        assessment(id="first-same-day", text="First same-day record."),
        assessment(id="second-same-day", text="Second same-day record."),
        assessment(id="incomplete", date="", rotation="", evaluator="", evaluator_status=""),
    ]
    rows = evaluation_rows(records)
    assert [row["narrative"] for row in rows] == ["First same-day record.", "Second same-day record.", "Later evidence.", ""]
    assert rows[-1]["date"] == rows[-1]["rotation"] == rows[-1]["evaluator"] == ""
    assert rows[-1]["source_locator"] == "Fictional sheet, row 2"


def test_explicit_source_values_are_preserved_without_turning_assessment_counts_into_ratings():
    generic = assessment(metric="Communication", value=0, unit="source rating", questions=[])
    assert evaluation_rows([generic])[0]["narrative"] == "Source value (Communication): 0 source rating"
    assert evaluation_rows([assessment(value=4, unit="rating")])[0]["narrative"] == "Source value (Evaluation): 4 rating"
    assert evaluation_rows([assessment()])[0]["narrative"] == ""


def test_missing_evaluator_status_is_not_assumed_to_mean_faculty():
    assert evaluation_rows([assessment(evaluator_status="")])[0]["evaluator"] == "Example Faculty"
    assert evaluation_rows([assessment(evaluator="Example Faculty [Faculty]")])[0]["evaluator"] == "Example Faculty [Faculty]"


def test_legacy_overall_field_is_retained_without_duplicating_derived_text():
    record = assessment(text="A complete overall narrative.", values={"overall_comments": "A complete overall narrative."})
    assert evaluation_rows([record])[0]["narrative"] == "Overall comments: A complete overall narrative."
