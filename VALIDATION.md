# Validation record

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
| Word reports | All evidence categories, retained narrative details, separate specialties, assessment fields, explicit CCC decisions, final snapshots, prior versions, invalid ownership, and unassessed or zero values. |
| Word protection | Password-to-open roundtrip, wrong passwords, integrity failures, exact recovered document bytes, passphrase validation, and fail-closed behavior. |
| Streamlit app | Production configuration and login gates, fictional preview, role-aware navigation, review persistence, duplicate review protection, snapshot export, failed save feedback, successful exports, and invalidation of old downloads after export failure. |

The eight fictional evidence CSV files were also run through parsing, resident matching, application import, and state validation together: all succeeded, producing 15 evidence records. The fictional roster provides the matching resident.

## Document layout inspection

The two supplied sample documents contain only fictional resident information. They were generated by the report module, rendered, and inspected on every page:

- `Example_Resident_Progress_Report.docx`: six pages, including the assessment, specialty-specific evidence, and detailed feedback.
- `Example_CCC_Letter.docx`: one page.

The supplied samples are ordinary editable Word documents so their layouts can be reviewed immediately. The app defaults to password-protected exports when it is used with resident data.

## Scope of source review

The supplied Jupyter notebooks were inspected as source code; they were not executed. Their original data and outputs were not included in the application package. The supplied REDCap PDF was used to identify review fields and layout intent. It was not treated as a complete historical data export or data dictionary.

No real resident information is included in the example files, automated fixtures, or demonstration mode.

## Remaining deployment acceptance checks

The following require the intended accounts, installed software, or current exports and were not performed in this workspace:

1. Live writes and recovery against the program's configured GitHub repository.
2. A deployed Streamlit session using the institution's actual sharing and access settings.
3. Browser interaction and concurrent saves using two actual staff accounts. AppTests exercise Streamlit through its testing API, not a browser.
4. Comparison against a current deidentified example of every New Innovations report layout the program intends to upload, especially each specialty's milestone PDF.
5. Opening a password-protected report and letter in the team's actual Microsoft Word version. The encryption library labels OOXML encryption experimental; automated integrity and decryption checks do not replace this compatibility check.
6. Historical REDCap migration. A CSV export and matching data dictionary are needed to map prior residents and reviews explicitly.
7. Institutional review of hosting, access, retention, and backup arrangements for the information involved.

The README contains a short acceptance sequence for completing these checks with fictional and deidentified data before regular use.

## Reproduce automated checks

From the project directory, in a Python 3.12 virtual environment:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The tests use no live GitHub credentials and do not require a deployed app.
