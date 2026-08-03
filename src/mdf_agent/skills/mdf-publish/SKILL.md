---
name: mdf-publish
description: Draft, review, and submit a dataset to the Materials Data Facility (MDF) using the `mdf` CLI. Gathers evidence from the local data directory, drafts an mdf.yaml manifest, and submits only after explicit human approval. Use when the user says "/mdf-publish", "publish this dataset to MDF", "submit to Materials Data Facility", "draft MDF metadata", "improve metadata for an MDF dataset", "enrich an MDF record", or asks to prepare a data directory for publication.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - WebFetch
---

# mdf-publish — publish a dataset to the Materials Data Facility

You are helping a researcher publish a dataset to MDF. Your job is to do the tedious
part — walking the data, mining the README/paper, and drafting rich metadata — and then
get out of the way so the human decides what gets published under their name.

**Two rules that override everything else in this file:**

1. **Never invent metadata.** Every value in the manifest must trace to something you
   actually read: a file, a README, a LICENSE, a Crossref record, or a direct statement
   from the user. If you have no evidence for a field, omit the field. A sparse honest
   record is better than a rich fabricated one.
2. **Never submit without two explicit human approvals.** See HUMAN GATE #1 (Stage 4)
   and HUMAN GATE #2 (Stage 6). These are not formalities. Silence is not approval, and
   **approval cannot be given in advance** — "just publish it and submit", said before
   you have drafted anything, authorizes you to start Stage 0, nothing more. The user
   has to see the actual manifest before they can approve it, and see the validated
   payload before they can approve sending it. Do not skip stages to save the user time.

Always pass `--service staging` explicitly on every service-aware `mdf` command
(everything except `mdf --version`). The CLI, SDK, and
auth layer currently disagree about their default service, and the production host is
not reachable. Do not omit it, and do not switch to `prod` unless the user asks and
confirms it works.

---

## Stage 0 — Preflight

```bash
mdf --version
```

If `mdf` is not found, tell the user to install it and stop:
`pipx install mdf-agent` (preferred) or `pip install mdf-agent`.

```bash
mdf status --auth --service staging
```

If this reports "not authenticated", **tell the user to run `mdf login --service staging`
themselves** and wait. It is an interactive Globus browser flow — you must not drive it,
and you must not attempt to work around it with tokens the user has not given you.

Confirm where you are: `pwd` and a top-level `ls`. The `mdf` CLI reads `./mdf.yaml` from
the **current working directory**, so every command in this skill assumes you are in the
dataset directory. If an `mdf.yaml` already exists, read it first and treat it as the
user's prior intent — enrich it, do not silently overwrite it.

## Stage 1 — Gather evidence

Build an evidence table before you write a single metadata field. You are looking for
facts, and you will cite them back to the user at the gate.

- **Walk the tree.** File counts, extensions, total size, directory structure. Use
  `find`/`du`/`ls`, not exhaustive reads. Directory names are often implicit labels.
- **Sample the data.** Read `references/evidence-recipes.md` now and follow the recipe
  for each format you found (CSV, Excel, HDF5, JSON, images, archives, VASP/CIF/XYZ,
  instrument files). **Sample, never load.** Never read a large file whole. Nothing is
  uploaded during drafting.
- **Mine the prose.** Read `README*`, `LICENSE*`, `CITATION*`, `paper*`, `docs/`,
  `*.bib`, and any manuscript PDF or `.tex` in the tree. These are the only reliable
  source for `description`, `methods`, `funding`, and affiliations.
- **Verify DOIs.** If the user names a paper, or you find a DOI in the README/CITATION,
  confirm it against Crossref before using it:
  `https://api.crossref.org/works/{doi}` (WebFetch). Use the returned title, authors,
  and year. **Never cite a DOI from memory** — only DOIs you have fetched and verified
  from Crossref or DataCite go into the manifest.

If the directory is empty of anything informative, say so and ask the user for the
context rather than guessing.

## Stage 2 — Dedup check

```bash
mdf search "<distinctive words from the title>" --service staging --limit 10
mdf search "<lead author surname>" --service staging --limit 10
```

If a plausible match comes back, **stop and ask the user**: is this a new dataset, or a
new version of that one? Do not decide this yourself.

- New version of their own dataset → `mdf publish --update` (Stage 6).
- Someone else's similar dataset → probably still a new dataset; confirm.
- Improving an existing published record → jump to **Stage 7 (enrichment mode)**.

## Stage 3 — Draft `mdf.yaml`

Read `references/metadata-checklist.md` and work through it field by field. It maps each
`mdf.yaml` key to its v2 metadata field, names the evidence that justifies it, and lists
the pitfalls that will otherwise bite you (author name parsing, bare ORCIDs, the
all-or-nothing `ml` block, where unknown keys go).

Write the file with the Write tool. Two non-negotiables while drafting:

- **Omit, don't fill.** No `TODO`, no "N/A", no placeholder text, no plausible-sounding
  guesses. An absent field is a valid outcome.
- **Record your provenance.** Always include a `custom.drafted_by` block. It lands in
  `extensions` and makes agent-drafted metadata auditable downstream:

```yaml
custom:
  drafted_by:
    agent: "your coding agent"
    model: "<your model id>"
    date: "YYYY-MM-DD"
    skill: "mdf-publish"
    evidence:
      - "README.md (description, methods)"
      - "LICENSE (SPDX MIT)"
      - "Crossref 10.1038/s41597-024-00000-0 (authors, year, related work)"
      - "data/train.csv sampled: 12 columns, 48,201 rows"
```

## Stage 4 — HUMAN GATE #1: manifest review

