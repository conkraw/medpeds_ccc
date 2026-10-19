# Med Peds CCC Streamlit App

This app replaces the repeated REDCap dashboards and Jupyter conversion steps with one resident record across a four-year Medicine-Pediatrics program. Administrators upload New Innovations reports, check the preview, and save. Reviewers read the evidence, document the CCC assessment, and download editable Word reports or resident letters.

The implementation is ready for a test deployment. It has been tested with fictional data and the input structures described in the supplied notebooks. Current raw New Innovations exports and the target GitHub/Streamlit accounts were not available for live acceptance testing. Complete the short acceptance check below before introducing real resident records.

### Upload files as they become available

Version 1.0.4 makes each of the four upload areas explicitly optional. One file is enough. Add it to the appropriate area, choose the specialty and dates, and select **Process 1 file**. Review the parsed entries and resident matches, confirm them, and select **Save to resident dashboards**. The other upload areas can stay empty. The selected files clear after a successful save, ready for the next upload.

Later uploads add to the saved evidence. You do not need to upload older reports again or supply a file for a category with no new information. Exact saved entries are skipped. A file containing only already saved entries no longer prevents another file in the same batch from adding new evidence. If none of the selected files has new entries, the app tells you and makes no save. Explicit complete replacements still require the existing replacement checks.

If you previously skipped a resident in a shared report, you can upload the identical file again after adding or matching that resident. The app adds only the newly matched entries. The preview also identifies changed entries across files in the same batch, so any needed confirmation is available before saving.

### Remove a resident entered by mistake

Open **Administration**, select **Residents**, and choose the resident under **Add or edit a resident**. Expand **Remove this resident**. Check the displayed name and the number of saved evidence entries and CCC reviews, confirm, and select **Remove resident**.

Removal deletes that resident from the current roster and dashboards, together with their evidence and CCC reviews, including all versions of those reviews. Other residents and shared uploads are preserved. The action is saved through the existing encrypted storage and conflict checks. A failed save leaves the last saved records intact and offers the existing recovery controls.

Original encrypted upload files, older backups, prior repository versions, and audit history are retained. This action does not erase every historical copy of the resident's information. To retain a real resident's accessible history, change their status to **Inactive** rather than removing them.

### Resident names, without manually assigned IDs

Use the resident's name from New Innovations. No New Innovations ID or REDCap record ID is required. Version 1.0.3 puts names first in the roster, upload matching, and report selection. The templates and fictional examples do not require you to supply resident or evaluation IDs.

The app automatically creates a private internal reference when a resident is added. It keeps that resident's evidence and reviews connected over four years, even when their name is edited. You do not create or manage that reference. Add a prior name or another confirmed spelling under Other names used in exports, or confirm and remember the variation during upload. Ambiguous names need an explicit match before evidence is saved. An ambiguous roster CSV is stopped for review instead of creating another resident.

Existing optional source references remain supported under a collapsed section for older imports. Keep a REDCap record ID separately only if it is needed to reconcile historical REDCap data during migration.

### Public and private repositories

Version 1.0.2 accepts either a public or private GitHub repository for encrypted storage. Every dashboard and original-file payload the app saves is encrypted before upload in both cases. App sign-in, encryption keys, GitHub write permissions, and conflict checks still apply. No additional setting is required to use a public repository.

### Fix for an existing Python 3.14 deployment

Version 1.0.1 updates one dependency from `pandas==2.2.3` to `pandas==2.3.3`. The earlier pin has no Python 3.14 wheel. If your Streamlit build log shows Python 3.14 and pauses after resolving packages, update that line in the repository's `requirements.txt` and commit the change. Streamlit will rebuild the dependencies. The corrected package set installs from binary wheels on Python 3.14.8. This change does not require editing Secrets or recreating the app.

The excerpted build log did not contain a final failure, so it cannot establish that this incompatibility was the only cause of the delay. See `VALIDATION.md` for the checks performed on the updated package.

## What the team sees

| Screen | Daily purpose |
| --- | --- |
| Program dashboard | See residents, training years, evaluation counts, latest evidence and reviews, and evidence that needs checking. |
| Upload files | Four optional upload areas for evaluations, milestones, activities, and duty hours. Process one file or any available group, then review and save. |
| Resident review | Read one resident's evidence, specialty-specific milestone history, CCC notes, and all eight checkpoints. |
| Word reports | Prepare a progress report or CCC letter for one resident, or Word files for a group. Password-to-open protection is the default. |
| Administration | Add, edit, or remove residents, correct uploads, download encrypted backups, restore a backup, and get templates. |

