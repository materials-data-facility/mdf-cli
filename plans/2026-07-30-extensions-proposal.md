# MDF v2 — Quality & Cross-Reference Extensions Proposal

**Date:** 2026-07-30 · **Status:** proposal, not scheduled · **Scope:** `connect_server/aws/v2`, `mdf-next2`, `mdf_client`
**Builds on:** `plans/2026-07-12-deployment-plan.md` Part E (E1–E14), `plans/2026-07-30-release-burndown.md`

---

## 1. Executive summary

The maintainer question — "help users find the *highest quality* datasets" — has three plausible answers, and only
one of them is honest at MDF's current scale.

- **Citations won't work yet.** I queried DataCite for the whole prod prefix: 640 DOIs under `10.18126`, and
  `citationCount`, `viewCount`, `downloadCount` are **0 on every record I sampled**, including when sorting by
  `-citationCount`. Two causes: MDF never registers usage reports (so DataCite has nothing to show even though
  `view_count`/`download_count` *are* tracked in Dynamo), and dataset citation events depend on publishers
  depositing dataset DOIs in reference lists. Build citation *display*, not citation *ranking*.
- **Editorial curation won't scale.** `mdf-next2/app/spotlight/` is a hardcoded TSX object with exactly one
  entry (`omol25`). A great front page; not an index-wide quality signal.
- **Mechanical signals will work, today.** Does the data resolve? Is there a license with an SPDX identifier? An
  ORCID? A DOI with a coherent version chain? Does the `ml` block validate well enough for `foundry_ml` to load
  it? Was a human curator involved, or is this a `v1-migration` record? All computable from data MDF already
  holds, all explainable to a submitter, and none requires MDF to judge anyone's science.

**Recommended top-3 for a "quality" release theme:**

| # | Feature | Why it's first | Effort |
|---|---------|----------------|--------|
| **1** | **Data availability / link health** — nightly fetchability check on `data_sources`, `mdf.data_available` on the search entry, "Data availability" facet, admin broken-link report | The single claim MDF must never get wrong is "this data is here." Nothing verifies it today. It is also the hard gate every other quality signal depends on. | M |
| **2** | **Quality tier** — `mdf.quality.{tier,score,reasons,rubric_version}` derived at ingest from mechanical signals only; facetable in search, explainable on detail, `sort=quality` opt-in | Turns signals into something rankable without editorializing. Rules-based + `reasons[]` means a submitter can fix their score, and MDF never says "this science is better." | M |
| **3** | **Foundry-ready gate** — validate `MLMetadata`, `mdf.ml_ready` + `mdf.foundry_short_name` on the entry, "ML-ready" facet + badge, `/datasets/{id}/foundry` manifest endpoint | `MLMetadata` is a first-class model that *nothing validates and nothing consumes*. `mdf_client` has zero Foundry surface (`models/config.py:153-159` actively strips `projects.foundry`). This is the largest gap between what the schema promises and what a user can do. | M/L |

Sequencing: #1 and #2 land together or #2 lies. #3 is independent and can run in parallel.

**Cross-cutting constraints (read before scoping anything below).**
1. **Two open burndown prerequisites.** **B-18** (delete/withdraw never reconcile the search index — stale public
   entries) and **B-17** (Globus ingest acceptance ≠ completion). A quality field on an entry that is
   stale-by-construction is worse than no field. Fix first.
2. **Schedule routing is missing.** `async_worker.py:14-18` routes *every* EventBridge scheduled event to
   `JOB_CLEANUP_TRANSFERS` unconditionally. Every nightly job below needs routing on `event["detail-type"]` or an
   EventBridge `Input` carrying `job_type`. Shared **S** prerequisite.
3. **Enrichment must live in `extensions`.** `SqliteSubmissionStore._write_submission` (`store.py:469`) has a fixed
   column list and silently drops unknown record keys (why `publish_error` only survives on Dynamo). So write all
   enrichment inside `dataset_mdata.extensions` — `DatasetMetadata.extensions` (`metadata.py:155`) already
   round-trips through both stores, `parse_metadata`, and `_extract_searchable_text` (`search.py:96-97`).

