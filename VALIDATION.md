# Validation record

## Yearly progress and CCC summary, version 1.1.0

On October 20, 2026 UTC, the complete suite passed on Python 3.14.8: 344 tests and eight subtests in 20.25 seconds. The 27 production Streamlit AppTests also passed separately in 8.89 seconds. All automated fixtures are fictional, and application tests block real network requests.

Yearly progress checks cover Pediatrics ITE scores and expected pass rates, Medicine ITE scores and percentiles, separate adult/pediatric clinic tallies, and four individual observed encounters. Blanks and entered zeros remain distinct. Encounters retain Not recorded, Not completed, and Completed states. Updates preserve dated prior values, and separate training-year intervals remain distinct even when their PGY and academic-year labels match. Explicitly finalized reviews freeze annual values and evidence from training start through the cutoff, alongside their existing review-period snapshots. Removal also removes the selected resident's yearly entries and history.

The Streamlit checks exercise saving partial yearly information, returning to update it, switching PGYs without copying values, viewer read-only access, and keeping the selected resident when opening Yearly progress from Resident review. Report checks verify ordinary editable Word downloads without an export passphrase, defaults from each resident's own start date through today, group downloads with individual start dates, and clearing a previous download after preparation fails. The fictional preview was also checked with ten annual entries across four residents and a preserved final annual snapshot.

The CCC summary generator was compared with the supplied report notebook and Word example. Its baseline has the example's 27 tables, section order, Arial fonts, table widths, narrow margins, header colors, signature lines, and Medicine/Pediatrics exam notation. Evaluation tables keep the original ten-column grid with date/rotation/evaluator spans and full-width narrative rows. Tests cover exact structure, no fixed evaluation limit, long narratives, zero and nonnumeric responses, completion-date cutoffs, Final snapshots, old snapshot fallback, and current evidence remaining independent of older narrative reviews. Conference values keep their source units and periods.

`Example_CCC_Summary.docx` was generated from fictional data, rendered, and visually inspected on all five pages. Section headings stay with their first content row, and small milestone matrices remain together. Medicine evaluations begin on page four and Pediatrics evaluations on page five in this sample. Longer evidence can add pages. Combined evaluations and self-assessments receive additional pages when present. Existing Final reviews without the newer cumulative or annual snapshots use only their available preserved data. A Final review missing its preserved resident profile stops with an actionable message instead of inserting current profile fields.

App sign-in, encrypted GitHub records and original files, public/private repository support, name-based matching, resident removal, and optional single-file uploads remain in place. Existing encrypted state is accepted without changing Secrets or regenerating keys. No live GitHub or Streamlit deployment was changed from this workspace.

## Resident removal and optional uploads, version 1.0.4

On October 19, 2026 UTC, the complete suite passed on Python 3.14.8: 256 tests and eight subtests in 10.55 seconds. The targeted model, upload workflow, and Streamlit checks passed separately with 92 tests in 7.54 seconds. All checks used fictional data and simulated persistence, with real network requests blocked in the application tests.

Resident removal is limited to the chosen internal resident ID. It removes the current roster entry and that resident's evidence and reviews, including contained final snapshots and prior review versions. It preserves same-name residents with different IDs, shared and excluded upload metadata, original encrypted-file references, and other residents' evidence and reviews. The input state remains unchanged until the candidate is saved. The action appends an audit event with the removed evidence and review counts.

Application checks verify the named removal confirmation, a disabled removal button before confirmation, persistence in a new session, clearing stale selections and prepared downloads, successful removal of the last resident, and safe handling of both recoverable storage failures and conflicting saves. Original uploads, older backups, repository history, and prior audit events are retained. Removal does not claim to purge historical copies.

Each of the four upload areas was tested with one file while the other three were empty. The tests use the actual parser, matching and confirmation controls, batch preparation, and dashboard persistence. AppTest cannot operate a browser's file picker, so fictional bytes are supplied at that boundary while the real upload widgets render. These checks are not a live-browser or live-GitHub acceptance test.

