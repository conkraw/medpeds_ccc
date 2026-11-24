# Package validation

## Current update

Version 1.2.0, packaged November 23, 2026, was checked with Python 3.12 and the pinned application dependencies. The version label identifies this complete bundle and does not change the stored data schema.

The focused internal suite passed 210 tests plus 8 subtests. It covered PDF conversion, preview navigation and session isolation, upload parsing, the new Pediatrics crosswalk, resident matching, checkpoint identity, duplicate handling, report generation and upload batching.

This is a complete deployment bundle. Internal test files and development-only fixtures are not included in the ZIP.

The final staged package passed import checks for all 15 application modules, production and demo startup through Streamlit AppTest, and a real four-page CCC Word-to-PDF conversion. These checks used the packaged file layout.

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

