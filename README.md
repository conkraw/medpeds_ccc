# Med-Peds CCC dashboard

Complete package version 1.4.0, November 27, 2026.

This complete deployment package includes the resident dashboard, encrypted GitHub storage, Word reports, PDF previews, evaluation comment selection, and the updated Pediatrics milestone importer. EPA Tracker reports titled Competency scores and rotation comments can now supply both scores and written evaluation comments through the milestone upload area.

## Updating your existing app

Start with START_HERE.txt. Upload this package's files into the existing app repository, preserving their folders. Deploy app.py.

Keep your current Streamlit Secrets, GitHub data repository, branch, data path, encryption key and password hashes unchanged. Existing encrypted records remain in that data repository. Do not run the first-time setup helper or create an empty dashboard when updating an app that already has saved records.

The package contains application code and fictional examples. It does not contain your resident data or account credentials.

After committing the files, reboot the Streamlit app and refresh your browser. The first restart may take longer if LibreOffice Writer must be installed from packages.txt.

## Screens and workflow

| Screen | Purpose |
| --- | --- |
| Program dashboard | Review the resident roster, evidence counts and latest evaluation updates. |
| Upload files | Add whichever Medicine, Pediatrics or administrative reports are available, then process and save the matched entries. |
| Yearly progress | Enter midyear and end-of-year review notes, exam results, clinic patient totals and observed encounters for PGY1 through PGY4. |
| Resident review | Review evidence and milestone history, then document a dated CCC checkpoint. |
| Evaluation flagger | Review low ratings and choose written comments for the CCC summary. |
| Word reports | Prepare a CCC summary, progress report or CCC letter for one resident or a group. |
| Report preview | View a generated Word report as a PDF and download either format. |
| Administration | Manage residents and aliases, exclude incorrect uploads, back up or restore encrypted data, and download templates. |

One upload is enough. Leave the other upload areas empty. Readable files can be processed even when another selected file cannot be read.

Only evaluation and milestone entries assigned to your Administration roster are saved. You do not need to create a New Innovations ID or REDCap record ID.

If a report uses a middle name or another spelling, choose the existing resident in the upload preview. Possible middle-name matches are suggested for confirmation. Other unrecognized names start as skipped and can be assigned under Match or override skipped names. Leave entries from other programs skipped.

Leave Remember confirmed name variations for future uploads checked to save the selected source spelling with the resident. Later uploads match that spelling automatically. This keeps the resident's display name and existing records in place. If the evidence is already saved, Save name matches can remember a new spelling without adding duplicate evidence or changing upload timestamps. A name already assigned to another resident must be corrected before it can be remembered.

You can also manage saved spellings under Other names used in exports in Administration.

The app detects duplicate entries. Later uploads add new evidence, including historical exports. Reuploading a previously partial file can add newly matched residents without adding the saved entries again. Confirm changed evidence when the preview asks.

Up to 100 files and 60 MB total can be processed in a batch, with a 15 MB limit for each file. The app saves matched parsed records through encrypted storage. It does not retain whole original exports from new uploads.

Read INPUT_FORMATS.md for supported inputs and date handling.

## Yearly progress and CCC reviews

Choose the resident and residency year, then choose Midyear or End of year. Enter Overall impression, Areas for growth, Patterns over time and Med-Peds CCC review. Select Save review notes to keep the changes. Each resident, residency year, academic year and checkpoint has its own notes.

These four Word sections contain only the corresponding saved text. A section remains blank until text is entered and saved. Clearing a field and saving makes that section blank again. Milestone scores and source details are no longer added automatically to these boxes. Scores and evaluation comments remain in their existing report tables.

The notes use the same saved review as Resident review, so there is no second copy to maintain. Existing review fields and finalized versions are retained. Finalized notes are read-only until an administrator uses the existing reopen action in Review history.

To prepare a specific review for the resident, open Word reports and choose its PGY, Midyear or Year end, and academic year under Use evidence from. Current dashboard reports include notes only from the matching checkpoint through the report cutoff. A new checkpoint starts blank.