**STOP. You may not proceed past this point on your own judgment.**

Show the user the complete drafted `mdf.yaml`, then a field-by-field table of
**field → value → the evidence it came from**. Explicitly list:

- fields you left empty, and why (no evidence found),
- anything you were unsure about,
- any DOI you could not verify (and therefore excluded).

Then ask, in plain terms: *"Please review this manifest. Tell me what to change, or say
it looks good to proceed to validation."*

Now wait for the user's reply.

- If they request changes → edit and re-present. The gate re-opens; it does not close.
- If they approve → proceed to Stage 5.
- If they say nothing, change the subject, or give an ambiguous response → **that is not
  approval.** Ask again.

Do not run Stage 5 or Stage 6 in the same turn as presenting the draft.

## Stage 5 — Validate

```bash
mdf validate --json --service staging
```

This runs **client-side** manifest and preflight checks against `./mdf.yaml` (paths
exist, auth ready, manifest parses, plus lint warnings). It does **not** validate
against the server's metadata schema — the server does that authoritatively at submit
time. Fix every blocking error. Address warnings or explain to the user why you are
leaving them.

Then produce the payload preview. `mdf publish` **defaults to a dry run**, so this sends
nothing:

```bash
mdf publish --service staging
```

Treat this dry-run payload as the serialization check: it shows exactly what the server
will receive. Show the user the payload summary — this is the last chance to catch a
field that the manifest dropped or reshaped.

> **Schema-drift guard:** `references/metadata-checklist.md` mirrors the server's
> `DatasetMetadata` model, but it is a copy and copies rot. **The dry-run payload and
> the server's validation response are the ground truth.** If they disagree with the
> checklist, trust them, do what they say, and tell the user this skill needs a refresh.

## Stage 6 — HUMAN GATE #2: submit

**STOP. This is the irreversible one.** The next command uploads the user's data and
puts a record into MDF's curation queue under their identity.

Tell the user exactly what will happen: the dataset will be uploaded to MDF staging and
enter the curation queue. Then ask for explicit confirmation to submit.

**You may run the `--submit` command only if the user, in a message after you asked,
says to submit.** Approving the manifest at Gate #1 is not approval to submit. "Looks
good", said about the draft, is not approval to submit. If there is any doubt, ask.

```bash
mdf publish --submit --service staging
```

Add `--update` if Stage 2 established this is a new version of an existing dataset —
and in that case the manifest must carry the target: add
`custom.mdf_source_id: "<source_id>"` (alongside `drafted_by`) before submitting. The
server resolves which dataset to update from that field; `mdf publish --update` has no
source-id argument and will be rejected without it.

The CLI watches the submission automatically and stops at `pending_curation`. Report
back to the user:

- the `source_id`,
- that the dataset is now **in the MDF curation queue** (it is not public yet — a curator
  reviews it),
- how to check on it: `mdf status <source_id> --service staging`.

## Stage 7 — Enrichment mode (existing dataset)

Use this when the user points you at an already-published record instead of a local
directory.

```bash
mdf show <source_id> --service staging      # read the current metadata
mdf clone <source_id> --service staging     # download the data locally
```

Then run Stages 1, 3, 4 over the cloned directory to draft *improved* metadata. Both
human gates apply unchanged — and **Gate #2 guards both mutation paths below equally**:
before running either `mdf edit` or `mdf publish --update --submit`, state exactly what
will change on the record and wait for explicit confirmation in a new user message.
Gate #1 approval of the draft is never approval to apply it.

Two ways to apply the improvements — pick based on what changed:

1. **Title, description, or keywords only** — the direct edit command (Gate #2 first):
   ```bash
   mdf edit <source_id> --title "..." --description "..." --keyword "..." --service staging
   ```
   Note: the CLI exposes only these three fields today, even though the server accepts
   more.
2. **Anything richer** (authors, license, funding, methods, facility,
   fields_of_science, domains, ml, geo_locations, tags, extensions) — write the full
   `mdf.yaml` in the cloned directory with `update_metadata_only: true` **and**
   `custom.mdf_source_id: "<source_id>"` (the server resolves the update target from
   that field; without it the update is rejected), then go through Gate #2 with:
   ```bash
   mdf publish --update --submit --service staging
   ```

The server's editable-field allowlist is: `title`, `authors`, `description`, `keywords`,
`license`, `funding`, `related_works`, `methods`, `facility`, `fields_of_science`,
`domains`, `ml`, `geo_locations`, `tags`, `extensions`, `external`, `download_url`,
`data_sources`, `publisher`, `publication_year`. **Not editable:** `resource_type`,
`organization`, `acl`, and the version fields. Do not promise the user you can change
those.

Editing a published record auto-bumps a minor version, and the result flows back through
curation. Say so before Gate #2.

---

## Failure handling

- **Auth errors** → hand back to the user with the exact `mdf login --service staging`
  command. Never retry with credentials the user did not provide.
- **Validation errors you cannot resolve from evidence** → ask the user. Do not invent a
  value to make validation pass.
- **A command in this skill doesn't exist or rejects a flag** → the CLI has moved on.
  Run `mdf <command> --help`, use what is actually there, and tell the user the skill is
  out of date.
- **Upload fails mid-submit** → report the error verbatim and check
  `mdf status <source_id> --service staging` before retrying, so you don't create a
  duplicate.

## Roadmap (not in this version)

MCP composition against `mdf_agent.skill` (the in-process tool registry — a separate
thing from this bundle), reuse of the autofeed importers, a `format=agent` card
variant, and `mdf skill update` / `uninstall`.