---

## 2. Detailed proposals (ranked)

### P1 — Data availability / link health · **M**

**Problem (both audiences).** `data_sources` is a bare `List[str]` of Globus/HTTPS URIs and nothing checks that they
resolve. A migrated 2018 dataset pointing at a decommissioned endpoint looks identical in search to one uploaded
yesterday. Users lose trust the first time a download 404s; maintainers have no list of what to fix.

**Surface.** Search: a `Data availability` facet (`verified | unverified | broken`) and a muted "data unverified"
note on broken result cards. Detail: a row in `SpecimenLabel` (`app/components/specimen-label.tsx`) — "Data
verified 2026-07-29". Admin: `GET /admin/link-health` → broken datasets grouped by endpoint, so one dead Globus
collection is one line, not 400.

**Sketch.** New `v2/link_health.py::check_data_sources(meta) -> dict`. New job type `JOB_CHECK_LINK_HEALTH` +
fan-out meta-job `JOB_DISPATCH_LINK_HEALTH` in `async_jobs.py`, modeled directly on
`_process_dispatch_embedding_rebuild` (`async_jobs.py:884`) — same scan-published-and-latest loop, same
`skipped_current` staleness short-circuit keyed on a new `extensions.link_health.checked_at`. HTTPS sources get a
`HEAD` (fall back to ranged `GET`); `globus://` sources get a Globus Transfer `ls` on the collection root reusing
`v2/transfer.py` credentials. Writes:

```
extensions.link_health = {status: "verified"|"partial"|"broken", checked_at, sources: [{uri, ok, code, latency_ms}]}
```

`search_client.build_gmeta_entry` (`search_client.py:81`) gains `mdf_block["data_available"] = <status>`;
`DEFAULT_FACETS` (`search_client.py:17`) gains `{"name": "Data availability", "field_name": "mdf.data_available"}`;
`FILTER_FIELD_MAP` (`app/routers/search.py:22`) gains `availability`; frontend needs the matching entries in
`V2_FACET_TO_PARAM` / `V2_VISIBLE_FACETS` / `V2_FACET_ORDER` (`app/search/page.tsx:66-80`) and the `search()` param
whitelist (`lib/api/v2-client.ts:93`). A nightly `JOB_REINDEX_QUALITY` re-ingests entries whose status changed.

**Risks / deps.** Endpoint checks can be slow and rate-limited — cap concurrency, treat timeouts as `unverified`
(never `broken`; a false "broken" is worse than silence). Globus collections requiring consent will always read
unverified: that's honest, and the facet must not imply "bad." Depends on B-18/B-17 and on schedule routing.

**Not now.** Byte-level checksum verification, fixity auditing, or re-downloading data. Reachability only.

### P2 — Composite quality tier · **M**