Review dates can be adjusted in the notes form. For a new review, the default evidence cutoff is today or the checkpoint end, whichever comes first within the selected training year. Saving notes does not change the yearly scores below them.

Yearly progress contains Pediatrics ITE score and expected pass rate, Medicine ITE score and percentile, adult and pediatric clinic patient totals, and four observed encounter statuses per residency year.

Set Progress recorded through to the date represented by those values. Blank and zero have different meanings. Year-to-date clinic totals are dated totals, not additions to prior totals. Prior versions remain available for historical report cutoffs. Training-year dates can be adjusted for an off-cycle or repeated year.

CCC reviews have PGY1 through PGY4 Midyear and Year end checkpoints. Midyear defaults to six months and Year end to the full training year. Review dates and coverage can be adjusted. Save draft persists work in progress. Finalize review preserves the evidence, resident profile and yearly values used at that time. Later imports do not alter a finalized snapshot.

Removing a resident deletes their current roster entry, evidence, yearly progress and reviews. Existing backups, audit events and repository history can still contain prior versions. Use Inactive when you want to retain accessible history.

## Milestones

Medicine and Pediatrics have separate milestone tables.

The New Innovations grid reader matches labeled table cells to their scores and retains the printed report period and generation date when available. Scores available at midyear can appear in a current midyear summary.

The General Pediatrics EPAs and Milestones Crosswalk reader uses page one. It reads the resident name, printed PGY and FALL or SPRING, then reads the recommendations table. A range such as 2–3.5 is saved as 3.5. Single values, including zero, are preserved. FALL maps to Midyear and SPRING to Year end.

These crosswalk PDFs do not print a calendar date. Under Dates for reports without dates, set Fallback period ends to the assessment date you want assigned. It defaults to today. Use the appropriate historical date for an older report. The record is a snapshot on that date, and the original upload range is retained as provenance.

### EPA Tracker scores and comments PDFs

Open Upload files, expand Milestone reports, and choose Add Pediatrics milestone files. Upload the PDF, process it, review the resident match and save.

The importer reads the recommendation table on page one and the Comments by rotation section across all pages. Each evaluation retains its evaluator, rotation, form and printed completion date. A labeled session date is used only when no completion date is available. The existing Word evaluation summaries show the written comments in their usual evaluator/comment layout, without question wording, score ranges, source filenames or evaluation references.

The comments' date range is not treated as the assessment date for the recommendation scores. Set Fallback period ends for those scores as described above. FALL and SPRING still select the printed PGY's Midyear and Year end milestone checkpoints.

Leave Include rotation comments from EPA Tracker PDFs checked to import both parts. Clear it to import scores only if those same evaluations are already saved from TAB files. PDF evaluation references and New Innovations identifiers can differ, so a PDF and a TAB file are not automatically treated as the same source evaluation. Repeated PDF evaluations with the same printed reference and content are skipped, including when a later PDF covers a different comment window.

If the source report says no saved evaluations match the resident, there are no comments to extract. Its milestone scores can still be saved. Correct the resident link in EPA Tracker and export a new PDF to add the missing comments.

## Evaluation flagger

An administrator can set separate cutoffs for competency/other ratings and EPA ratings. A valid rating at or below its cutoff is flagged. Missing ratings and administrative counts are ignored.

Reviewers and administrators can review comments and save the ones to include. The CCC summary places selected comments on their own page after the Medicine and Pediatrics evaluation summaries. That page contains the evaluator, specialty designation and selected comment text, without the triggering scores. Peer evaluator names remain suppressed.

## Word reports and PDF previews

CCC summary uses the notebook-based tables and separate Medicine and Pediatrics evaluation pages. It includes the imported milestones, yearly progress and saved CCC narrative. Evaluation comments are cleaned of question prompts and Comment prefixes. Resident-on-resident evaluator names are suppressed.

For current evidence, the default report range is the resident's start date through today. Group reports use each resident's own start date unless you choose a shared start. Saved reviews use their own reporting period. Final reviews use their preserved snapshots.