Medicine and Pediatrics retain their own forms, questions, scores, and source labels. They appear within the same resident record and are never averaged into one cross-specialty milestone score. Combined reports remain a separate classification.

## Daily administrator routine

1. Download whichever reports are ready in New Innovations.
2. Open **Upload files** and select Medicine, Pediatrics, or Combined. Choose the actual reporting dates.
3. Add one or more files to the appropriate upload areas and leave unused areas empty. Select the appropriate report type within Evaluations, Activities, and Duty hours.
4. Select **Process 1 file** or the button showing your selected file count. Read warnings and check the displayed entries. Files with parsing errors cannot be accepted.
5. Confirm every resident name match. Save name variations only when you are sure they refer to the same resident. No record ID is required.
6. Select **Save to resident dashboards** and wait for the saved message. Parsed data and, by default, copies of the original files are encrypted before GitHub receives them.
7. Open **Resident review** or **Word reports**.

Medicine and Pediatrics files can be uploaded in separate batches throughout the year. You do not need to prepare filenames, run notebooks, make midyear and year-end copies, or assemble an intermediate REDCap CSV.

Read [INPUT_FORMATS.md](INPUT_FORMATS.md) for the exact supported columns and file types. `templates/` contains blank compatibility CSVs; `examples/` contains fictional practice files. A template is an alternative for an unsupported export, not a prerequisite for ordinary uploads.

## Reviewing a resident

Choose a resident, then select the date range to inspect. The Overview shows available evidence and the most recent entered CCC assessment. Evidence contains the original feedback, question responses, evaluator, dates, and source location. Milestones keeps Medicine and Pediatrics separate and lets you follow one code over time.

The eight checkpoints are PGY1 Midyear and Year end through PGY4 Midyear and Year end. Midyear defaults to the first six months of that training year. Year end defaults to the **full training year**. Adjust the period, meeting date, and academic year when the resident has an off-cycle start, leave, or a repeated training year. The academic year remains part of the review identity, so repeated PGY years do not overwrite one another. An administrator can set a current PGY override on the roster when elapsed time does not reflect the resident's actual training level.

The evidence cutoff uses the completion date when available, otherwise the source event or reporting date. For example, an evaluation completed January 5 is not available in a December 31 cutoff, even if the rotation occurred in December. Its rotation dates are retained. Select a cutoff that reflects the evidence available to the committee.

Review fields include strengths, growth areas, patterns over time, the CCC discussion and decision, an action plan, who will help, and follow-up. An expandable section retains clinic visit counts, observed encounters, separate Medicine and Pediatrics ITE results, USMLE results, scholarship, advocacy, and nursing/team feedback. These are entered program values; the app does not claim they were independently verified.

**Save draft** persists work in progress. **Finalize review** preserves a copy of the selected evidence, resident profile, entered assessment, and review history. Later uploads or corrections do not silently change that final evidence. An administrator can reopen a finalized review with a reason; the previous final version remains available for export. This is a versioned review record, not a legally certified electronic signature.

Missing files prompt an evidence check, not a performance judgment. Unassessed scores do not become zero. Conference percentages and cumulative summaries are shown with their actual source periods and are not added or averaged together. Source-reported duty-hour concerns are retained; the app does not infer compliance, days off, or readiness for independent practice from incomplete records.

## Word output

Progress reports contain the entered CCC assessment, evidence counts and source periods, separate milestone sets, recorded clinic/exam fields, procedures, attendance, duty-hour information, review history, and optional full evaluation and self-assessment details. Full details are selected by default. There is no 50-evaluation or five-violation truncation.

For a group of residents, the default progress-report basis is each resident's latest saved CCC review in the selected range, using that review's assessment and reporting period. A resident without a saved review is flagged before generation. Choose the current dashboard date range when you want a current evidence report instead.

CCC letters use the selected review's entered assessment, strengths, growth areas, and action plan. The app does not invent a signature or choose a concern designation based on the time of year. The original local Word letter templates referenced in the notebooks were not supplied, so these are new, editable report and letter layouts.

**Password-protected Word** is the default. Enter a separate export passphrase of at least 14 characters twice. The app encrypts the DOCX with Office password-to-open protection and verifies an exact in-memory decryption roundtrip before releasing it. Bulk downloads put the individually protected documents into a ZIP. Generic filenames avoid resident names in file metadata outside the documents. Share the passphrase separately through your approved workflow.

