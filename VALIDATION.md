# Package validation

## Current update

Version 1.3.1, packaged November 27, 2026, was checked with Python 3.12 and the pinned application dependencies. The version label identifies this complete bundle and does not change the stored data schema.

The focused internal suite passed 315 tests plus 8 subtests. It covered PDF conversion, preview navigation and session isolation, upload parsing, the Pediatrics crosswalk, the combined EPA Tracker scores/comments report, resident matching and overrides, checkpoint identity, duplicate handling, report generation and upload batching.

This is a complete deployment bundle. Internal test files and development-only fixtures are not included in the ZIP.

The final staged package passed import checks for all 15 application modules and production and demo startup through Streamlit AppTest. The milestone/comment parser, Word builder and PDF converter are unchanged from version 1.3.0, which also passed a real five-page fictional CCC Word-to-PDF conversion using the packaged file layout.

## Resident-name override checks

The exact resident matcher is unchanged. Middle-name suggestions require confirmation and retain explicit surname boundaries. Focused cases cover additional middle names, compatible initials, ambiguous candidates, aliases, compound surnames, accents, credentials and conflicting identifiers. Source names that cannot be suggested remain accessible through a manual roster selector.

Real-PDF Streamlit tests confirmed that a selected name maps both milestone scores and evaluation comments to the same existing resident. A saved alias matches in a new session, while an unchecked Remember option leaves the roster aliases unchanged. A skipped-name override survives reruns.

The upload preview rejects a proposed alias that belongs to another resident. A failed persistence attempt leaves both evidence and aliases unchanged in the saved state, and retry commits them together. An alias-only save for duplicate evidence preserves every existing record, source and upload timestamp.

## Combined competency report checks

Both supplied EPA Tracker Competency scores and rotation comments PDFs were checked through parsing, resident matching, saving, the existing Word evaluation tables and production PDF conversion.

| Sample layout | Milestone values | Evaluations | Written comments |
| --- | ---: | ---: | ---: |
| Explicit no-matching-evaluations report | 22 | 0 | 0 |
| Report with comments across five pages | 22 | 7 | 29 |

Every supplied written comment reached the Word evaluation section under the existing name-cleanup rules. The current midyear table retained all 22 milestone values. Evaluator, date and rotation headers remained in their usual layout. Question prompts, recommendation ranges, filenames and evaluation references were absent from the Word comment text.

The two generated Word reports converted successfully to PDF. The comment table was visually checked for clipping and missing rows. Existing empty specialty placeholder pages remain when that specialty has no comments in the supplied evidence.

The Streamlit checks exercised the real milestone upload area with a selectable fictional PDF. They confirmed mixed-category saving and history, score-only import, roster exclusion, duplicate reupload, and adding previously omitted comments without repeating milestone scores. Parser tests cover wrapped prompts, comments continuing across pages, invalid metadata and date rejection, explicit missing-comments messages, distinct milestone checkpoint identity and repeated evaluation references across changed PDF windows.

The PDF and TAB formats can use different evaluation identifiers. Their comments are not fuzzy-merged. The optional comment checkbox supports a scores-only import when the underlying evaluations were already imported from TAB files.

## Milestone checks

Both supplied General Pediatrics EPAs and Milestones Crosswalk PDFs were checked through parsing, resident matching, import and Word report generation. Each produced all 22 expected milestone values.

The checks confirmed:

- First-page resident name, PGY and season recognition.
- The highest endpoint of each printed recommendation range.
- Preservation of standalone scores, including zero.
- Exclusion of later EPA-input pages from milestone scoring.
- Placement of FALL PGY2 values in the PGY2 MY Word row.
- A point snapshot using the selected fallback end date when no calendar date is printed.
- Separate checkpoint identities for different PGYs and seasons.
- Duplicate detection and report-date cutoffs.

The prior notebook layout was preserved. Current-year midyear scores appear in the milestone table. For later-year reports, prior-year midyear values follow the existing notebook behavior and can remain in narrative notes when only prior-year EOY table rows are displayed.

## PDF checks

The converter now explicitly uses LibreOffice's server renderer. It removes inherited display settings from the conversion child process and uses a private temporary directory and profile.

Real Word-to-PDF conversion succeeded for representative CCC reports, including the sample-derived milestone reports. The milestone tables were visually inspected for alignment and clipping. A full fictional CCC report also converted with separate Medicine, Pediatrics and selected-comment pages.

Focused conversion tests covered missing software, invalid input or output, timeout, process failure, temporary-directory cleanup and fixed privacy-safe error codes. Raw converter diagnostics are not displayed to the user.

Preview checks confirmed that the same generated Word bytes are converted, prepared files stay in the signed-in session, and relevant selection or state changes invalidate an old preview.

## Historical test expectations

Older development tests were recovered from previous app versions. Some still expect the earlier 27-table layout, question wording in comments, older annual-field layouts or an option to retain whole original exports.

Those expectations conflict with later requested behavior. The same old report-test failures were reproduced against the unchanged restored baseline. They were not treated as new failures caused by this update, and the current verification does not claim that every historical test passes.

The current package keeps the requested notebook layout and later comment-cleanup behavior.

## Deployment limits

These checks were performed locally with fictional test fixtures, supplied source-format samples and simulated storage where needed. No live GitHub repository or Streamlit deployment was changed.

The hosted app still needs the complete package, its existing Secrets and the required system packages. A local conversion success does not establish that the hosted machine has finished installing LibreOffice.

After updating the hosted app, confirm that the existing roster loads, a generated Word report can be previewed as PDF, and a new crosswalk import displays the expected milestone values. Use the new PDF reference code if conversion fails.

Encryption and password features do not by themselves establish approval of a particular hosting environment. The package preserves the existing application access and encrypted-storage workflow.

