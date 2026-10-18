"""Synthetic import tests. These fixtures contain no real resident information."""
import csv
import hashlib
import io
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ccc import model as m
from ccc.importers import ParseResult, parse_upload


def csv_bytes(headers, rows, encoding="utf-8", delimiter=","):
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=delimiter)
    writer.writerow(headers)
    writer.writerows(rows)
    return out.getvalue().encode(encoding)


def synthetic_xlsx(*, percent=False, formula=False):
    """Minimal OOXML fixture assembled in memory; never contains real data."""
    content_types = '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    relationships = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    workbook = '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Synthetic" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    workbook_rels = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    styles = '''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="9" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    headers = ["Person", "Category", "Status", "% Attended"] if percent else ["resident_name", "date", "metric", "value"]
    values = ["Example, Alex", "Conference", "R2"] if percent else ["Example, Alex", "2026-10-01", "Meeting"]
    def inline(col, row, value):
        return f'<c r="{col}{row}" t="inlineStr"><is><t>{value}</t></is></c>'
    header = ''.join(inline(chr(65+i), 1, value) for i, value in enumerate(headers))
    body = ''.join(inline(chr(65+i), 2, value) for i, value in enumerate(values))
    numeric = '<c r="D2" s="1"><v>0.75</v></c>' if percent else '<c r="D2">' + ('<f>1+1</f>' if formula else '') + '<v>2</v></c>'
    sheet = f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:D2"/><sheetData><row r="1">{header}</row><row r="2">{body}{numeric}</row></sheetData></worksheet>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in [("[Content_Types].xml", content_types), ("_rels/.rels", relationships), ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", workbook_rels), ("xl/styles.xml", styles), ("xl/worksheets/sheet1.xml", sheet)]:
            archive.writestr(path, text)
    return output.getvalue()