Later uploads preserve earlier evidence. A duplicate-only export can appear before or after a new file without blocking it. An all-duplicate or explicitly skipped batch creates no source, blob, or audit entry. Invalid parsing, unresolved residents, or unconfirmed changed evidence leave the original state unchanged. Explicit complete replacements retain replacement validation and cannot silently omit resident rows by marking them skipped. Successful saves clear the selected files, and each new preview requires fresh acknowledgement.

Additional checks cover completing a prior partial import from the identical file and reporting period after adding a previously skipped resident. The original resident's evidence and source stay unchanged, and only the missing entries are added under a fresh source reference. A subsequent complete reimport creates no save. Default model duplicate rejection and explicit replacement restrictions remain in place. The preview and save controls use the same sequential batch logic, so changed entries across two newly selected files are identified before saving and require an enabled, explicit confirmation.

The earlier name-based roster, encrypted public/private repository support, password protection, and Python 3.14 dependency correction are retained. No data migration or secrets changes are required for this update. The live GitHub repository and Streamlit deployment were not changed from this workspace.

## Resident names without source IDs, version 1.0.3

On October 18, 2026 UTC, the complete suite passed on Python 3.14.8: 221 tests and eight subtests in 11.13 seconds. These checks used synthetic data and simulated storage responses.

The roster form, default templates, examples, and resident selection menus now use names without requiring a New Innovations resident ID, per-event ID, or REDCap record number. Existing optional source references are preserved under an expandable section for older imports. The app continues to create random internal UUIDs automatically.

Four focused regressions verify name-only matching, normalized names and saved aliases, explicit resolution of duplicate names, and evidence/review continuity when a resident's name is edited while keeping the same internal UUID. A name-only end-to-end check reads all eight example exports and saves 15 evidence records, including three logical evaluations with five question responses and two identical same-day procedure entries retained separately. Reimport checks recognize the saved records.

The targeted model/importer checks passed with 68 tests and eight subtests. All 13 Streamlit AppTests also passed, including saving a resident without entering a source ID and reopening that same resident with its automatic internal reference intact. An ambiguous roster CSV is stopped for review rather than creating another resident. The public/private repository support and Python 3.14 dependency correction are retained.

## Public repository support, version 1.0.2

On October 18, 2026 UTC, all 217 automated tests passed in 14.78 seconds on Python 3.14.8. The focused security and storage suite also passed separately with 108 tests in 2.58 seconds.

Public and private repositories are both accepted automatically. The updated checks cover confirmed missing dashboard files, encrypted dashboard save/load, encrypted original-file round trips, opaque source filenames, generic commit messages, and refusal of stale SHA updates in both visibility modes. Missing or nonboolean repository visibility metadata and a mismatched repository identity are rejected before reading or writing application files.

The application still encrypts every dashboard and original-file payload before a GitHub write. Key and token validation, immutable branch reads, file integrity verification, save conflict handling, and app authentication remain in place. The setup screen now describes the connected encrypted storage without claiming the repository is private.

These checks use synthetic data and simulated GitHub responses. The user's live GitHub repository and Streamlit deployment were not changed from this workspace. The earlier Python 3.14 dependency correction is included in this release.

## Python 3.14 deployment fix, version 1.0.1

On October 18, 2026 UTC, a fresh isolated CPython 3.14.8 environment installed all 59 production and test packages using binary wheels only. Dependency resolution took 2.51 seconds, preparation took 6.37 seconds, and installation took 80 milliseconds in this workspace. These timings describe this test environment, not a promised Streamlit deployment time.

The sole dependency change was `pandas==2.2.3` to `pandas==2.3.3`, the first pandas release with general Python 3.14 compatibility. All 183 automated tests then passed on Python 3.14.8 in 18.04 seconds. This verifies installation and application behavior for the updated dependency set, including the dashboard, file imports, encrypted storage logic, and protected Word exports.

The user-provided deployment log stopped after dependency resolution and contained no final exception. The old pandas pin is a confirmed Python 3.14 compatibility problem, but the excerpt cannot establish that it was the only cause of the observed delay. The live repository and Streamlit deployment were not changed by these local checks.

Official compatibility reference: https://pandas.pydata.org/docs/whatsnew/v2.3.3.html

## Original version 1.0.0 result

