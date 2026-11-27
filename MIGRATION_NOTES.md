# Existing app data and the earlier REDCap workflow

## Updating this Streamlit app

This complete package replaces application files. It does not replace the encrypted resident archive.

Keep the existing Streamlit Secrets, encryption key, GitHub token, data repository, branch and data path. Existing saved records can be read by the updated app. Do not create a new encryption key or initialize an empty dashboard as part of this update.

Whole original exports are not retained from new uploads. Earlier app versions may have stored separate encrypted originals. Preserve the existing data repository if those historical objects are needed.

## How the earlier workflow is represented

| Earlier concept | Current app |
| --- | --- |
| r1my/r1eoy through r4my/r4eoy | One resident with eight dated CCC review checkpoints. |
| Separate Medicine and Pediatrics evaluations | Specialty-specific evidence and separate Word summary pages. |
| Resident self-assessments | Dated forms retaining source questions, responses and comments. |
| Milestones | Separate Medicine and Pediatrics code sets and dated scores. |
| ITE results, clinic totals and observed encounters | Yearly progress entries with dated history for PGY1 through PGY4. |
| Coach and graduation date | Roster fields, preserved with finalized reviews. |
| Scholarship, advocacy, nursing feedback and CCC narratives | Review fields included in the notebook-based Word summary. |
| Selected concerning comments | Evaluation flagger selections on a separate comments page. |
| Reports and letters | Editable Word downloads and optional PDF previews, with no export passphrase. |

## Evidence and history

The app stores an observation once and selects it by the report's dates. It does not require separate midyear and year-end copies of evaluation exports. Historical exports can be imported in batches and later updated with new files. Duplicate checks protect already saved entries.

Residents are matched by name or a confirmed alias. The app creates its own internal reference. You do not need a New Innovations ID or REDCap record ID.

Version 1.3.1 exposes resident-name overrides directly in the upload preview. Unrecognized names remain skipped until assigned to an existing resident. Confirmed variations use the existing saved alias field, so no data migration is needed. An alias-only save changes the roster spelling list without rewriting evidence or source upload timestamps.

New Innovations milestone grids are read by their labeled cells. The new Pediatrics crosswalk uses its first-page recommendation table and saves the maximum of each printed range. Missing scores remain distinct from zero.

EPA Tracker Competency scores and rotation comments PDFs can be uploaded through Milestone reports. The app saves their recommendation scores and original evaluation comments as separate evidence categories. No archive migration is required. Existing sources without category metadata remain readable.

If their comments have already been imported from TAB files, clear Include rotation comments from EPA Tracker PDFs to add only the milestone scores. The PDF and TAB exports can use different evaluation references.

Conference percentages and cumulative summaries retain their source periods. Duty-hour concerns remain source-reported evidence. The committee enters its assessment in a CCC review.

Finalized reviews retain their saved evidence, resident profile and yearly values. An authorized reopening preserves the prior final version.

## REDCap history

The earlier populated dashboard PDF explains the old layout but is not a complete data export. It cannot reconstruct every resident, field or historical review.

Original New Innovations evidence can be imported through the app. Existing CCC narratives can be entered into dated reviews. A bulk REDCap migration requires the REDCap CSV and data dictionary, with explicit resident, field and date mapping. Keep any old REDCap record ID as migration provenance when needed.

This package does not claim to have imported historical REDCap records.