Design detail is in §3. Summary: `v2/quality.py::build_quality_block(submission, meta)` called from
`build_gmeta_entry`, emitting `mdf.quality = {tier, score, reasons[], rubric_version}`. Search gains a `Quality`
tier facet and an opt-in `sort=quality`; detail gains an explainable "Record completeness" panel; admin gains
`GET /admin/quality` (tier histogram + the top fixable gaps, ranked by how many records they'd move a tier).

**Effort** M. **Risk:** the tier is user-visible judgement of *records*, and will be misread as judgement of
*science* unless the copy and the `reasons[]` are disciplined. **Not now:** feeding quality into the default
relevance ranking (see §3).

### P3 — Foundry-ready gate, badge, and manifest endpoint · **M/L**

**Problem.** `MLMetadata` (`metadata.py:91`) is a well-designed first-class model with `data_format`, `task_type`,
`splits`, `keys[].role`, `short_name` — and it is **write-only**. Nothing validates it; `dataset_card.py:83-102`
reflects it back verbatim; `mdf_client` has *no* ML surface at all and
`src/mdf_agent/models/config.py:153-159` deliberately strips `projects.foundry` on the way to the payload. So the
one population that most wants "highest quality datasets" — people running `foundry_ml` — cannot discover or load
MDF v2 datasets, and MDF cannot tell which of its own datasets are ML-ready. Meanwhile `mdf-next2` already renders
`FoundryMLSection` when `card.ml` is truthy and ships a hardcoded `{query:'foundry', label:'Foundry ML'}` quick-chip
(`app/search/page.tsx:656-678`) that searches free text for the word "foundry".

**Surface.** Search: an `ML-ready` facet, and an `ML-ready` `Badge` (`components/ui/badge.tsx`, variant `success`)
on result cards — the first real badge on a result card. Detail: promote `FoundryMLSection` from "renders if `ml`
exists" to "renders with a validation state," plus a copyable `Foundry().load("<short_name>")` snippet next to the
existing `mdf clone` block. Foundry availability: `GET /datasets/{source_id}/foundry` returning a loader-shaped
manifest (splits resolved to concrete download URLs, keys typed).

**Sketch.** New `v2/ml_readiness.py::validate_ml(meta, profile) -> {ready: bool, level: "loadable"|"described"|"declared", problems: [...]}`.
Checks that are all mechanical and all locally computable: `short_name` present and unique across the index
(needs a `short_name → source_id` reservation check at submit, in `app/routers/submissions.py:623`); every
`splits[].path` resolves against the stored `dataset_profile` file list (`profiler.py` output, already on the
record); every `keys[].name` appears in the profiled tabular columns
(`dataset_card.py:141-150` already walks these); `data_format` in a known set; at least one `role == "target"`
key for supervised `task_type`s. `build_gmeta_entry` gains `mdf_block["ml_ready"]` and
`mdf_block["foundry_short_name"]`; `dataset_card.py` `ml_summary` gains `readiness`. CLI: an `mdf publish --ml`
path plus reversing the `config.py:153-159` strip so `projects.foundry` in `mdf.yaml` maps to `ml` client-side
(the mapping logic already exists server-side as `metadata._migrate_foundry`, `metadata.py:554` — reuse the shape,
don't fork it). New agent tool `mdf_foundry_manifest` in `skill/mdf_tools.py` (30th tool).

**Risks / deps.** Requires deciding whether `short_name` is globally unique (it must be, if `Foundry().load(name)`
is to work) and backfilling collisions in the migrated corpus. Coordinate the manifest shape with whoever owns
`foundry_ml` before freezing the endpoint — otherwise it's a second, divergent contract. `B-13`
(`MetadataEditRequest` gaps) does not block this: `ml` is already editable (`app/routers/submissions.py:310`).

**Not now.** Hosting loader logic, dataframe materialization, or ML-format conversion in the backend. MDF says
*whether* a dataset is loadable and *where* the parts are; `foundry_ml` loads it.

### P4 — Completeness coach at submit + curator quality report · **S/M**

**Problem.** Every proposal above measures quality *after* the fact. The cheapest way to raise index quality is to
stop low-completeness records from being created. The publish form has the ingredients — `SuggestedTags` +
`lib/tag-taxonomy.ts`, `institution-autocomplete.tsx`, a DOI importer — but nothing tells a submitter "you're at 5/9;
a license and an ORCID move this to *complete*."

**Surface.** Publish form: a live completeness meter using the same rubric and the same `reasons[]` strings the
search tier uses (identical vocabulary, or submitters learn two systems). Curation queue (`app/curation/page.tsx`):
tier + missing-fields column, so curators triage by fixability. Admin: the "top fixable gaps" table from P2.

**Sketch.** Expose the rubric as `GET /quality/rubric` (static, cacheable) and `POST /quality/preview`
(partial metadata dict → `{tier, score, reasons}`), both thin wrappers over `v2/quality.py`. Frontend calls
`preview` debounced on form change via a new `v2-client.ts` method; CLI folds the same call into `mdf validate` /
`mdf publish` preflight so terminal and web agree.

**Risks.** Do not gate submission on score — a hard minimum turns a coach into a wall and sends people to Zenodo.
Advisory only. **Not now:** LLM-generated descriptions or auto-filled keywords; that needs its own provenance story.

### P5 — Cross-reference enrichment job (`extensions.xref`) · **M**

**Problem.** A user asking "is this dataset connected to anything?" gets nothing. `related_works` is submitter-typed
and mostly empty; `/datasets/{id}/related` only does shared-author and embedding-similarity (`search.py:565`,
`search.py:336`) — both *internal*. Nothing pulls the outside world in. Maintainers likewise can't report on
scholarly reach.

**Surface.** Detail: a "Cited by & connected works" section under Citation, populated from the enrichment block,
each entry a link out. Result card: a small `N citations` chip *only when N > 0*. Admin: `GET /admin/enrichment`
coverage + failure report. Explicitly **not** a facet and **not** a ranking input (see §3).

**Sketch.** New `v2/xref.py` with one adapter per source and a shared normalized shape, mirroring the existing
`mdf_client/src/mdf_agent/importers/` pattern (`base.py` ABC + `registry.py` priority list) — that pattern is
already proven and should be lifted, not reinvented. New job types `JOB_ENRICH_XREF` + `JOB_DISPATCH_XREF`.
Writes:

```
extensions.xref = {checked_at, citation_count, sources: {datacite: {...}, crossref: {...}, openalex: {...}}, works: [{doi, title, relation, source}]}
```

Adapters, in priority order of signal-per-unit-effort: **(1) DataCite REST** —
`GET https://api.datacite.org/dois/{doi}`, keyless, verified fields `citationCount`, `referenceCount`, `viewCount`,
`downloadCount`, `versionCount`, `partCount`; reuse `DataCiteClient.get_doi` (`datacite.py:124`), don't add an HTTP
client. **(2) DataCite Event Data** `/events?doi=...` for the individual citing works behind that count.
**(3) OpenAlex** `/works?filter=doi:...` for `cited_by_count`; keyless, but dataset-DOI coverage is thin — treat as
supplementary and never let it silently contradict DataCite. **(4) Crossref**, only to resolve a citing paper's
metadata once Event Data has given you its DOI.

**Risks / deps.** As measured, this ships showing zeros for essentially the whole corpus — so ship it *with* P6,
which makes the numbers non-zero, or ship it links-only and add counts later. External APIs are the failure mode:
every adapter must fail soft into `sources.{name}.error` and never block the nightly pass.

**Not now.** Full-text mining of papers for un-DOI'd dataset mentions; a real-time citation webhook; treating
citation count as quality.

### P6 — Usage-report round-trip to DataCite · **S**

**Problem.** MDF already counts views and downloads (`store.increment_counter`, surfaced by `GET /stats/{source_id}`,
`app/routers/submissions.py:917`, aggregated in `/admin/stats`). Those numbers exist only inside MDF; DataCite
Commons — where a funder or PI actually looks a dataset up — shows 0. Cheapest win available: the data is collected,
the pipe just isn't connected.

**Surface.** Nothing new in the UI; the payoff is *off*-site (DataCite Commons, ORCID records, funder reports) and it
makes P5's counts real. Optionally a "Usage reported to DataCite" line in the detail Statistics card.

**Sketch.** New monthly `JOB_REPORT_USAGE`: aggregate per-DOI view/download deltas since the last watermark, format a
COUNTER Research Data Usage Report, POST to DataCite's reports API with a JWT from the existing credential path
(`datacite.py:28` + `scripts/setup_datacite_ssm.sh`). Persist `extensions.usage_reported = {through, report_id}` so
re-runs are idempotent.

**Risks / deps.** **Verify the DataCite usage-reports endpoint, JWT provisioning, and COUNTER schema version before
scoping** — the one external contract here I have not confirmed against a live call. Blocked on **B-5/O-2** (rotate
the DataCite credential sitting in git history); do not add a second consumer of a compromised key. And bot-filter
the counters first, or MDF publishes crawler traffic as scholarly impact. **Not now:** SUSHI harvesting endpoints
for others to pull from MDF.

### P7 — ORCID-anchored identity + author & organization pages · **M**

**Problem.** ORCID is modeled end to end — `Author.orcid` (`metadata.py:30`), DataCite `nameIdentifiers`
(`metadata.py:227`), v1 migration (`metadata.py:434-440`), and it is the *preferred* key in `normalize_author_key`
(`search.py:479`) — yet it is rarely populated, so co-author linking silently degrades to lowercased name matching.
"Jane Smith" and "J. Smith" are two people; "Y. Wang" is forty. And there is no author page at all: `mdf-next2` has
**no** `/authors`, `/organizations`, or `/domains` route, and org/author values render as plain text, not links.
E10 and E13 both live here.

**Surface.** Detail: authors become links, ORCID iD shown as an icon. New `/authors/[orcid]` and
`/organizations/[slug]` collection pages (org filter already works via `mdf.organization`;
`app/data/institutions.ts` is a ready controlled vocabulary). Search: facet values become links into those pages.
Publish form: ORCID prefill from the Globus Auth linked identity.

**Sketch.** Promote the in-memory author index (`search.py:465-556`, rebuilt on demand, invalidated by the snapshot
job) to a persisted artifact next to the embedding snapshot (`embedding_snapshot.py` — same S3 pointer pattern) so
author pages don't pay a full-table scan. New `JOB_RESOLVE_ORCID` enriching `extensions.orcid_resolved` with
canonical name/affiliation from the ORCID public API. Add `orcid` to the GMeta `content["dc"]["creators"]` entries
(`search_client.py:122` emits `{"name": ...}` only — one line, large downstream value) so ORCID becomes filterable.

**Risks / deps.** The ORCID public API needs a free client credential — one more secret in the deploy path, and
**B-9** shows that path isn't solved for OpenAI either; fix the pattern once. Name-only author keys will merge
distinct people: ship the page, but label name-matched clusters as unverified.

**Not now.** ORCID write-back (pushing MDF datasets into researchers' ORCID records). Excellent feature, wrong
order — it requires identity to be right first, and a wrong write-back is a public error.

### P8 — Legacy cross-reference rescue + typed `external_refs` · **S/M**

**Problem.** `migrate_v1_payload` (`metadata.py:383`) reads only `dc`, `mdf`, `custom`, `projects`, `data_sources`,
`tags`, `test`, `update`. Worth stating precisely, because the common belief is wrong in both directions: non-`foundry`
`projects.*` blocks (`oqmd`, `jarvis`) **do** survive, dumped untyped into `extensions` (`metadata.py:547-549`) where
nothing can query them; top-level v1 `mrr` (Materials Resource Registry) and `services` blocks are **never read and
are genuinely lost** — and `scripts/convert_production_datasets.py:8` documents this as intentional ("Skips:
services, mrr, jarvis, oqmd blocks (not needed)"). So MDF's own record of which datasets are registered where, and
which mirror OQMD/JARVIS, is either unqueryable or gone. Meanwhile `ExternalSource` (`metadata.py:61`) models exactly
*one* external origin, so it cannot express "this exists in OQMD *and* JARVIS."

**Surface.** Detail: a "Cross-references" block in Research Context — "Also in: OQMD, JARVIS", "Registered in MRR",
each a link. Search: an `External source` facet (the GMeta block already carries `external_source`,
`search_client.py:92-97` — it's just not faceted). Result card: a "Cross-published" chip.

**Sketch. (a)** Add `external_refs: List[ExternalRef]` to `DatasetMetadata` — `{system, identifier, url, relation}`,
plural, sitting alongside the existing singular `external`. **(b)** Extend `migrate_v1_payload` to read top-level
`mrr`/`services` and lift `projects.oqmd`/`projects.jarvis` into typed `external_refs`; keep the raw blocks in
`extensions` for provenance (never delete on migration). **(c)** A one-shot backfill in
`aws/v2/scripts/` next to `reconcile_migration.py`, re-reading the v1 payloads for already-migrated records.
**(d)** Add `external_refs` (and, per **B-13**, `external`) to `MetadataEditRequest` (`app/models.py:96`) and
`editable_fields` (`app/routers/submissions.py:307`) so this is fixable post-ingest rather than migration-only.
**(e)** Generalize `mdf_client/src/mdf_agent/importers/registry.py` with a DataCite/Crossref DOI adapter (E6) and
add the dedupe that is entirely absent today: refuse an import whose `external.doi` already exists in the index.

**Risks / deps.** `MetadataEditRequest` on a `published` record auto-creates a minor version
(`app/routers/submissions.py:333-336`), so a bulk backfill through the API would mint a version bump per dataset —
the backfill must write through the store directly, and must not mint DOIs (cf. **D-5**).

**Not now.** Implementing an OPTIMADE provider or resolving MDF datasets against Materials Project / OQMD / AFLOW
by *structure*. See §4.

---

## 3. Quality score design

**The one rule.** The score may only read signals about the *record and its packaging*, never about the *science*.
"Has an SPDX license identifier" is a fact about a record. "Is this a good dataset" is not MDF's call. Every design
decision below follows from that.

### Signals and where each comes from

| Signal | Computed from | Notes |
|---|---|---|
| **Completeness** `C` | `DatasetMetadata` only — description ≥ N chars, ≥1 keyword, `license.identifier` (not just `name`), ≥1 author with ORCID, ≥1 `funding`, `publication_year`, `methods`/`facility`, `domains`/`fields_of_science` | Pure function of `parse_metadata` output. No network. Cheap, deterministic, explainable. |
| **Identifiers** `I` | `dataset_doi` present; DOI resolves; version chain coherent (`root_version`/`previous_version` present when `version_count > 1`) | Guard: **D-16** notes `previous_version` is batch-relative in the sync path and can clobber `root_version` — coherence checks will false-positive until that's fixed. |
| **Availability** `A` | P1's `extensions.link_health.status` | **Gate, not a weight** (below). |
| **ML-readiness** `M` | P3's `validate_ml(...).level` | Additive-only; absence is not a penalty. Most good datasets aren't ML datasets. |
| **Curation** `K` | `approved_by` present and human; `user_id != "v1-migration"` (**D-13**); `test` false; `curation_history` has a human action | The single most predictive signal available, and the one most at risk of unfairly punishing the migrated corpus. |
| **Connectedness** `X` | `len(related_works)`, `len(external_refs)`, count of non-empty `extensions.xref.sources` — **counts of links, not counts of citations** | Log-scaled, capped at ~3 points. |

**Deliberately excluded, and why:** *citation count* (measures field size and paper age, not dataset quality —
and verified as 0 across all 640 MDF DOIs, so it would be pure noise today); *view/download count* (rewards age and
press releases, and is trivially self-inflatable); *keyword or domain weighting* (that is picking favourite
sciences); *description length beyond a floor* (rewards padding); *any human star rating or review* (that is peer
review, see §4); *file count or dataset size* (a 4 kB benchmark set can be the best thing in the index).

### Composition: a gated tier, plus a score for sorting only

Do **not** publish a single opaque float as the primary artifact. Publish a tier derived by stated rules, with the
reasons attached:

```
mdf.quality = {
  tier: "verified" | "complete" | "basic" | "incomplete",
  score: 0..100,
  reasons: ["no license identifier", "no author ORCID", "data sources unverified"],
  rubric_version: "2026.1",
}
```

Tier rules (illustrative, to be pinned in `v2/quality.py`):

- `verified` — requires `A == verified` **and** `I` (resolving DOI + coherent chain) **and** `K` (human curation)
  **and** `C ≥ 0.8`.
- `complete` — `A != broken`, DOI present, `C ≥ 0.6`.
- `basic` — has the DataCite mandatory minimum (title + authors) and `A != broken`.
- `incomplete` — anything else, **or** `A == broken` regardless of every other signal.

Availability and curation act as **gates rather than weights** on purpose. A weighted sum lets a beautifully
documented dataset with dead links score 85; a gate says the thing that matters most cannot be compensated for.

`score` exists for exactly two consumers: the admin report's ranking of fixable gaps, and an opt-in `sort=quality`.
It must never be the *only* thing shown to a user, because a bare number invites the science reading.

### Where it lives

**A derived field on the search entry, computed at ingest** — `v2/quality.py::build_quality_block(submission, meta)`
called from `search_client.build_gmeta_entry` (`search_client.py:62`), the same place `mdf.domains`, `mdf.latest`, and
`mdf.download_url` are assembled. Entry and tier then cannot disagree, at the cost of one call on a path that already
runs on every publish. Three load-bearing corollaries:

1. **Network-dependent inputs are not computed at ingest.** `link_health` and `xref` are written to `extensions.*` by
   nightly jobs; `build_quality_block` only *reads* them, so ingest stays fast and offline-safe. A nightly
   `JOB_REINDEX_QUALITY` re-ingests only entries whose tier changed (diff against `search_synced_hash`, already on
   the record) — not the whole index.
2. **Quality must be non-editable.** Add `quality` to neither `MetadataEditRequest` (`app/models.py:96`) nor
   `editable_fields` (`app/routers/submissions.py:307`). And since `extensions` is currently *wholly*
   submitter-writable, the MDF-owned keys (`link_health`, `xref`, `usage_reported`, `orcid_resolved`, `quality`) need
   a reserved-namespace guard on submit and edit — otherwise a submitter declares their own links healthy.
3. **The rubric is versioned.** `rubric_version` makes a rule change detectable and the corpus re-scoreable, and lets
   the frontend refuse to render a tier it has no copy for.

### Facet vs. ranking

Expose `mdf.quality.tier` as a **facet** (`DEFAULT_FACETS`, `search_client.py:17`) and as an **opt-in sort**. Do
**not** fold quality into the default relevance score — one principled reason and one practical: silently reordering
by an MDF-authored quality judgement *is* editorializing however mechanical the inputs; and Globus Search offers no
clean field-boost knob, so any blend lands in a hand-rolled re-rank nobody can debug and that the semantic path
(`search.py:275`) wouldn't share. A facet the user chose is honest, inspectable, and behaves identically across
keyword, faceted, and semantic search.

One default worth arguing about: a **"hide incomplete"** toggle, defaulted **off**, always showing the hidden count.
Off by default because the migrated corpus will be disproportionately `incomplete` through no fault of its authors
(**D-13**), and hiding it by default would silently erase MDF's own history.

---

## 4. Non-goals

- **An OPTIMADE provider endpoint.** OPTIMADE federates *structure-level* queries (`/v1/structures` + filter
  grammar). MDF's unit is a file-based archive with a DOI, not a queryable structure collection; implementing a
  provider means building a structure store MDF does not have. Link to OPTIMADE providers from `external_refs`
  (P8); do not become one.
- **Structure-level cross-matching against Materials Project / OQMD / AFLOW / JARVIS.** Composition- or
  structure-keyed "same material as" linking needs normalization, a formula parser, and per-DB API keys, and it
  produces claims about science. If demand is real, the honest v1 is P8's declared, human-asserted cross-references.
- **Peer review, star ratings, comments, reviewer scores.** MDF is a repository, not a journal. Changes the
  submission incentive, needs moderation, and is exactly the editorializing §3 is built to avoid.
- **Scientific validity checking** — unit consistency, physical plausibility, outlier detection. Different project;
  a wrong answer here is far more damaging than no answer.
- **Citation count as a ranking or facet input.** Display only (P5): verified zero across the whole prod prefix, and
  structurally a measure of field size.
- **Full-text mining of papers** for un-DOI'd dataset mentions — depends on publisher access MDF doesn't have.
- **A second search engine / vector database.** The embedding snapshot + S3 pointer is sufficient at 640 datasets
  and two orders of magnitude beyond. **F-11** — semantic search unreachable from the UI — is the actual gap, and
  it is a frontend task.
- **Auto-rejecting or auto-hiding low-tier submissions.** Advisory scoring only (P4). And no synchronous
  quality/enrichment computation in the request path: network-touching work is nightly, ingest only reads.
- **Expanding `spotlight/` into a CMS.** One hardcoded object, and it works. If editorial curation must scale that
  is its own proposal — and P2's tier is the mechanical answer that makes it unnecessary in most cases.
