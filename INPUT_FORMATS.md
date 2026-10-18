# Upload formats and compatibility templates

Use the original New Innovations export whenever its layout is supported. You do not need to run a notebook, combine files on your computer, or prepare REDCap field names. Choose the report category and specialty, select the report's actual dates, upload it, and review the names, dates, and counts before saving.

Residents are identified by their names in your New Innovations exports. A resident ID, evaluation ID, or REDCap record number is not required. The app creates its own internal resident reference automatically and matches future uploads to the saved name or a confirmed name variation. Optional source IDs are still recognized when an older file contains them, but the blank templates and practice files do not ask you to create any.

The import readers were built from the logic and column names in your supplied notebooks and tested with fictional fixtures. Actual New Innovations source spreadsheets and resident milestone PDFs were not supplied with those notebooks. A current deidentified example of each export is still needed to confirm its layout against the live system. The supplied REDCap PDF describes the existing dashboard; it is not a raw milestone export.

## Supported files

CSV, TSV, UTF-16 TAB, modern XLSX, binary XLS, and New Innovations HTML tables saved with an XLS extension are supported. Files are limited to 15 MB, 100,000 data rows, 400 columns, and 30 sheets/tables. XLSX expansion is limited to 100 MB. Text-based milestone PDFs may contain up to 150 pages. Excel formulas are never executed; only Excel's cached values are read. Check a warning if a workbook includes formulas.

| Upload category | Original export columns recognized | What is retained |
| --- | --- | --- |
| Evaluations | `Last Name`, `First Name`, `Subject Status`, `Evaluator`, `Start Date`, `Subject Rotation`, `Session Name`, `Question`, `Comment`, `Overall Comments`; response/rating and evaluation ID when available | One record per logical evaluation, with its questions, responses, comments, evaluator, rotation, and dates |
| Evaluations, older processed form | `Subject Name`, `Session Date`, `Session`, `Evaluator Name`, `Overall Comments`, and optional question triplets | Each evaluation and all available question content |
| Self-assessments | `Subject Name`, `Subject Status`, `Evaluation Start Date`, `Evaluation End Date`, `Date Completed`, `Evaluator Name`, `Session`, `[Question N]`, `[Question N Response]`, `[Question N Comment]` | Every form and review date, including more than two reviews in a year |
| Milestones | `resident_name`, `milestone`, `value`, with `date` or `report_dates`; `training_year` when available | Individual scores by explicit code and specialty; `x`/not assessed remains distinct from zero |
| Procedures | `ResidentLastName`, `ResidentFirstName`, `DatePerformed`, `ResidentStatus`, `Procedure`, with optional source identifiers when present | Individual dated procedure entries, including repeated procedures on the same day |
| Conference attendance | `Person`, `Category`, `Status`, `% Attended` | Each category's percentage and report period as a snapshot |
| Duty hours | `Name`, `Start`, `End`, `Hours`, `Work Type`; or the hierarchical `Work Type` report with PGY and resident header rows | Individual shifts, reported hours, actual start/end times, and source details |
| Duty-hour concerns | `Name`, `Status`, `Log Date`, `Rule`, `Description`, `Comments`, `Rotation`, `Work Type` | All source-reported concerns, without a five-entry limit |

Header capitalization, surrounding spaces, and common punctuation differences are normalized. The first 40 rows are checked for a report header. Names are retained for resident matching; a missing resident name blocks the affected file.

## Dates, specialties, and counts

Completion dates are preserved when present. Otherwise, the reader uses the relevant event date or source reporting dates. When the file has no date, the selected report end date can be used, but the preview identifies that fallback and requires review. If neither the source nor the upload supplies a valid date, the file cannot be accepted. Choose the actual period represented by a conference or milestone snapshot; a cumulative annual report should retain its annual coverage.

Select Medicine or Pediatrics for a single-specialty export. Combined can be used for shared reports. In a combined file, a recognized `specialty` or `eval_type` column identifies the specialty of each row. The reader does not classify every unfamiliar rotation as Medicine. Milestone scores require an explicit Medicine or Pediatrics classification because the two programs use different sets of codes.

A question-per-row evaluation file can contain many spreadsheet rows for one evaluation. The reader groups rows using the native evaluation ID when available together with the resident, form, evaluator, rotation, and dates. Without that ID, it uses the remaining context and shows a warning. Exact repeated question rows within that group are removed. Distinct responses, question wording, and overall comments are preserved. There is no 50-evaluation cap. Retain native event IDs when exporting procedures and duty logs so separate events can be distinguished reliably.

Conference percentages are snapshots. Overlapping reports cannot be added or averaged to obtain a valid attendance rate. Duty-hour totals and elapsed shift times are checked for discrepancies, but the app does not invent a compliance decision from an incomplete log. Source-reported concerns remain separate from your CCC assessment.

## Milestone PDFs

A PDF must contain selectable text, a resident name, and explicit rows with a milestone code next to one unambiguous score. Multiple score columns for the same code, scanned pages, unlabeled number sequences, and conflicting scores block that file. The old notebooks assigned the last 21 or 22 numbers by position; this reader does not reproduce that assumption. An inferred name or an apparently partial report generates a review warning.

For an incompatible PDF, use `templates/milestones.csv`: enter one code per row, verify it against the source, and provide the resident, specialty, score, and dates. Keep missing/not-assessed scores marked `x` or `not assessed` instead of entering zero.

## Templates and fictional examples

The `templates` folder contains blank CSV headers for all seven categories and the resident roster. Use ISO dates (`2026-09-30`) in compatibility files, and full dates plus times for shifts. Add further self-assessment question triplets by increasing the question number. The evaluation compatibility template represents one evaluation per row; its `text` field holds the narrative and its `value` is normally `1`.

For an already prepared aggregate, the standardized long format can use `resident_name,date,metric,value,unit,record_mode,period_start,period_end`. Set `record_mode` to `snapshot` for a source summary rather than an individual event. For example, a cumulative procedure count must be labeled as a snapshot so it is not combined with individual procedure entries. Use the original event-level log whenever available.

The roster template uses `name, start_date, graduation_date, coach_name, status, aliases`. Only the name and training start date are required columns. Use the full name shown in New Innovations. Separate multiple aliases with semicolons. Set start and graduation dates for the resident's actual training plan. Existing roster files with an optional `external_id` column remain accepted, but leave it out for name-only exports. REDCap `record_id` and the app's internal `id` are not roster-upload columns.

Everything in `examples` is fictional and is labeled by category. `examples/milestones.csv` includes both specialties, so select Combined to use its row-level `specialty` column. The conference example needs its reporting period selected in the upload screen because the original export style has no date column. Use the examples to practise the workflow before importing real files.

If any parsing error appears, do not accept the partially extracted candidates. Correct the file, use its compatibility template, or obtain a supported source export. A preview and a successful save are separate steps.
