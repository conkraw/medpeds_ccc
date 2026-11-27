# Package validation

## Current update

Version 1.4.0, packaged November 27, 2026, was checked with Python 3.12 and the pinned application dependencies. The version label identifies this complete bundle and does not change the stored data schema.

Verification passed 436 focused tests plus 8 subtests, including the corrected historical-milestone assertion that previously expected automatic prose in the CCC review box. It covered the four manual review fields, separate checkpoint selection, persistence and reporting, along with PDF conversion, preview navigation, upload parsing, resident matching, duplicate handling and upload batching.

This is a complete deployment bundle. Internal test files and development-only fixtures are not included in the ZIP.

The original notebook Word layout, milestone/comment parser and PDF converter are unchanged. The CCC adapter now fills the four review boxes from the saved fields for the matching checkpoint.

## Manual review note checks

The helper and Streamlit checks cover blank defaults, four-field saving and reload, explicit clearing, and separate resident, PGY, academic-year and Midyear or Year end entries. Updating notes preserves other review fields, annual values and metadata. Finalized reviews remain read-only and prior snapshots survive reopening.

Report checks confirm that imported milestones do not populate the review boxes, only the corresponding saved text appears, and an earlier checkpoint cannot fill a later blank one. Explicit saved reviews use their own fields and frozen evidence. Existing manual legacy text is used only when it belongs to the matching review, with explicitly cleared modern fields taking precedence.

Failed saves retain the pending notes for retry. Successful saves invalidate older generated Word downloads and PDF previews. Viewer accounts cannot edit notes.

Two complete fictional CCC summaries were generated, one with imported milestones and blank review fields and one after saving all four notes. Both converted through the production PDF converter and rendered as five-page reports with 26 tables. Visual inspection confirmed blank or correctly filled narrative boxes, unchanged milestone matrices and specialty evaluation pages, and no clipping or overlap.

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

The prior notebook layout was preserved. Current-year midyear scores appear in the milestone table. For later-year reports, the notebook retains its existing selection of milestone rows. Additional historical values remain available in the resident evidence history and are no longer appended to the manual CCC review box.

## PDF checks

The converter now explicitly uses LibreOffice's server renderer. It removes inherited display settings from the conversion child process and uses a private temporary directory and profile.

Real Word-to-PDF conversion succeeded for representative CCC reports, including the sample-derived milestone reports. The milestone tables were visually inspected for alignment and clipping. A full fictional CCC report also converted with separate Medicine, Pediatrics and selected-comment pages.

Focused conversion tests covered missing software, invalid input or output, timeout, process failure, temporary-directory cleanup and fixed privacy-safe error codes. Raw converter diagnostics are not displayed to the user.

Preview checks confirmed that the same generated Word bytes are converted, prepared files stay in the signed-in session, and relevant selection or state changes invalidate an old preview.

## Historical test expectations

Older development tests were recovered from previous app versions. Some still expect the earlier 27-table layout, question wording in comments, older annual-field layouts or an option to retain whole original exports.

Those expectations conflict with later requested behavior. Ten old report-test failures were reproduced against the unchanged 1.3.1 baseline. Two further old assertions expect automatic historical notes in the CCC review box or PGY1 narrative copied into PGY3. Those expectations conflict with the current request. The current verification does not claim that every historical test passes.

The current package keeps the requested notebook layout and later comment-cleanup behavior.

## Deployment limits

These checks were performed locally with fictional test fixtures, supplied source-format samples and simulated storage where needed. No live GitHub repository or Streamlit deployment was changed.

The hosted app still needs the complete package, its existing Secrets and the required system packages. A local conversion success does not establish that the hosted machine has finished installing LibreOffice.

After updating the hosted app, confirm that the existing roster loads, a generated Word report can be previewed as PDF, and a new crosswalk import displays the expected milestone values. Use the new PDF reference code if conversion fails.

Encryption and password features do not by themselves establish approval of a particular hosting environment. The package preserves the existing application access and encrypted-storage workflow.