class ImporterTests(unittest.TestCase):
    def test_name_only_examples_parse_match_and_keep_distinct_events(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        state = m.new_state("Fictional name-only example program")
        with (examples / "roster.csv").open(newline="", encoding="utf-8-sig") as fixture:
            for row in csv.DictReader(fixture):
                self.assertNotIn("external_id", row)
                row["aliases"] = [alias.strip() for alias in row.get("aliases", "").split(";") if alias.strip()]
                state = m.save_resident(state, row, "admin")
        self.assertEqual(len(state["residents"]), 1)
        resident_id = state["residents"][0]["id"]
        self.assertTrue(resident_id)
        self.assertFalse(state["residents"][0].get("external_id"))

        cases = [
            ("evaluations_medicine.csv", "evaluations", "Medicine", 2),
            ("evaluations_pediatrics.csv", "evaluations", "Pediatrics", 1),
            ("self_assessments.csv", "self_assessments", "Combined", 1),
            ("milestones.csv", "milestones", "Combined", 4),
            ("procedures.csv", "procedures", "Medicine", 2),
            ("conferences.csv", "conferences", "Medicine", 2),
            ("duty_hours.csv", "duty_hours", "Medicine", 2),
            ("duty_violations.csv", "duty_violations", "Medicine", 1),
        ]
        for filename, kind, specialty, expected_count in cases:
            with self.subTest(filename=filename):
                data = (examples / filename).read_bytes()
                parsed = parse_upload(filename, data, kind, specialty, "2026-07-01", "2026-09-30")
                self.assertFalse(parsed.errors, parsed.errors)
                self.assertEqual(len(parsed.records), expected_count)
                for record in parsed.records:
                    # Resident identity and a native event ID are both optional
                    # in source exports; the app supplies its own internal IDs.
                    self.assertFalse(record.get("external_id"))
                    self.assertFalse(record.get("external_record_id"))
                    self.assertEqual(m.match_resident(state, record["resident_name"]), resident_id)
                source = {"name": filename, "sha256": hashlib.sha256(data).hexdigest(), "kind": kind,
                          "specialty": specialty, "period_start": "2026-07-01", "period_end": "2026-09-30"}
                state, stats = m.import_records(state, parsed.records, source, {}, "admin")
                self.assertEqual(stats["new_records"], expected_count)
                self.assertEqual(stats["duplicates"], 0)
                self.assertEqual(stats["possible_updates"], 0)
                repeated, stats = m.prepare_records(state, parsed.records, {})
                self.assertEqual(repeated, [])
                self.assertEqual(stats["duplicates"], expected_count)

        self.assertEqual(len(state["records"]), 15)
        self.assertEqual(len({record["id"] for record in state["records"]}), 15)
        self.assertEqual({record["resident_id"] for record in state["records"]}, {resident_id})
        evaluations = [record for record in state["records"] if record["category"] == "evaluations"]
        self.assertEqual(len(evaluations), 3)
        self.assertEqual(sum(len(record["questions"]) for record in evaluations), 5)
        procedures = [record for record in state["records"] if record["category"] == "procedures"]
        self.assertEqual(len(procedures), 2)
        self.assertEqual(procedures[0]["date"], procedures[1]["date"])
        self.assertEqual(procedures[0]["metric"], procedures[1]["metric"])
        self.assertEqual(m.validate_state(state), state)

    def test_question_rows_collapse_without_losing_overall_or_response(self):
        headers = ["Subject Status", "Last Name", "First Name", "Evaluator", "Start Date", "Subject Rotation", "Evaluator Department", "Session Name", "Question", "Response", "Comment", "Overall Comments"]
        base = ["PGY2", "Example", "Alex", "Reviewer One", "08/01/2026", "WARD", "MED", "Rotation evaluation"]
        rows = [base + ["Clinical reasoning", "3", "Specific observation.", "Distinct overall comment."],
                base + ["Communication", "4", "Clear communication.", "Distinct overall comment."],
                base + ["Communication", "4", "Clear communication.", "Distinct overall comment."]]
        result = parse_upload("eval.csv", csv_bytes(headers, rows), "evaluations", "Pediatrics")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.records), 1)
        rec = result.records[0]
        self.assertEqual(rec["resident_name"], "Example, Alex")
        self.assertEqual(rec["specialty"], "Pediatrics")
        self.assertEqual(len(rec["questions"]), 2)
        self.assertEqual(rec["questions"][0]["response"], "3")
        self.assertIn("Distinct overall comment.", rec["text"])
        self.assertEqual(rec["date"], "2026-08-01")
        self.assertEqual(rec["pgy"], 2)
        self.assertTrue(any("duplicate question" in warning for warning in result.warnings))

    def test_more_than_fifty_evaluations_are_retained(self):
        headers = ["Subject Name", "Subject Status", "Session Date", "Session", "Evaluator Name", "Overall Comments"]
        rows = [["Example, Alex", "R1", "2026-08-01", "Evaluation", f"Synthetic reviewer {i}", f"Observation {i}"] for i in range(60)]
        result = parse_upload("eval.csv", csv_bytes(headers, rows), "evaluations", "Medicine")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.records), 60)

    def test_sar_utf16_preserves_three_reviews_and_question_wording(self):
        headers = ["Subject Name", "Subject Status", "Evaluation Start Date", "Evaluation End Date", "Date Completed", "Evaluator Name", "Evaluator Status", "Session", "[Question 1]", "[Question 1 Response]", "[Question 1 Comment]"]
        rows = [["Example, Alex", "R2", "07/01/2026", "12/31/2026", date, "Reviewer", "Faculty", "Self-assessment", question, "Developing", "Keep practising"] for date, question in [("10/01/2026", "Goal at first review?"), ("11/01/2026", "Goal at second review?"), ("12/01/2026", "Updated goal wording?")]]
        result = parse_upload("review.TAB", csv_bytes(headers, rows, "utf-16", "\t"), "self_assessments", "Combined")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.records), 3)
        self.assertEqual([r["date"] for r in result.records], ["2026-10-01", "2026-11-01", "2026-12-01"])
        self.assertEqual(result.records[-1]["questions"][0]["text"], "Updated goal wording?")
        self.assertEqual(result.records[-1]["questions"][0]["response"], "Developing")

    def test_milestone_long_csv_keeps_not_assessed_distinct_from_zero(self):
        headers = ["resident_name", "training_year", "report_dates", "milestone", "value"]
        rows = [["Example, Alex", "PGY3", "07/01/2026 - 12/31/2026", "PC 1", "x"], ["Example, Alex", "PGY3", "07/01/2026 - 12/31/2026", "PROF 2", "0.00"]]
        result = parse_upload("scores.csv", csv_bytes(headers, rows), "milestones", "Medicine")
        self.assertFalse(result.errors, result.errors)
        self.assertIsNone(result.records[0]["value"])
        self.assertEqual(result.records[1]["value"], 0.0)
        self.assertEqual(result.records[1]["metric"], "PROF2")
        self.assertEqual(result.records[0]["record_mode"], "snapshot")
        self.assertEqual(result.records[0]["period_start"], "2026-07-01")

    def test_milestones_require_specialty_and_valid_score(self):
        data = csv_bytes(["resident_name", "date", "milestone", "value"], [["Example, Alex", "2026-10-01", "PC1", "7"]])
        self.assertTrue(parse_upload("scores.csv", data, "milestones", "Combined").errors)
        result = parse_upload("scores.csv", data, "milestones", "Medicine")
        self.assertTrue(result.errors)
        self.assertFalse(result.records)

    def test_native_procedure_ids_preserve_same_day_events(self):
        headers = ["ResidentID", "ResidentLastName", "ResidentFirstName", "DatePerformed", "ResidentStatus", "Procedure", "ProcedureLogID"]
        rows = [["synthetic-1", "Example", "Alex", "08/10/2026", "R2", "Procedure A", "event-1"], ["synthetic-1", "Example", "Alex", "08/10/2026", "R2", "Procedure A", "event-2"]]
        result = parse_upload("Logger_Extract.csv", csv_bytes(headers, rows), "procedures", "Pediatrics")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.records), 2)
        self.assertEqual([r["external_record_id"] for r in result.records], ["event-1", "event-2"])
        self.assertEqual(result.records[0]["value"], 1)
        self.assertEqual(result.records[0]["external_id"], "synthetic-1")

    def test_html_disguised_xls_conference_is_snapshot_with_explicit_fallback(self):
        html = b'<html><table><tr><th>Person</th><th>Category</th><th>Status</th><th>% Attended</th></tr><tr><td>Example, Alex</td><td>Noon conference</td><td>R2</td><td>75%</td></tr></table></html>'
        result = parse_upload("conf_host_aspx.xls", html, "conferences", "Medicine", "2026-07-01", "2026-10-01")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0]["value"], 75)
        self.assertEqual(result.records[0]["record_mode"], "snapshot")
        self.assertEqual(result.records[0]["date"], "2026-10-01")
        self.assertTrue(result.records[0]["date_is_fallback"])
        self.assertIsNone(result.records[0]["values"]["offered"])

    def test_hierarchical_duty_html_keeps_overnight_shift(self):
        html = b'''<html><table><tr><th>Work Type</th><th>Start</th><th>End</th><th>Hours</th><th>Rotation</th></tr>
        <tr><td>R2</td><td></td><td></td><td></td><td></td></tr>
        <tr><td>Example, Alex</td><td></td><td></td><td></td><td></td></tr>
        <tr><td>Clinical</td><td>08/10/2026 20:00</td><td>08/11/2026 08:00</td><td>12</td><td>Ward</td></tr>
        <tr><td>Total Hrs : 12</td><td></td><td></td><td></td><td></td></tr></table></html>'''
        result = parse_upload("GridExport.xls", html, "duty_hours", "Medicine")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.records), 1)
        rec = result.records[0]
        self.assertEqual(rec["resident_name"], "Example, Alex")
        self.assertEqual(rec["value"], 12)
        self.assertEqual(rec["values"]["elapsed_hours"], 12)
        self.assertEqual(rec["date"], "2026-08-10")
        self.assertEqual(rec["period_end"], "2026-08-11")

    def test_missing_duty_name_does_not_inherit_previous_pgy_resident(self):
        data = csv_bytes(["Work Type", "Start", "End", "Hours"], [["R1", "", "", ""], ["Example, Alex", "", "", ""], ["Clinical", "08/01/2026 08:00", "08/01/2026 16:00", "8"], ["R2", "", "", ""], ["Clinical", "08/02/2026 08:00", "08/02/2026 16:00", "8"]])
        result = parse_upload("GridExport.csv", data, "duty_hours", "Medicine")
        self.assertTrue(result.errors)
        self.assertEqual(len(result.records), 1)

    def test_violation_events_are_not_capped(self):
        headers = ["Name", "Status", "Log Date", "Rule", "Description", "Comments", "Rotation", "Work Type"]
        rows = [["Example, Alex", "R3", f"08/{n+1:02d}/2026", "Source rule", "Source description", f"Source comment {n}", "Ward", "Clinical"] for n in range(8)]
        result = parse_upload("violations.csv", csv_bytes(headers, rows), "duty_violations", "Medicine")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.records), 8)
        self.assertIn("Source comment 7", result.records[-1]["text"])

    def test_missing_date_requires_selected_period(self):
        data = csv_bytes(["resident_name", "metric", "value", "text"], [["Example, Alex", "Meeting", "1", "Discussion"]])
        missing = parse_upload("notes.csv", data, "evaluations", "Combined")
        self.assertTrue(missing.errors)
        selected = parse_upload("notes.csv", data, "evaluations", "Combined", "2026-07-01", "2026-10-01")
        self.assertFalse(selected.errors, selected.errors)
        self.assertTrue(selected.records[0]["date_is_fallback"])

    def test_unsupported_headers_empty_files_and_code_are_errors(self):
        for name, data in [("bad.csv", b"a,b\n1,2\n"), ("empty.csv", b""), ("script.ipynb", b"{}")]:
            self.assertTrue(parse_upload(name, data, "evaluations", "Medicine").errors)

    def test_pdf_positional_scores_are_refused(self):
        pages = ["Resident: Example, Alex\n07/01/2026 - 12/31/2026\n" + "\n".join(["3.00"] * 21)]
        with patch("ccc.importers._read_pdf_pages", return_value=pages):
            result = parse_upload("scores.pdf", b"%PDF synthetic", "milestones", "Medicine")
        self.assertTrue(result.errors)
        self.assertFalse(result.records)
        self.assertIn("will not guess", result.errors[0])

    def test_pdf_explicit_codes_accept_missing_scores_without_averaging(self):
        pages = ["Resident: Example, Alex\n07/01/2026 - 12/31/2026\n2nd Year in Residency\nPC 1 History 3.00\nPC 2 Exam x\nPROF 1 Accountability 2.50"]
        with patch("ccc.importers._read_pdf_pages", return_value=pages):
            result = parse_upload("scores.pdf", b"%PDF synthetic", "milestones", "Medicine")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.records), 3)
        self.assertIsNone(result.records[1]["value"])
        self.assertEqual(result.records[0]["pgy"], 2)

    def test_pdf_multiple_columns_or_invalid_scores_block_file(self):
        for rows in ["PC1 3.0 4.0\nPC2 2.0", "PC1 -1\nPC2 2.0", "PC1 6.0\nPC2 2.0"]:
            pages = [f"Resident: Example, Alex\n07/01/2026 - 12/31/2026\n{rows}"]
            with patch("ccc.importers._read_pdf_pages", return_value=pages):
                result = parse_upload("scores.pdf", b"%PDF synthetic", "milestones", "Medicine")
            self.assertTrue(result.errors, rows)

    def test_xlsx_cached_formula_value_is_read_without_recalculation(self):
        result = parse_upload("synthetic.xlsx", synthetic_xlsx(formula=True), "evaluations", "Medicine")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(result.records[0]["value"], 2)
        self.assertTrue(any("never executed" in warning for warning in result.warnings))

    def test_xlsx_percentage_format_preserves_75_percent(self):
        result = parse_upload("conference.xlsx", synthetic_xlsx(percent=True), "conferences", "Medicine", "2026-07-01", "2026-10-01")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(result.records[0]["value"], 75)

    def test_file_and_zip_expansion_bounds_block_before_parsing(self):
        with patch("ccc.importers.MAX_FILE_BYTES", 10):
            result = parse_upload("big.csv", b"x" * 11, "evaluations", "Medicine")
        self.assertTrue(result.errors)
        self.assertFalse(result.records)
        with patch("ccc.importers.MAX_EXPANDED_BYTES", 10):
            result = parse_upload("big.xlsx", synthetic_xlsx(), "evaluations", "Medicine")
        self.assertTrue(any("expands" in error for error in result.errors))
        self.assertFalse(result.records)

    def test_unknown_and_zero_responses_are_preserved(self):
        data = csv_bytes(["Subject Name", "Date Completed", "Session", "[Question 1]", "[Question 1 Response]", "[Question 2]", "[Question 2 Response]"], [["Example, Alex", "2026-10-01", "Evaluation", "Item one", "Unable to assess", "Item two", 0]])
        result = parse_upload("responses.csv", data, "evaluations", "Medicine")
        self.assertFalse(result.errors, result.errors)
        questions = result.records[0]["questions"]
        self.assertEqual(questions[0]["response"], "Unable to assess")
        self.assertEqual(questions[1]["response"], "0")

    def test_generic_summary_keeps_explicit_snapshot_mode(self):
        data = csv_bytes(["resident_name", "date", "metric", "value", "record_mode", "period_start", "period_end"], [["Example, Alex", "2026-09-30", "Procedure A", "8", "snapshot", "2026-07-01", "2026-09-30"]])
        result = parse_upload("procedure_summary.csv", data, "procedures", "Medicine")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(result.records[0]["record_mode"], "snapshot")
        self.assertEqual(result.records[0]["value"], 8)

    def test_procedure_definition_id_is_not_assumed_to_be_log_id(self):
        data = csv_bytes(["ResidentLastName", "ResidentFirstName", "DatePerformed", "Procedure", "ProcedureID"], [["Example", "Alex", "2026-09-30", "Procedure A", "type-1"], ["Example", "Alex", "2026-09-30", "Procedure A", "type-1"]])
        result = parse_upload("procedures.csv", data, "procedures", "Medicine")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.records), 2)
        self.assertTrue(all(not r["external_record_id"] for r in result.records))

    def test_invalid_completion_date_is_visible_when_start_date_is_used(self):
        data = csv_bytes(["Subject Name", "Start Date", "Date Completed", "Session", "Overall Comments"], [["Example, Alex", "2026-09-01", "unreadable date", "Evaluation", "Observation"]])
        result = parse_upload("dates.csv", data, "evaluations", "Medicine")
        self.assertFalse(result.errors, result.errors)
        self.assertTrue(result.records[0]["source_date_needs_review"])
        self.assertTrue(any("nonblank source date" in warning for warning in result.warnings))

    def test_explicit_specialty_override_is_visible(self):
        data = csv_bytes(["resident_name", "date", "metric", "value", "text", "specialty"], [["Example, Alex", "2026-09-01", "Evaluation", 1, "Observation", "Pediatrics"]])
        result = parse_upload("override.csv", data, "evaluations", "Medicine")
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(result.records[0]["specialty"], "Medicine")
        self.assertTrue(any("Pediatrics" in warning and "Medicine" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
