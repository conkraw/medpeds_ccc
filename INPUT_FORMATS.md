# Upload formats

Use supported original New Innovations exports directly. One file is enough. Select the appropriate upload area, process the file, review the matched residents and save the results.

Residents are matched by name or a saved alias. You do not need to create a New Innovations resident ID or a REDCap record ID. Existing optional source IDs are recognized where available.

## File types and limits

The app supports CSV, TSV, TAB, XLSX, binary XLS, and New Innovations HTML tables saved with an XLS extension. TAB exports can be UTF-16. Text-based milestone PDFs are also supported.

Each file can be up to 15 MB. A batch can contain up to 100 files and 60 MB total. Tabular limits are 100,000 data rows, 400 columns and 30 sheets/tables. Expanded XLSX content is limited to 100 MB. Milestone PDFs may have up to 150 pages.

Excel formulas are not executed. The reader uses saved cell values. Check warnings when a workbook contains formulas.

## Supported report categories

| Report | Recognized source fields or layout |
| --- | --- |
| Evaluations | Last Name, First Name, Subject Status, Evaluator, Start Date, Subject Rotation, Session Name, Question, Response, Comment and Overall Comments, with native evaluation IDs when available. |
| Older processed evaluations | Subject Name, Session Date, Session, Evaluator Name, Overall Comments and available question fields. |
| Self-assessments | Subject Name, source dates, Date Completed, evaluator/session fields and numbered Question, Response and Comment triplets. |
| Milestone spreadsheets | resident_name, milestone, value, specialty and a date or report period, with training_year when supplied. The notebook's all_resident_milestones.csv is supported. |
| New Innovations milestone PDFs | Labeled Program Subcompetency for Residents grids with the resident, specialty, training year and report dates. |
| Pediatrics crosswalk PDFs | General Pediatrics EPAs and Milestones Crosswalk, with the name, FALL/SPRING, PGY and recommendations table on page one. |
| Procedures | ResidentLastName, ResidentFirstName, DatePerformed, ResidentStatus and Procedure, with optional source identifiers. |
| Conferences | Person, Category, Status and % Attended, with the actual reporting period. |
| Duty hours | Name, Start, End, Hours and Work Type, or the supported hierarchical report with PGY/resident headings. |
| Duty-hour concerns | Name, Status, Log Date, Rule, Description, Comments, Rotation and Work Type. |

Headers are normalized for capitalization, surrounding spaces and common punctuation differences. The first 40 rows are checked for a tabular header.

## Resident filtering and duplicate handling

Evaluation and milestone imports save only entries matched to the Administration roster. Other residents from those exports are ignored. If a roster resident has a different spelling in an export, add that spelling under Other names used in exports.

Exact saved entries are skipped. Reuploading a file can add a resident whose rows were previously omitted, without adding the already saved records again. Changed evidence is identified for review.

Question-per-row evaluation files are grouped into logical evaluations using a native evaluation ID when available, plus resident and source context. Without an ID, the reader uses form, evaluator, rotation and dates and displays a warning. Distinct question responses and comments remain preserved. Identical repeated question rows within one evaluation are removed.

A file with parsing errors cannot be saved. Other readable files in the selected batch can still be processed.

## Dates and specialties

Completion dates are used when available, followed by the relevant source event or reporting date. Source rotation dates are retained. A report cutoff determines which dated evidence is available to the committee.

Fallback dates apply only when required dates are absent from the source. They do not limit historical imports. If neither the source nor the upload supplies a valid date, the file cannot be accepted.

Use the appropriate Medicine or Pediatrics upload area. For mixed tabular files, recognized specialty fields can identify each row. In the CCC evaluation summaries, PEDS/pediatric rotations are placed in Pediatrics and other named rotations in Medicine according to the app's existing classification rules.

Medicine and Pediatrics milestone code sets remain separate.

## New Innovations milestone grids

The PDF must contain selectable text and a supported labeled milestone grid. The reader pairs each code with the score beneath its label. It checks the selected specialty and expected number of codes.

The resident's printed training year and report dates are retained. A printed generation date can identify a snapshot available before the configured annual period ends, allowing current midyear scores to appear without waiting until the following June.

Unassessed values remain unassessed. Zero is a score. Ambiguous names, conflicting cells, missing codes or invalid values require correction before import.

## Pediatrics EPA-to-milestone crosswalk

For the General Pediatrics EPAs and Milestones Crosswalk format, the reader:

- Reads the resident name, season and PGY from page one.
- Reads only that page's recommendations table.
- Saves the largest endpoint of an explicit range, so 2–3.5 becomes 3.5.
- Preserves standalone values, including zero.
- Assigns FALL to Midyear and SPRING to Year end for the printed PGY.
- Checks all 22 labeled milestones before accepting the file.

The later explanation and EPA-input pages do not supply milestone scores.

These PDFs have no printed calendar date. Set Fallback period ends to the assessment date you want assigned. The date defaults to today. Use the actual historical date you intend for an older assessment.

Each crosswalk is stored as a snapshot on that selected end date, with an explicit fallback marker. The original selected upload range is retained only as provenance. A cohort-wide fallback start date therefore does not exclude a younger resident's milestone scores from the report.

A current FALL PGY2 crosswalk appears in the PGY2 MY milestone row. Finalized reviews retain the snapshots saved when they were finalized.

## Other milestone PDFs

A text-based PDF with an identifiable resident and explicit code/scalar rows can also be read. Unsupported layouts, scanned images and ambiguous number sequences need a structured milestone spreadsheet.

Use templates/milestones.csv when a source cannot be read reliably. Enter one code per row with the resident, specialty, score and dates. Use x or not assessed for an unassessed value. Do not enter zero to represent missing information.

## Templates and examples

templates/ contains blank CSV headers for the report categories and roster. examples/ contains fictional practice data using Alex Example.

Use ISO dates such as 2026-09-30. Duty shifts should include complete dates and times. Additional self-assessment question triplets can be added by increasing their question number.

The roster template requires name and start_date. Optional columns include graduation_date, coach_name, status and aliases. Separate multiple aliases with a semicolon. Existing optional external_id columns remain supported.

The standardized long format can represent a source summary with record_mode set to snapshot and its actual period_start and period_end. Conference percentages and cumulative counts retain that coverage. They are not summed with overlapping snapshots or prorated into smaller report windows.

## Yearly entries and report output

Enter exam results, clinic totals and observed encounters directly in Yearly progress. Set Progress recorded through to the date represented by those values. Blank remains unrecorded and zero remains an entered value.

The CCC summary uses the notebook-based tables. Medicine and Pediatrics evaluation summaries start on separate pages. Evaluation comments omit question prompts and Comment prefixes. Resident-on-resident evaluator names are suppressed for the supported peer evaluation session names.

The Evaluation flagger lets you select comments for a separate page after the specialty summaries. That page includes the evaluator, specialty designation and selected text without the triggering scores.

Current reports default from the resident's start date through today. Saved reviews use their own period, and Final reviews use their preserved data. Word files are ordinary editable DOCX files. Report preview converts the generated Word file to PDF.