**183 automated tests passed on October 17, 2026**, using Python 3.12 and the pinned dependencies in this package. The final run completed in 33.34 seconds. An additional run of all 13 Streamlit AppTests passed after adding explicit assertions that login and export passphrase fields clear after successful or failed actions.

This is a working first implementation prepared for a test deployment. These checks establish behavior with synthetic data and simulated storage responses. They do not establish acceptance of every current New Innovations export or certification of a hosting environment.

## Automated coverage

| Area | Checks performed |
| --- | --- |
| Importers | Structured evaluation and self-assessment responses, source identity, different specialty labels, spreadsheet/HTML/tabular inputs, percent and zero values, milestone label ambiguity, malformed and oversized files, and category-specific output. |
| Resident model | Exact and alias matching, ambiguous names, duplicates and corrections, native event IDs, source exclusion, evidence periods, complete snapshot coverage, repeated training years, final review snapshots, and review reopening/version history. |
| Encrypted storage | Authenticated encryption, key compatibility, bounded compressed state, invalid or truncated inputs, repository identity/branch verification, immutable reads, content hashes, explicit missing-file handling, SHA conflicts, ambiguous saves, and immutable original-file retries. |
| Access and passwords | Argon2id password verification, account configuration, session expiry, role restrictions, failed-login handling, attempt limits, and clearing password input state. |
| Word reports | CCC template dimensions, fonts, colors, column spans, section order, and separate Medicine/Pediatrics page starts; dated yearly values and cumulative evidence; retained narrative details, final snapshots, prior versions, invalid ownership, and unassessed or zero values. |
| Yearly progress | Optional exam/clinic values, valid percentages and whole-number tallies, separate encounter states, dated updates, repeated training-year intervals, historical report cutoffs, frozen final snapshots, and resident removal. |
| Legacy Word-protection utility | Its password-to-open roundtrip, wrong-password, integrity, and validation tests remain. Version 1.1.0 downloads ordinary Word files and does not call this optional utility. |
| Streamlit app | Production configuration and login gates, fictional preview, role-aware navigation, review persistence, duplicate review protection, snapshot export, failed save feedback, successful exports, and invalidation of old downloads after export failure. |

The eight fictional evidence CSV files were also run through parsing, resident matching, application import, and state validation together: all succeeded, producing 15 evidence records. The fictional roster provides the matching resident.

## Document layout inspection

The two supplied sample documents contain only fictional resident information. They were generated by the report module, rendered, and inspected on every page:

- `Example_Resident_Progress_Report.docx`: six pages, including the assessment, specialty-specific evidence, and detailed feedback.
- `Example_CCC_Letter.docx`: one page.

All supplied samples and app downloads are ordinary editable Word documents. Version 1.1.0 removes the export-passphrase step at the user's request. App sign-in and encrypted GitHub records remain in place.

## Scope of source review

The supplied Jupyter notebooks were inspected as source code; they were not executed. Their original data and outputs were not included in the application package. The supplied REDCap PDF was used to identify review fields and layout intent. It was not treated as a complete historical data export or data dictionary.

No real resident information is included in the example files, automated fixtures, or demonstration mode.

## Remaining deployment acceptance checks

The following require the intended accounts, installed software, or current exports and were not performed in this workspace:

1. Live writes and recovery against the program's configured GitHub repository.
2. A deployed Streamlit session using the institution's actual sharing and access settings.
3. Browser interaction and concurrent saves using two actual staff accounts. AppTests exercise Streamlit through its testing API, not a browser.
4. Comparison against a current deidentified example of every New Innovations report layout the program intends to upload, especially each specialty's milestone PDF.
5. Opening the CCC summary, detailed report, and letter in the team's actual Microsoft Word version. Table layout was rendered and inspected locally, but pagination can vary with the evidence length and Word installation.
6. Historical REDCap migration. A CSV export and matching data dictionary are needed to map prior residents and reviews explicitly.
7. Institutional review of hosting, access, retention, and backup arrangements for the information involved.

The README contains a short acceptance sequence for completing these checks with fictional and deidentified data before regular use.

## Reproduce automated checks

From the project directory, in a Python 3.14 virtual environment:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The tests use no live GitHub credentials and do not require a deployed app.