Word downloads are ordinary editable DOCX files. No export passphrase is required.

Use Preview Word reports as PDF in the sidebar, or Open PDF preview on Word reports. Choose the resident, document and date range, then select Prepare PDF preview. Conversion runs on the app server using LibreOffice Writer. The resulting PDF reflects the generated Word document. Word and PDF downloads are offered together.

Conversion uses private temporary files that are removed afterward. Prepared documents stay in the signed-in session and are cleared when the relevant context changes or the user signs out. They are not saved to GitHub by the preview feature.

If conversion fails, the Word download remains available. Read the PDF reference code in the error message. Confirm packages.txt is beside app.py, then reboot after installing the update.

## First-time installation only

Skip this section when updating your existing app.

### Repository and hosting

Place the complete project contents in the code repository. Keep ccc/, templates/ and .streamlit/config.toml in their supplied locations. requirements.txt installs Python dependencies. packages.txt installs LibreOffice Writer and the supplied font package.

Use app.py as the Streamlit entry point. Python 3.12 was used for the current package checks. demo_app.py is an optional fictional preview with no uploads or saving.

Code and encrypted data can use the same repository or separate repositories. Public and private data repositories are supported. Initialize the chosen data branch before connecting the app.

A fine-grained GitHub token needs Contents read and write access to the configured data repository. Keep the token and encryption key in Streamlit Secrets. Upload resident files through the app.

### Create credentials for a new installation

In a local terminal:

~~~bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/generate_secrets.py
~~~

On Windows, use py -3.12 to create the environment and .venv\Scripts\python for the remaining commands.

Choose setup to generate the first administrator account and encryption key. Password entry is hidden. The helper prints a TOML configuration and does not write a secrets file or request a GitHub token.

Copy the output into the app's Streamlit Secrets settings and fill in the repository and token. The following values are placeholders:

~~~toml
[app]
session_timeout_minutes = 30
max_session_hours = 8

[github]
owner = "YOUR_GITHUB_OWNER"
repo = "YOUR_DATA_REPOSITORY"
branch = "main"
path = "encrypted/ccc.enc"
token = "YOUR_FINE_GRAINED_GITHUB_TOKEN"

[encryption]
key = "KEY_FROM_THE_LOCAL_SETUP_HELPER"
previous_keys = []

[users.coordinator]
display_name = "Program Coordinator"
role = "admin"
password_hash = "ARGON2ID_HASH_FROM_THE_LOCAL_SETUP_HELPER"
enabled = true
~~~

The repo setting also accepts OWNER/REPOSITORY or a GitHub repository URL. Keep a secure backup of the encryption key. A new key cannot decrypt data encrypted with a different key unless the old key is retained in the supported key configuration.

For a genuinely new installation, sign in and create an empty dashboard only after confirming that no existing data should be present at the configured path.

To add another account, run the helper in user mode. That mode generates an account block without changing the encryption key.

| Role | Access |
| --- | --- |
| Admin | View and export, upload, manage residents and settings, update yearly progress, select comments, manage reviews, and back up or restore data. |
| Reviewer | View and export, update yearly progress, select comments, and create or finalize reviews. |
| Viewer | View dashboards and export reports. |

Set enabled = false in an account's Secrets configuration to disable it.

## Saving and recovery

Resident records, names, comments, settings, aliases, reviews and audit events are encrypted before persistence. The app accepts both public and private GitHub repositories. The encryption key and GitHub token stay outside the repository.

Saves use the loaded GitHub file version. If another user saved first, the app refuses to overwrite that newer version. Failed saves display an unsaved status and preserve the last confirmed state. Recovery controls can provide an encrypted copy of pending changes.

Dashboard backups include the saved parsed records and reviews. Older versions of the app may have stored original uploads as separate encrypted objects. Those historical objects are not included in a dashboard backup. Preserve the data repository when they are needed.

This complete deployment bundle does not perform a data migration or change a live GitHub or Streamlit deployment. See VALIDATION.md for the checks performed before packaging.