**Standard Word** is an explicit option. Those downloaded files are readable and must be kept in your approved secure location. Generated files are held in the signed-in session and are not written to GitHub or server disk by the app.

The upstream `msoffcrypto-tool` documentation describes its OOXML encryption support as experimental. Automated roundtrip, wrong-password, and integrity tests pass; open a protected fictional report in your actual Microsoft Word version as part of acceptance testing. Protection failures never fall back to a readable file.

## One-time deployment

The daily administrator does not need to perform these setup steps. They are for the app owner or the person who maintains the Streamlit deployment.

### 1. Prepare the repositories

Put the contents of this project in the GitHub repository used for the Streamlit app. The entry point is **`app.py`**, with the `ccc/`, `.streamlit/`, `templates/`, and other project folders retained. Do not upload the original notebooks or resident export files into the code repository.

Use your existing GitHub repository for the code and encrypted data, or select a separate data repository. Both public and private repositories are supported. Initialize the chosen repository with a README so its `main` branch exists. The app verifies the repository identity and branch before reading or saving records.

In a public repository, anyone can download the encrypted files. Reading their resident information requires the encryption key, which stays in Streamlit Secrets along with the GitHub token and account password hashes. Use the app's upload screen for resident files so encryption happens before they reach GitHub. Files manually uploaded or committed to GitHub are not encrypted by the app.

Create a fine-grained GitHub personal access token restricted to the data repository, with **Contents read and write** permission. The repository owner must also grant access required by any organization approval rules. Branch protection rules must permit these application writes. Keep the token in Streamlit Secrets.

### 2. Generate the first account and encryption key

Use Python 3.12 and a local terminal in the project folder. For a Windows PowerShell terminal:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python scripts/generate_secrets.py
```

For macOS or Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/generate_secrets.py
```

Choose `setup`, then an administrator username, display name, and a passphrase of at least 14 characters. Password entry is hidden. The helper prints the account's Argon2id password hash and a new encryption key in a TOML settings block. It does not request your GitHub token or write a secrets file.

Copy that block into **Streamlit app settings → Secrets**, and fill in the data repository and fine-grained token there. Do not paste secrets into a GitHub file or a chat message. Back up the encryption key in the program's password manager: losing it and every retained old key makes the stored records unreadable.

The supplied helper uses this layout; placeholders below are not working credentials:

```toml
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
```

For compatibility with the usual archive convention, `[github] repo` also accepts `OWNER/REPOSITORY` or a GitHub repository URL. If `path` is omitted, `base_path` supplies the parent of `ccc.enc`; its default is `encrypted`.

### 3. Deploy the actual app

In the intended Streamlit hosting account, select the code repository, branch, **`app.py`**, and Python 3.12 or 3.14. The pinned `requirements.txt` supplies the packages. Add the generated Secrets block and configure the intended Streamlit sharing settings. The app requires its own account password before any resident data are loaded.

The hosting service processes decrypted records in memory after authentication. Choose an institution-approved hosting arrangement for the information involved; application encryption and passwords alone do not establish institutional approval or a particular compliance status. This package does not configure enterprise SSO, a reverse proxy, or an organizational access policy.

### 4. Initialize and practise

Sign in, choose **Create an empty resident dashboard**, and use Administration to add the roster. The empty-dashboard action appears only after the repository and branch are confirmed and the exact data file is absent. If existing records should already be there, verify the configured path before initializing.

Use the fictional files before real exports. For a separate preview without credentials, run:

```bash
streamlit run demo_app.py
```

The preview contains only fictional data, offers no uploads or saving, and is separate from production. Deploy `app.py` for actual use.

### 5. Add accounts

Run the helper again and choose **`user`**. That mode generates only a new account block and leaves the encryption key unchanged. Add the account block to Streamlit Secrets.

| Role | Access |
| --- | --- |
| Admin | View and export; upload files; edit the roster and settings; create, finalize, and reopen reviews; back up and restore records. |
| Reviewer | View and export; create and finalize CCC reviews. |
| Viewer | View dashboards and export documents. |

Use `enabled = false` to disable an account. Account removal, password changes, role changes, or disablement are checked on app reruns. Inactive sessions expire after 30 minutes by default, with an eight-hour maximum. A background check closes an active browser session after expiry; logout clears its application session data. Login attempts are limited per account and across the server process. Those limits reset on a process restart and do not coordinate multiple replicas; use hosting-level controls where needed.

