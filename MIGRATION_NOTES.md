# From the existing workflow to the resident dashboard

The attached material contains 17 notebooks and a seven-page populated REDCap dashboard. The evaluation and SAR notebooks each have a duplicate copy. Their source code was reviewed to identify input layouts, review fields and Word output requirements. Original New Innovations spreadsheets, resident milestone PDFs and local letter template DOCX files were not included.

## Preserved structure

| Existing concept | App representation |
| --- | --- |
| `r1my`, `r1eoy` through `r4my`, `r4eoy` | One resident plus eight dated review checkpoints. The academic year remains part of the identity. |
| Medicine and Pediatrics evaluation fields | Distinct specialty-labeled evidence records with original questions, responses, comments, evaluator, rotation and dates. |
| Resident self-assessment question triplets | Every dated form with its question text, response and comment. |
| Separate milestone code sets | Explicit Medicine or Pediatrics scores, retained by code, form, actual date and source. |
| Coach and graduation date | Resident roster fields, with a profile copy preserved at finalization. |
| Clinic counts, observed encounters, ITE and USMLE | Fields within each dated CCC review, entered by the program. |
| Scholarship, advocacy, RN/team feedback and narratives | Review fields with editable Word output. |
| Procedure logger and conference exports | Dated events and clearly identified source summaries. |
| Duty-hour logs and reported violations | Complete source entries; no inferred compliance conclusions. |
| CCC reports and resident letters | New editable DOCX layouts with optional password-to-open protection. |

## Deliberate corrections

The previous evaluation workflow produced midyear and year-end copies by changing field suffixes without consistently applying a date cutoff. The app stores an observation once and chooses it by the review's actual dates.

The old source logic could limit output to 50 evaluations, five duty-hour details, or the first two self-assessments in a year. Those caps and ordinal assignment rules are not carried forward. Exact repeated question rows are deduplicated, while distinct responses and comments are retained.

The former milestone PDF steps took the final 21 Medicine or 22 Pediatrics numeric tokens and assigned a fixed positional order. The new parser requires explicit unambiguous code/score labels. It blocks scanned, positional, conflicting or multi-column score layouts rather than silently assigning uncertain scores. Missing scores remain missing.

Some old report and letter decisions followed a numeric threshold or the midyear/year-end field suffix. The app requires an explicit CCC assessment. It does not label readiness or concerns from the checkpoint or infer promotion from a score.

The old duty tracker used gaps in shift start dates and incomplete windows to infer time off and compliance. The app keeps imported hours and source-reported concerns. It does not treat absent log entries as proof of a day off.

Punctuation-stripped or shortened resident names could collide in the legacy scripts. The app assigns an internal resident ID, prefers a native New Innovations ID, and requires confirmation for unmatched names. Aliases are explicit and persisted.

Procedure spellings and prefixes remain as exported. Similar labels are not automatically merged because they may represent different definitions. Conference snapshots keep their source dates and are not summed. Both kinds can be reviewed without losing their original wording.

## Historical migration boundary

The populated dashboard PDF can explain the old layout but cannot reconstruct all project fields, branching logic, every resident or all prior review periods. This release does not import the PDF as resident data and does not invent unseen historical records. A bulk historical migration should use the REDCap CSV and data dictionary, retaining the original record ID as provenance and explicitly matching each row to the new roster.

Original reports already available from New Innovations can be imported through the app. Prior CCC narratives can be entered into dated reviews. The actual old letter wording can be incorporated later if the referenced template DOCX files are supplied.