## Saving, corrections and recovery

All resident records, names, comments, source filenames, review fields, name aliases, settings and audit events are encrypted before persistence. Original uploads, when retained, use separate encrypted files with random names. GitHub sees ciphertext and generic filenames/commit messages; it still sees repository/account metadata, sizes and change times. The encryption key and token stay outside the repository.

Normal dashboard navigation reads the loaded signed-in session, so every tab change does not reload the entire archive. Saving is explicit and checks the GitHub file version originally loaded. If another person saved first, the app refuses to overwrite their change. On a connection problem, it shows **Not saved** and preserves the last confirmed state. Download the pending changes as an encrypted file if needed, then reload and reapply the changes to the newer version. There is no forced-overwrite button.

Repeated identical uploads are detected. Exact evidence duplicates are skipped while preserving separate event counts where the source supplies distinct IDs. Changed or ambiguous overlapping events are flagged for review. A complete corrected export can replace a selected earlier upload; the entire prior upload is excluded from live views. An administrator can also exclude an incorrect upload with a reason. Historical final review snapshots remain intact.

The encrypted dashboard backup includes parsed evidence, residents, reviews and audit history. It **does not include the separately encrypted original file copies**. Those remain in the data repository; preserve that repository and the encryption key as part of your recovery plan. Restore previews the resident, evidence and review counts and requires explicit confirmation before replacing the dashboard. A backup restored into a different repository will not automatically move its original-file objects.

Data serialization is compressed inside the authenticated encryption envelope to reduce repeated four-year narrative and snapshot content. The limits are 64 MiB of expanded dashboard JSON, 8 MiB of compressed state, 15 MiB per original upload, and a 60 MiB/30-file upload batch. Exceeding a limit blocks the save rather than dropping old records. For a program that grows beyond this small-team archive design, move the persistence layer to an approved database/object store while keeping the app's resident model and UI.

Retain previous encryption keys in `previous_keys` when introducing a new current key. New writes use the current key, and old keys can decrypt older files. Saving the dashboard does not re-encrypt every historical Git commit or original upload. Do not remove old keys while those objects are still needed. There is no automatic full-archive key-rotation command in this version.

## Acceptance check before real data

1. Confirm signed-out users see no resident data and each account has the intended role.
2. Save the fictional roster and one example of every report category. Refresh, sign out, sign back in, and confirm the records remain.
3. Reimport the same file and confirm counts do not increase. Check a corrected export and the replacement preview.
4. Check a current deidentified export from each distinct source format: Medicine evaluation, Pediatrics evaluation, SAR, each specialty's milestone PDF, procedure logger, conference attendance, shift log, and duty-hour violation report.
5. Compare resident names, actual evidence dates, evaluation counts, question responses/comments, and scores against those exports. Confirm each specialty's PDF has explicit, unambiguous score labels.
6. Finalize a fictional review, add later evidence, and verify the final report still reflects the saved meeting evidence.
7. Open a password-protected fictional report and letter in the Word version your team uses. Test an encrypted backup restore in the test deployment.
8. Use two signed-in sessions to confirm an older save is refused after the other session saves.

Automated tests use synthetic inputs and mocked GitHub responses. Run them with:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Current project-specific verification results are documented in `VALIDATION.md`.

## Existing REDCap records

The supplied PDF depicts one populated PGY1 midyear dashboard, not a data dictionary or a complete four-year data export. This package does not claim to have migrated the residents shown in the source material. Original New Innovations evidence can be imported directly, and earlier CCC narratives can be entered into their dated reviews. A bulk historical migration needs the REDCap CSV plus its data dictionary, with explicit resident and date mapping. See `MIGRATION_NOTES.md` for the preserved fields and changed behavior.

## Source documentation

- [Streamlit app secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Streamlit native app testing](https://docs.streamlit.io/develop/concepts/app-testing)
- [pandas 2.3.3 Python 3.14 compatibility](https://pandas.pydata.org/docs/whatsnew/v2.3.3.html)
- [GitHub repository contents API](https://docs.github.com/en/rest/repos/contents)
- [Cryptography Fernet and MultiFernet](https://cryptography.io/en/stable/fernet/)
- [Argon2 password hashing](https://argon2-cffi.readthedocs.io/en/stable/howto.html)
- [Office document protection support and limitations](https://msoffcrypto-tool.readthedocs.io/en/latest/)
