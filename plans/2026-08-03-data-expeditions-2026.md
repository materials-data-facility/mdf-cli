# MDF Data Expeditions 2026 — campaign plan

**Status:** draft for review · **Date:** 2026-08-03 · **Burndown:** G-1 (P2) · **Decision owner:** Ben
**Runs as:** a Moment campaign (`Campaign`, `moment-next/app/prisma/schema.prisma:3145`)
**Short form:** "Data Expeditions". Never "quests" — G-1(e) is closed.
All `moment-next` paths below are relative to `/Users/blaiszik/Desktop/git/moment-next/`; MDF paths to
`/Users/blaiszik/Desktop/git/{mdf_client,connect_server}/`.

---

## 1. Concept & pitch

An **Expedition** is a scoped, agency-aligned hunt for materials data that should be public and
isn't — or is public and unusable. A contributor claims one task ("publish a solid-state electrolyte
conductivity dataset with a complete `ml` block"), points their coding agent at `/mdf-publish`, and
walks the dataset from a group drive to a DOI'd, curated MDF record. The agent does the tedious part;
two human gates keep the human in charge of what gets published under their name
(`mdf_client/src/mdf_agent/skills/mdf-publish/SKILL.md:20-31`).

**Why now — three things landed at once.**

1. **MDF v2 is shipping.** Waves 1–6 are done and cross-model reviewed; what remains is cutover ops
   (`mdf_client/plans/2026-07-30-release-burndown.md:11`). A campaign is how you fill a new index
   instead of launching an empty one.
2. **Agent-assisted publishing works.** `/mdf-publish` + `mdf skill install` shipped in wave 6. The
   marginal cost of a *rich* record — two-paragraph description, methods, SPDX license,
   Crossref-verified relations, `ml` block with units — dropped from an afternoon to twenty minutes.
   The floor is now high **by default**, which is the only reason a quality bar is enforceable at scale.
3. **Moment can run this natively.** Campaigns, quantitative targets with an evidence ledger, an
   attribution roll, a public season board, and a campaign→agent behavioral channel all exist
   already (§3). We configure a campaign platform; we don't build one.

**What a contributor gets.** A **DOI per dataset** with them as author, unconditionally.
**Coauthorship** on the year-end *MDF Data Expeditions 2026* paper at ≥6 contribution units (§5) — a
stated, computable rule. A **public attribution roll** naming them, their agent, and every approved
submission, each number traceable to a typed record (`app/lib/campaigns/credits.ts:1-31`). And a
recipe that transfers into their lab's publishing habit.

**What MDF gets:** ~150 new datasets in six agency-aligned pockets, a measured lift in corpus
metadata completeness, and public evidence that agent-drafted + human-gated publishing produces
*better* records than the historical unassisted path.

---

## 2. The Expeditions (6)

Refines G-1(a). Each is one Moment project enlisted in the campaign. Yields span the full window
(pilot 2026-09 → horizon 2027-06-30); **silver+** is the subset clearing the §4 bar. Leads are
**roles**, not people; naming happens at campaign creation.

| # | Expedition · tag | Agency alignment | Target dataset shapes | Yield: subm / silver+ |
|---|---|---|---|---:|
| E1 | Critical Materials & Rare-Earth Alternatives · `de2026-critical-materials` | DOE Critical Materials Innovation Hub; DOE MESC supply chain | Nd/Dy-lean magnet phase/property tables; separation & leaching process→yield; substitution screening; recycling-stream characterization | 20 / 14 |
| E2 | **Batteries & Solid-State Electrolytes** · `de2026-batteries` **(pilot)** | DOE VTO (cell/pack), OE (grid storage), BES (electrolyte science) | conductivity vs. composition/temperature; cycling & degradation with cell-build metadata; SEI characterization; DFT/MD electrolyte screening; extracted literature corpora | 40 / 28 |
| E3 | Semiconductor & Microelectronics Process–Property · `de2026-semiconductors` | NIST CHIPS Metrology; NSTC materials data | deposition/etch recipe→film property; defect & dopant profiling; metrology reference sets (ellipsometry, XPS, SIMS); thermal/interface transport | 20 / 12 |
| E4 | Catalysts for Hydrogen & CO₂ · `de2026-catalysis` | DOE HFTO, BES catalysis, FECM | activity/selectivity/stability vs. composition & support; operando spectroscopy series; adsorption-energy DFT sets; electrolyzer durability runs | 25 / 18 |
| E5 | Quantum & Superconducting Materials · `de2026-quantum` | National Quantum Initiative (DOE+NSF+NIST); DOE QIS centers | qubit-relevant characterization (loss tangents, TLS density, surface oxides); film growth→Tc/Jc; topological transport; junction fab→coherence | 15 / 10 |
| E6 | AI-Ready Benchmark Corpora · `de2026-ai-ready` | **Genesis Mission**; NSF AI institutes; DOE ASCR | train/val/test-split datasets with a complete `ml` block; benchmark re-releases of scattered corpora; negative/failed-experiment sets | 30 / 24 |

**Totals: 150 submitted / 106 silver+.** Treat 150 as the headline dial and 100 silver+ as the one
that matters.

**Example sources, per expedition, in order:** CMI-funded group drives and SI-only tables (Ames Lab,
university magnet groups) · the existing Battery Cell Properties extraction — 1,871 records from
1,000 ChemRxiv preprints, the proof this works (Moment project #204, page `llm-roadmap-designs`,
Design 9) — plus JCESR-lineage groups and ARPA-E performers with expired DMP clocks · university
cleanroom process logs (the most under-published class of materials data) and NIST metrology
collaborators · HydroGEN/H2NEW node data, single-atom/alloy screening studies, CO₂-reduction groups
publishing only Faradaic-efficiency plots · QIS center members and the "we measured 300 films and
published 4" backlog · group-internal benchmark splits circulated by email, and papers whose "data
available on request" hides a clean CSV.

**Lead roles and the three judgment calls.** One lead per expedition (*Critical Materials*,
*Battery Data*, *Microelectronics Data*, *Catalysis*, *Quantum Materials*, *AI-Ready Data*
Expedition Lead), plus a **Campaign Steward** and deputy.
**E2 is the pilot** — its lead calibrates the rubric; their first ten reviews define silver.
**E3 is the highest-friction expedition:** proprietary tool recipes and industrial NDAs mean the lead
spends more time on clearance than metadata. Budget for it and do not read a low E3 count as failure.
**E6 has the highest silver+ rate** — an `ml`-shaped dataset drafted through `/mdf-publish` clears the
bar almost automatically; that lead owns `ml`-block conventions and is the Genesis Mission liaison.

---

## 3. Architecture on Moment

### 3.1 One campaign + one umbrella project + six expedition projects

**The campaign is the top-level object, not a project.** `Campaign` carries what a project cannot
express: quantitative targets with an evidence ledger (`schema.prisma:3229`, `:3279`), qualitative
objectives with acceptance criteria stated up front (`:3507`), a public funder/affiliation letterhead
(`:3484`), a closure ceremony with a frozen credits snapshot (`:3578`), and an `agentConstitution`
(`:3168`).

**Why one project per expedition, not tasks-only.** Four schema facts:

1. **Review authority is per-project.** Only the project **owner** or a designated **mentor** can
   review a submission (`app/lib/reviewers.ts:26-27`, `:38`). Six projects = six independent review
   lanes with six leads as mentors; tasks-only funnels every review through one owner. Decisive.
2. **Context is per-project.** Brief, `agentRead` pages, resources, handoff and session notes all
   hang off `Project` (`schema.prisma:384-392`). Each expedition needs its own conventions injected
   into its agents' context, not a shared blob.
3. **Enlistment is per-project with a declared commitment** (`CampaignProject.commitment`, `:3432`),
   and the campaign constitution is injected into *enlisted* projects' agent context
   (`app/lib/copilot/context-assembler.ts:382-399`, rendered `:1316-1344`, capped at
   `CONSTITUTION_PROMPT_CHARS = 2400`, `:301`).
4. **A task carries one campaign** (`:600-606`) but lives in one project — and attribution still rolls
   up campaign-wide via `campaignCredits` (§5.3), so splitting costs nothing in reporting.

The **umbrella project** holds the charter, quality bar, authorship policy, contributor runbook and
scoreboard — read-once pages — is enlisted too, and owns the paper-writing tasks.

### 3.2 Campaign scaffold — targets and objectives

Three headline **targets**. `CampaignTarget.baselineSource` is required and non-empty
(`schema.prisma:3243`): provenance for the starting line, and it must not be invented.

| Target | `metricKey` | unit | direction | baseline | target |
|---|---|---|---|---|---|
| Expedition datasets published | `de2026.datasets_published` | datasets | MAXIMIZE | 0 | 150 |
| Silver-or-better contributions | `de2026.silver_plus` | datasets | MAXIMIZE | 0 | 100 |
| Median metadata completeness | `de2026.median_completeness` | points (0–100) | MAXIMIZE | **must be measured** | 75 |

> **Do not create the third target until the baseline exists.** Run the §4.2 scorer over the
> published corpus at 2026-09-30 and use that number, with run date and record count as
> `baselineSource`. A fabricated baseline poisons every later claim of improvement.

Six **objectives** (`CampaignObjective`, criteria up front, `:3507-3539`), one per expedition — e.g.
*"E2: ≥28 silver-or-better datasets tagged `de2026-batteries`, each with a resolving DOI and a
curator-approved record."* Delivery requires `evidenceUrl` + a steward VERIFY (`:3541-3546`). This is
where §2's yields become public commitments. `CampaignOrg` rows (`:3484`) carry the letterhead: DOE,
NSF, NIST as `FUNDER`; ANL, UChicago and participating institutions as `AFFILIATION`.

### 3.3 Tasks sized for `/burn`

Expedition tasks are proposed with `propose_task` and **campaign-linked at proposal time** —
`campaignId` and `campaignTargetId` are accepted fields
(`app/api/v1/agents/projects/[projectId]/tasks/propose/route.ts:61-62`, applied `:149-150`).

Real constraints that shape task design:

- **Work type must be agent-proposable** — only the nine `forAgents: true` types are accepted
  (`app/(dashboard)/tasks/_components/work-types.ts:63`; enum at propose `:41`).
  **`RESEARCH_TASK` is NOT agent-proposable** (`work-types.ts:24`) — a human steward can create it in
  the UI, an agent cannot. Use **`DATA_ANALYSIS`** for publish/enrich and **`LITERATURE_REVIEW`** for
  scouting.
- **`baseCredits` ≤ 25** for agent-proposed tasks (propose `:30`, `:50`).
- **`contributorType` defaults to `BOTH`** (`schema.prisma:555`) and the propose route doesn't set it
  — exactly right: every expedition task is claimable by a human *or* their agent.
- **20 claims per rolling 24 h per agent** (`app/api/v1/agents/tasks/[id]/claim/route.ts:4-7`).
- **Submission floors:** `reasoning` ≥100 chars, `output` ≥200 chars, `confidenceScore` ≥0.3
  (`app/api/v1/agents/tasks/[id]/submit/route.ts:27-30`).
- **Set `autoApproveAgentTasks = false`** on all seven projects (`schema.prisma:423`; propose `:106`)
  — expedition tasks are curated work items, not an open firehose. Proposals land `UNDER_REVIEW`.

Three task shapes, each one contribution-sized:

- **SCOUT** (`LITERATURE_REVIEW`, EASY, 5 cr) — "Find 5 unpublished/underpublished datasets in scope
  X; per candidate: source, license-evidence URL, rough shape, contact." The lead turns accepted
  candidates into PUBLISH tasks.
- **PUBLISH** (`DATA_ANALYSIS`, MEDIUM/HARD, 15–25 cr) — one task = one dataset = one DOI. The atom.
- **ENRICH** (`DATA_ANALYSIS`, MEDIUM, 10–15 cr) — "raise `<source_id>` from bronze to silver" via
  Stage 7 enrichment mode (`SKILL.md:209-251`), both gates unchanged.

### 3.4 End-to-end flow of one contribution

```
1  CLAIM      /burn → get_tasks → claim_task → get_task_context. The project context arrives
              carrying the campaign commitments block AND the constitution verbatim
              (context-assembler.ts:1316-1344).
2  GATHER     /mdf-publish Stages 0-3: preflight → evidence table → dedup check → draft mdf.yaml.
              Manifest MUST carry the expedition tag block (§3.6) and custom.drafted_by.
3  GATE #1    Human reviews the manifest field-by-field against evidence (SKILL.md:128-149).
              Silence is not approval; approval cannot be given in advance.
4  VALIDATE   mdf validate --json; mdf publish (dry run) → payload preview.
5  GATE #2    Human authorizes submission in a NEW message (SKILL.md:179-207).
6  SUBMIT     mdf publish --submit → source_id → MDF curation queue (pending_curation).
7  MDF CURATE POST /curation/{source_id}/approve (connect_server/aws/v2/app/routers/curation.py:116,
              require_curator). DOI mints; record publishes; search ingest confirmed.
8  MOMENT     submit_task(taskId, output:{...}, reasoning, confidenceScore) with
   SUBMIT     completionReport.campaignMeasurements = [{ targetId, value: 1,
              evidenceUrl: "https://doi.org/10.18126/<…>", note: "de2026-batteries · silver" }]
              (parsed by app/lib/campaigns/submission-measurements.ts:42-100).
              output carries source_id, DOI, completeness score + subscores, claimed tier,
              license evidence, gate approvals, drafted_by.
9  LEAD       Expedition lead (a project mentor) approves at POST /api/tasks/{id}/approve
   REVIEWS    (app/api/tasks/[id]/approve/route.ts:70). qualityScore 0.0-1.0 is REQUIRED on approve
              (:120-133) and IS the tier (§4.3). No self-review (:229-234). Inside the approval
              transaction: CreditAward written (app/lib/credits.ts:90 via task-approval.ts:242) AND
              the campaign measurement filed REPORTED (submission-measurements.ts:211) — never
              VERIFIED.
10 STEWARD    POST /api/campaigns/{slug}/measurements/{id}/verify → currentBest moves
   VERIFIES   (applyVerifiedMeasurement, app/lib/campaigns/service.ts:48). Season board dial and
              credits roll update.
11 CLOSE OUT  log_session(projectId, note, taskId, handoff:{nextStep, watchOut}). watchOut = the
              friction actually hit (a license that couldn't be verified, an instrument header
              that lied) — that is what the next contributor needs.
```

Steps 1–8 and 11 are fully agent-executable. 7, 9, 10 are human by design (three different humans,
ideally). 3 and 5 are human by policy.

### 3.5 Coordination surfaces

- **`agentRead` pages are the contract.** `agentRead: true` pulls a page into *every* agent's context
  for that project (`schema.prisma:1053` ff.; upsert via
  `app/api/v1/agents/projects/[projectId]/pages/route.ts:109`). Umbrella: `quality-bar`,
  `contributor-runbook`, `authorship-policy`. Per expedition: `expedition-brief`.
- **`agentConstitution` is the enforcement channel** — the only campaign→agent behavioral wire that
  exists (`schema.prisma:3164-3168`; ACTIVE-only load at `context-assembler.ts:382-399`). Keep it
  under 2400 chars or it truncates (`:301`, notice `:1341`). Text in §8 step 3.
- **Handoff + session notes** are the per-expedition lab notebook (`ProjectHandoff` `:888`,
  `SessionNote` `:923`); the lead refreshes `nextStep` weekly.
- **`ProjectResource`** for each published dataset (`kind: DATASET`, `role: OUTPUT`). Note the
  provenance spine: `taskId`/`submissionId` are stamped **server-side at approval only, never
  self-reported** (`:1228-1234`), so approved-work resources are auditable.
- **Campaign discussions** (`Campaign.discussions`, `:3193`) for open questions and disputes.

### 3.6 The MDF-side expedition tag

Every submission's `mdf.yaml` carries **both** halves:

```yaml
subjects:                          # → keywords → dc.subjects  (THE QUERYABLE HALF)
  - "de2026-batteries"
  - "mdf-data-expeditions-2026"
  # ...plus 6-12 real scientific keywords
custom:                            # → extensions              (THE AUTHORITATIVE HALF)
  expedition: "de2026-batteries"
  expedition_campaign: "mdf-data-expeditions-2026"
  expedition_task: "moment:task/1234"
  expedition_tier_claimed: "silver"        # advisory only — never read for credit
  drafted_by: { agent, model, date, skill: "mdf-publish", evidence: [ ...≥3 lines ] }
```

**Why both — a real gap.** `custom:` lands in `extensions` on the payload
(`mdf_client/src/mdf_agent/models/config.py:212-221`), but **`extensions` is never written into the
Globus Search entry**: `build_gmeta_entry` assembles only `mdf.*`, `dc.*`, `data.*`
(`connect_server/aws/v2/search_client.py:197-286`). So `extensions.expedition` is **not searchable or
facetable today** — visible only via `mdf show <source_id>` / the submissions API. `dc.subjects` *is*
a default facet (`search_client.py:152-158`) and a filterable query param (`keyword` → `dc.subjects`,
`connect_server/aws/v2/app/routers/search.py:22-28`, endpoint `:123`).

Attribution query that works today (public, no auth):

```bash
curl -s "$MDF_API/search?keyword=de2026-batteries&limit=100" | jq '.total, .datasets[].source_id'
```

**Two follow-ups to file as burndown items after review:**

- **B-21 (P2, S)** — add `mdf.expedition` to `build_gmeta_entry` from `extensions.expedition`, plus
  `DEFAULT_FACETS` + `FILTER_FIELD_MAP`. Ship with the **reserved-namespace guard** on `extensions`:
  today `extensions` is *wholly* submitter-writable
  (`mdf_client/plans/2026-07-30-extensions-proposal.md:331-334`), so a contributor could self-declare
  gold. That is exactly why §4 computes tier from metadata and never reads the claimed value.
- **C-12 (P2, S)** — `mdf search` exposes no facet filters: the CLI has
  `--type/--limit/--semantic/--json/--brief` only (`mdf_client/src/mdf_agent/cli/main.py:1273-1288`)
  while the API accepts `year/organization/author/keyword/domain`. Add `--keyword`, or the roll-up
  script must bypass the CLI (it does today; §5.3).

*Rejected alternative:* an MDF `organization` per expedition. `mdf.organization` **is** a facet
(`search_client.py:154`), but `organization` is not in the server's editable-field allowlist
(`SKILL.md:242-248`), needs backend setup, and would fight the real owning organization.

---

## 4. Quality bar & scoring

### 4.1 What counts as a completed contribution

All four, no exceptions:

1. **Curator-approved** in MDF (status `published`, DOI minted, search ingest confirmed).
2. **Completeness score ≥ 50** (§4.2), computed by the lead's script from the record's metadata —
   **never from the submitter's claimed tier**.
3. **`custom.drafted_by` present** with ≥3 evidence lines when an agent drafted it
   (`SKILL.md:107-126`). Provenance is a requirement, not a nicety.
4. **Both human gates observably passed** — the submission's `output` names who approved at Gate #1
   and Gate #2 and when. A submission that cannot say this is rejected regardless of record quality.

Excluded from all counts: `test: true` records, and anything with `user_id == "v1-migration"` (the
migrated corpus, D-13 — `release-burndown.md:31`; scoring it would punish authors who never chose this).

### 4.2 The completeness score (0–100)

A pure function of the record's metadata — deterministic, offline, explainable. **It reads facts about
the record and its packaging, never about the science** (the one rule,
`extensions-proposal.md:271-273`). Every clause maps to `references/metadata-checklist.md`.

| Pts | Signal | Rule | Checklist |
|---:|---|---|---|
| 20 | `description` | ≥600 chars **and** ≥2 paragraphs (what/how/why + contents/structure) | `:62-74` |
| 15 | `methods` | ≥1 entry naming an instrument, code, or technique | `:76-87` |
| 15 | `license` | SPDX **identifier** present (not just free-text `name`) | `:102-119` |
| 10 | `keywords` | ≥6 entries, none filler ("data", "science", "research") | `:89-96` |
| 10 | authors | ≥1 author with a normalized ORCID | `:28-58` |
| 10 | `related_works` | ≥1 entry with a Crossref/DataCite-**verified** id and a `relation_type` | `:133-156` |
| 5 | `funding` | ≥1 entry with `funder_name` | `:121-131` |
| 5 | context | `facility`, **or** both `domains` and `fields_of_science` | `:167-178` |
| 5 | provenance | `custom.drafted_by` with ≥3 evidence lines | `SKILL.md:114-126` |
| 5 | `ml` | `data_format` + ≥1 `keys` entry with `role` **and** (`units` or `dtype`) | `:184-218` |

**Non-ML data is not penalized:** if the data isn't ML-shaped the 5 `ml` points shift to
`description` (25). Absence of an `ml` block is never a deduction (`extensions-proposal.md:282`).
Conversely an ML-shaped dataset with a *partial* `ml` block scores 0 there — the block is
all-or-nothing and the server rejects it without `data_format` (`:185-187`).

**Deliberately excluded:** citation counts (verified 0 across all 640 MDF DOIs — pure noise),
view/download counts (rewards age, trivially self-inflatable), file count or size (a 4 kB benchmark
set can be the best thing in the index), any star rating (that's peer review, not MDF's job) —
`extensions-proposal.md:286-290`.

**Rubric version `de2026.1`**, pinned in a script committed to `mdf_client` and stamped into every
scored record, so a rule change is detectable and the corpus re-scoreable.

> **Relation to the proposed `mdf.quality` block.** `extensions-proposal.md:292-336` designs a
> server-computed tier at ingest. **That is a proposal; it is not implemented.** The expedition score
> is a client-side scorer over the same signals, scoped to what `parse_metadata` gives us today.
> When `mdf.quality` ships, retire the local scorer and map tiers onto it — never run two rubrics.

### 4.3 Tiers → contribution units → `qualityScore`

| Tier | Requires (on top of §4.1) | Units | `qualityScore` |
|---|---|---:|---:|
| **Bronze** | score ≥ 50; license present; description ≥ 300 chars | 1 | 0.5 |
| **Silver** | score ≥ 70; ≥1 `methods` entry; SPDX identifier; ≥1 ORCID | 2 | 0.75 |
| **Gold** | score ≥ 85; ≥1 verified `related_works`; complete `ml` block if ML-shaped; `funding`; ≥1 ORCID | 3 | 1.0 |
| — | fails §4.1 or an anti-gaming rule | 0 | reject / request_changes |

The tier is **carried by the platform, not a spreadsheet**: `qualityScore` is required on approve and
multiplies the credit — `creditAmount = round((creditsAwarded ?? baseCredits + bonusCredits) *
qualityScore)` (`approve/route.ts:120-133`, `:257-260`). Set `baseCredits` so gold on a PUBLISH task
lands ~20 credits and the ledger sorts itself. Reject and request_changes each require ≥10 chars of
notes (`:135-140`); the lead names the missing field, and `request_changes` lets the contributor
resubmit on the same taskId (`/burn` step 0 reads the outcome next session).

### 4.4 Anti-gaming rules

1. **No bulk low-effort mirrors.** A dataset already public elsewhere (Zenodo/Figshare/Dryad/NOMAD/
   Materials Cloud) resubmitted with substantively unchanged metadata = **0 units**. A *value-added*
   mirror (new `ml` block from real column sampling, verified typed relations, normalized units,
   documented methods) caps at **bronze / 1 unit** and **must** carry `related_works` with
   `IsIdenticalTo` or `IsDerivedFrom` to the source DOI. Mirroring at scale is the autofeed's job.
2. **Autofeed-derived submissions are credited differently (Design 8B).** Design 8B stages harvested
   datasets under a dedicated `autofeed-agent` identity, flags them AUTOFEED, and **credits the
   original authors, never re-minting authorship** (Moment project #204, page `llm-roadmap-designs`,
   Design 8 Part B, step 5). Therefore triggering or approving an autofeed record earns **0
   contribution units** — the corpus grows, the coauthorship ledger doesn't. *Curating* autofeed
   candidates is real work, credited as VALIDATOR: **0.25 units/decision, capped at 4 units.**
3. **No dataset splitting.** Datasets from one study a reasonable person would publish as one record
   count as one contribution. Lead judgment, stated in the review note.
4. **Diminishing returns per contributor per expedition:** datasets 1–5 full, 6–10 at 0.5×, 11+ at 0;
   hard cap **12 units per contributor per expedition**. Breadth (a second expedition, or mentoring)
   is worth more than volume in one — the behavior the campaign wants.
5. **No self-review** — enforced in code (`approve/route.ts:229-234`). Leads cross-review: E2↔E6,
   E1↔E3, E4↔E5.
6. **Attribution is claimed, not assumed.** Co-contributors are named via
   `TaskSubmissionContributor` and must **attest**; still-`PENDING` rows are `OMITTED` at approval and
   unconfirmed names never render (`schema.prisma:801-836`). Ghost coauthors are structurally
   impossible.

---

## 5. Incentives & authorship

### 5.1 Per-dataset DOI credit — unconditional

Every accepted dataset mints a DOI with the contributor as author, independent of tiers, thresholds,
and the paper. Stated first on purpose: no one should feel their work was banked toward a reward they
might not reach.

### 5.2 Coauthorship on *MDF Data Expeditions 2026*

**Contribution units = Σ (datasets × tier units)** per §4.3, computed from typed records only —
approved `TaskSubmission` rows on campaign-linked tasks and the `CreditAward` ledger, which is
exactly what `campaignCredits` already aggregates (`app/lib/campaigns/credits.ts:13-20`). Never
self-report.

| Path | Threshold | Result |
|---|---|---|
| **Contributor** | ≥6 units **and** ≥2 distinct datasets at silver+ | Coauthor |
| **Expedition lead (escalation)** | ≥15 verified units in their expedition **and** they write their expedition's section | Coauthor, in the lead block after the corresponding authors |
| **Curation / validation** | ≥20 review decisions on expedition submissions (`reviewedById` on approved submissions) | Coauthor |
| **Below threshold** | any accepted contribution | Acknowledgements + the public credits roll; keeps every DOI |

Rationale for 6: one gold + one silver + one bronze, or three silvers — reachable in a quarter by
someone doing real work, unreachable by publishing six thin mirrors (rule 4.4.1 zeroes those).

**Leads who don't write their section are acknowledged, not authored.** Stewardship without writing
is not authorship. Say this at recruitment, not at submission time.

**Agents are not authors.** An agent's contribution is recorded in `custom.drafted_by`, in the paper's
methods, and in the Moment credits roll — where agents appear as first-class actors alongside their
named human operator (`credits.ts:22-23`, `:57-66`). The human operator earns the units.

**Dispute procedure, written down before there is a dispute:** the credits roll is the record. A
contributor who believes a count is wrong opens a campaign discussion citing submission IDs; the
**campaign steward** — not the lead whose review is contested — adjudicates and posts a
`CampaignUpdate`. The author list freezes at closure, when `CampaignClosure` snapshots the full
credits roll into JSONB (`schema.prisma:3578-3596`) — immutable and timestamped, and a later profile
edit cannot mutate it.

### 5.3 The visible progress surface — what renders today

- **Public season board** — `GET /api/campaigns/{slug}/board`
  (`app/api/campaigns/[slug]/board/route.ts:170`, builder `:138`): per-dial baseline → currentBest →
  target with computed `progress`, verified measurement count, a **weekly delta**, and the single
  latest *true* event (`latestBoardEvent`, `:106` — never a synthetic line). Anonymous-readable.
- **Campaign page** — `app/(dashboard)/campaigns/[slug]/page.tsx`: letterhead, dials, evidence
  ledger, portfolio of enlisted expeditions, milestones, updates, the newcomer on-ramp, and the
  credits roll (`components/campaigns/{CampaignDial,CampaignLedger,CampaignPortfolio,CampaignFirstTask,CampaignCreditsRoll}.tsx`).
- **Attribution roll** — `GET /api/campaigns/{slug}/credits` (`route.ts:11`) and its agent twin
  `GET /api/v1/agents/campaigns/{slug}/credits` (`route.ts:19`). Per actor: roles, `taskCredits`,
  `approvedSubmissions`, `measurementsSubmitted`, and a per-event trail; sorted stewards-first then
  `taskCredits` desc (`credits.ts:468-482`). Names and counts are campaign-public — "attribution IS
  the product" (`:24-30`) — while per-event *links* into projects a viewer can't see are omitted.
- **Public SVG badge** — `GET /api/badge/campaign/{slug}`. Embed on the MDF frontend and in
  contributors' READMEs.
- **"Co-authorship eligible" chip** — set `Project.coAuthorshipEligible = true` on all seven projects
  (`schema.prisma:345-349`); it drives an existing UI chip. Free credibility.

**There is no leaderboard we should use.** `GET /api/leaderboard` exists but is global,
`prisma.user`-only (agents never appear), and its `credits` sort is a documented per-page in-memory
re-sort (`app/api/leaderboard/route.ts:167`). **The credits roll is the campaign's leaderboard** —
campaign-scoped, includes agents, every number traceable. Do not build a second one.

**MDF-side scoreboard needs a small build.** `/admin/stats` is curator-gated and returns only status
counts + view/download totals + sync state (`connect_server/aws/v2/app/routers/admin.py:302`) — **no
per-contributor breakdown**. The public path is Globus Search. Ship a nightly script in `mdf_client`
that, per expedition, calls `GET /search?keyword=de2026-<slug>&limit=100` (paging on `offset`), scores
each record per §4.2, then writes (a) the umbrella `scoreboard` page via `upsert_page` and (b) one
`report_campaign_progress` measurement per dial. ~150 lines, not a platform feature. Optionally pair
with a public expedition page on the MDF frontend alongside F-12.

---

## 6. Timeline

**Hard prerequisite: the cutover must land first.** Pointing contributors at a dead host would be a
self-inflicted wound. Gating items from `release-burndown.md`:

| Item | Why it gates the pilot | State (2026-08-03) |
|---|---|---|
| **O-5** | Create `api.materialsdatafacility.org` — the CLI hardcodes exactly this host for prod | template shipped; **domain creation still ops** (`:11`) |
| **C-1** | Default `prod` URL is a dead host; every programmatic default hangs ~30 s then fails | blocked on O-5 (`:11`) |
| **O-2 / B-5** | DataCite credential rotation, prod prefix `10.18126`, prod Globus confidential client | **open, maintainer** (`:11`) |
| **F-1** | Merge `design/specimen-phase0` → `main`; everything shipping is unmerged | **open** (`:11`) |
| **F-6** | Decide `NEXT_PUBLIC_USE_V2_API` — it ships `false`, keeping the new MDF dark | **open decision** (`:11`) |
| **O-1** | The cutover sequencing gate itself (freeze → sync → reconcile → flip → redirects → canary) | **open** |
| *new* | Curation capacity: add expedition leads to `CURATOR_GROUP_IDS` (`connect_server/aws/v2/app/auth.py:218-229`) | not started |

| Window | Milestone | Gate |
|---|---|---|
| **Aug 2026** | Plan review; ADMIN creates the campaign (PROPOSED, non-public); umbrella + 7 projects bootstrapped; pages written; leads named | this document approved |
| **Sep 1–14** | **Baseline measurement** — run the §4.2 scorer over the published corpus; that number becomes the third target's baseline with a real `baselineSource` | scorer committed |
| **Sep 15–30** | **Pilot: E2 only.** 6–8 invited contributors, 10–15 datasets, campaign still non-public. Pilot lead calibrates the rubric on the first 10 reviews and publishes the calibration to `quality-bar` | **all prerequisites above closed**; a real DOI minted through the prod path |
| **Oct 15** | **Open launch.** Campaign → `ACTIVE`, `isPublic: true`, `startedAt` set; six expeditions enlisted; ~10 OPEN tasks each; featured on-ramp set; announcement dispatch | pilot produced ≥10 curated datasets **and** median completeness ≥70 on pilot records |
| **Dec 31** | **Checkpoint 1** (`CampaignMilestone`, checkpointValue 40). Steward posts a `CampaignUpdate`; leads refresh handoffs; retire dead task shapes | — |
| **Mar 31, 2027** | **Checkpoint 2** (90). Mid-campaign coauthorship snapshot published so contributors know where they stand | — |
| **Jun 30, 2027** | **Horizon.** Submissions close; final curation sweep; credits roll frozen | — |
| **Jul–Sep 2027** | **Paper window.** Lead sections in Jul (`WRITING_ASSIST` tasks on the umbrella); full draft Aug; author list from the frozen `CampaignClosure` snapshot; submit by **Sep 30** | closure ceremony run |

**Naming decision, stated explicitly:** "2026" is the campaign *vintage* (launch year), not the
publication year. A 12-month pilot→horizon window is what makes 150 datasets and a substantive paper
credible. *Rejected alternative:* compress the horizon to 2026-12-31 for a literal year-end paper —
that yields ~40 datasets and a thin data-descriptor note. **Escalate to Ben if a 2026 title with a
2027 submission is unacceptable; the fix is to rename, not to compress.**

---

## 7. Risks & open questions

| # | Risk | Mitigation | Residual |
|---|---|---|---|
| R1 | **Curation-queue load.** 150 submissions through one queue, `require_curator` on every approve (`curation.py:116`) | Add leads to `CURATOR_GROUP_IDS` (`auth.py:218-229`) → six parallel curators, cross-assigned so a lead never curates their own. Cap OPEN PUBLISH tasks at 10/expedition to throttle at the source | If leads are slow, time-to-DOI becomes the contributor-facing failure. **Publish a 10-business-day queue SLA and track it** |
| R2 | **Single-curator bottleneck** (R1 worst case): one person, ~15 min/record → 37 h of review | R1, plus a completeness pre-check in the roll-up that rejects sub-50 records before a human opens them | **Open: is `CURATOR_GROUP_IDS` a Globus group we can actually manage?** Confirm before recruiting leads |
| R3 | **Authorship disputes** — "I did more than that count says" | Typed-records-only counting (§5.2); attestation-based co-contributors (`schema.prisma:801-836`); steward (not lead) adjudicates; list frozen in `CampaignClosure` | A contributor who never uses the Moment flow has no record. **Moment task submission is mandatory for credit — say so at recruitment** |
| R4 | **License verification burden.** Never infer from prose (`SKILL.md:114-119`) — the top per-dataset cost, worst in E3 | SCOUT tasks must return a license-evidence URL *before* a PUBLISH task is created. Unlicensed is a valid publishable state — omit the field, note it at Gate #1 | Some datasets die at license. Budget ~20% scout→publish attrition, higher in E3 |
| R5 | **`extensions` is submitter-writable**, no reserved-namespace guard (`extensions-proposal.md:331-334`) — a contributor can self-declare gold | Tier is *computed*; `expedition_tier_claimed` is advisory and never read for credit. Ship B-21's guard | Cosmetic until B-21 |
| R6 | **`extensions.expedition` isn't searchable** (`search_client.py:197-286`) | The mandatory `subjects` twin (§3.6) + B-21 | Forget the `subjects` entry and the dataset is invisible to attribution. **In the constitution, and in `validate` warnings if possible** |
| R7 | **Moment campaign scaffolding is human-only.** No agent create/read of a campaign; targets, objectives, milestones, updates, orgs, enlistment and verification are all session+steward; creation additionally needs `platformRole === 'ADMIN'` (`app/api/campaigns/route.ts:34`) | §8 splits explicit human and agent phases. Nothing in the recurring loop needs the human path except verification | Verification is a standing weekly steward chore. If it lapses, dials freeze while work continues — visible as REPORTED-but-unverified ledger rows |
| R8 | **`mdf preview` fails across the migrated corpus** (C-7 partial) and D-13 coverage gaps make migrated records look worse than they are | Exclude `v1-migration` records from all scoring (§4.1); never render a tier for a record we didn't score | — |
| R9 | **Skill-doc drift.** The installed CLI has no `explore` command though `~/.claude/skills/moment/reference.md:68` documents it; the domain enum has 16 values (`schema.prisma:499-516`) vs 10 in `reference.md:49` | §8 uses only verified surfaces; run `moment doctor` before any bootstrap | Cost me time; will cost the coordinator time. Worth an issue against the skill |
| R10 | **Yields are judgment, not extrapolation** — no per-quarter submission-rate baseline existed | Checkpoint 1 is the first real data point. **Revise targets then rather than defending them** — `PATCH /api/campaigns/{slug}/targets/{id}` exists | Stated as a known unknown |

**Open questions for Ben:** (1) the 2026-title / 2027-submission decision (§6); (2) target journal —
*Scientific Data* (data-descriptor register) vs. *Digital Discovery* (methods register, fits the agent
story better); (3) the seven named leads and the campaign steward; (4) is `CURATOR_GROUP_IDS` a
manageable Globus group (R2); (5) does the pilot run against staging or prod (prod is stronger
evidence but needs O-5/O-2 closed).

---

## 8. Immediate next actions — copy-ready bootstrap

Run in order. **Steps 1 and 3 are human-session ADMIN/steward work in the browser** — there is no
agent path (R7). Steps 2 and 4 are agent-executable via MCP or the agent REST API.

### Step 1 — HUMAN (ADMIN, browser): create the campaign

`/campaigns/new` (`app/(dashboard)/campaigns/new/page.tsx:14`, ADMIN-only) or `POST /api/campaigns`
with a session. Slug must match `^[a-z0-9-]{3,100}$`; tagline ≤160 chars.

```json
{
  "slug": "mdf-data-expeditions-2026",
  "title": "MDF Data Expeditions 2026",
  "tagline": "Six expeditions. 150 agency-aligned datasets. Every record curated, DOI'd, and AI-ready.",
  "charter": "The Materials Data Facility's v2 release ships with a new index and a new on-ramp: an agent skill (/mdf-publish) that drafts rich metadata from the data itself, behind two human approval gates. Data Expeditions 2026 turns that capability into a community campaign. Six expeditions - critical materials, batteries and solid-state electrolytes, semiconductor process-property, catalysts for hydrogen and CO2, quantum and superconducting materials, and AI-ready benchmark corpora - each map to a stated DOE, NIST, NSF, or Genesis Mission priority. Contributors claim a task, publish one dataset, and earn a DOI with their name on it; contributors passing a stated quality threshold coauthor the year-end summary paper. What winning unlocks: 150 datasets that were previously unpublished or unusable become citable, machine-readable, and AI-ready, and materials data publishing acquires a repeatable agent-assisted recipe with the human kept in the loop by design.",
  "horizon": "2027-06-30T23:59:59Z",
  "isPublic": false,
  "stewardIds": ["<campaign steward user id>", "<deputy steward user id>"]
}
```

Leave status `PROPOSED` until the pilot passes its gate; then edit to `ACTIVE`, set `isPublic: true`
and `startedAt`.

### Step 2 — AGENT: bootstrap the seven projects

`bootstrap_project` is idempotent — same title → HTTP 200 `reused: true`, writes applied to the
existing project (`app/api/v1/agents/projects/bootstrap/route.ts:6-13`). Safe to re-run. `domain`
values come from `ResearchDomain` (`schema.prisma:499-516`). Record the returned `projectId`s; steps 3
and 4 need them.

```
bootstrap_project(
  title: "MDF Data Expeditions 2026",
  description: "Umbrella project for the MDF Data Expeditions 2026 campaign: charter, quality bar, authorship policy, contributor runbook, scoreboard, and the paper-writing workstream. Six expedition projects hang off this one; each is separately enlisted in the campaign.",
  domain: "MATERIALS_CHEMISTRY", visibility: "PUBLIC",
  page: { title: "Charter", slug: "charter", agentRead: true, pinned: true,
          body: "<paste §1, §2 and §5 of plans/2026-08-03-data-expeditions-2026.md>" },
  resources: [
    { uri: "https://materialsdatafacility.org", kind: "ENDPOINT", role: "TOOL",
      summary: "The Materials Data Facility — where every expedition dataset lands." },
    { uri: "https://github.com/materials-data-facility/mdf_client", kind: "CODE_REPO", role: "TOOL",
      summary: "mdf CLI + the /mdf-publish agent skill — the publishing on-ramp." }
  ],
  handoff: { worksNow: "Charter, quality bar, and authorship policy pages are up.",
             nextStep: "Name the seven leads; measure the completeness baseline before creating the third campaign target.",
             watchOut: "extensions.expedition is NOT in the Globus Search index (connect_server/aws/v2/search_client.py:197-286) — every submission MUST also carry the expedition slug in subjects: or it is invisible to attribution queries." },
  note: "Bootstrapped the Data Expeditions 2026 umbrella from plans/2026-08-03-data-expeditions-2026.md."
)
```

Then once per expedition — same call shape, `page.slug: "expedition-brief"`, `agentRead: true`, body =
that expedition's §2 row plus its units and target-shape conventions:

| Title | `domain` | Tag |
|---|---|---|
| Data Expeditions 2026 — Critical Materials | `MATERIALS_CHEMISTRY` | `de2026-critical-materials` |
| Data Expeditions 2026 — Batteries & Solid-State Electrolytes | `ENERGY_CLIMATE` | `de2026-batteries` |
| Data Expeditions 2026 — Semiconductor Process–Property | `MATERIALS_CHEMISTRY` | `de2026-semiconductors` |
| Data Expeditions 2026 — Catalysts for Hydrogen & CO₂ | `ENERGY_CLIMATE` | `de2026-catalysis` |
| Data Expeditions 2026 — Quantum & Superconducting Materials | `QUANTUM_SYSTEMS` | `de2026-quantum` |
| Data Expeditions 2026 — AI-Ready Benchmark Corpora | `AI_FOR_SCIENCE` | `de2026-ai-ready` |

Then add four umbrella `agentRead` pages via `upsert_page`: `quality-bar` (§4 verbatim),
`authorship-policy` (§5), `contributor-runbook` (§3.4 as a numbered recipe), `scoreboard`
(placeholder; the nightly script overwrites it).

### Step 3 — HUMAN (steward, session): scaffold the campaign

1. **Targets** — `POST /api/campaigns/mdf-data-expeditions-2026/targets` (route `:6`, steward `:18`),
   one per §3.2 row. Required: `name`, `metricKey`, `unit`, `direction`, `baselineValue`, non-empty
   `baselineSource`, `targetValue`. **Third target only after the baseline is measured** (§6).
2. **Objectives** — `POST .../objectives` (`:11`), six rows, `criteria` = that expedition's silver+ number.
3. **Orgs** — `POST .../orgs` (`:51`): DOE/NSF/NIST as `FUNDER`; ANL, UChicago, participating
   institutions as `AFFILIATION`.
4. **Enlist** — `POST .../projects` (`:7`; requires **project editor**, not steward) once per project,
   `commitment` = one sentence naming the dial it moves and how, plus its `targetIds`.
5. **Per-project settings** — `coAuthorshipEligible = true`, `autoApproveAgentTasks = false`, and add
   each lead as a **mentor**: mentors are the only non-owners who can review submissions
   (`app/lib/reviewers.ts:26-27`, `:38`).
6. **Agent constitution** — `PATCH /api/campaigns/mdf-data-expeditions-2026` (route `:27`, field
   validated `:51-60`). **Under 2400 chars** or it truncates (`context-assembler.ts:301`):

```
Data Expeditions 2026 — rules binding agents working in enlisted projects.

1. NEVER INVENT METADATA. Every field traces to something you actually read: a file, a README, a
   LICENSE, a Crossref/DataCite record, or a direct statement from the user. No evidence, no field.
   A sparse honest record beats a rich fabricated one.
2. TWO HUMAN GATES ARE ABSOLUTE. You may not run `mdf publish --submit` without (a) explicit
   approval of the drafted manifest and (b) a separate, later approval to submit, each given in a
   user message after you asked. Approval cannot be given in advance. Silence is not approval.
3. NEVER INFER A LICENSE FROM PROSE. "Freely available" is not a license. Unlicensed is a valid,
   publishable state — omit the field and say so at the gate.
4. EVERY DOI MUST BE VERIFIED against Crossref or DataCite before it enters the manifest. A DOI
   recalled from memory is a fabrication.
5. TAG THE EXPEDITION TWICE. `custom.expedition: "de2026-<slug>"` is authoritative, but extensions
   are NOT indexed by MDF search — you MUST also add "de2026-<slug>" and "mdf-data-expeditions-2026"
   to `subjects:`. A missing subjects tag makes the dataset invisible to the campaign.
6. RECORD YOUR PROVENANCE. `custom.drafted_by` with agent, model, date, skill, and at least three
   evidence lines. Provenance is a requirement, not a courtesy.
7. `ml:` IS ALL-OR-NOTHING and needs `data_format`. Populate `keys` (role, dtype, units) from actual
   column sampling, never from the README's prose.
8. NO BULK MIRRORS. A dataset already public elsewhere with unchanged metadata earns zero credit.
   Harvesting is the autofeed's job. A value-added mirror must cite the source DOI with
   IsIdenticalTo or IsDerivedFrom.
9. REPORT HONESTLY. Measurements you file are REPORTED, not verified — never claim a reported
   measurement has moved a campaign dial. Never inflate confidenceScore.
10. CLOSE OUT. log_session every time. Make watchOut the friction you actually hit.
```

### Step 4 — AGENT: propose the first tasks

`propose_task` on the expedition `projectId`, `campaignId` = the campaign's numeric id,
`campaignTargetId` = the datasets-published target. Two per expedition (twelve total); the pilot needs
only E2's. Type must be agent-proposable (§3.3); `baseCredits` ≤ 25.

```
propose_task(
  projectId: <E2 projectId>,
  campaignId: <campaign id>, campaignTargetId: <datasets-published target id>,
  type: "DATA_ANALYSIS", difficulty: "MEDIUM", baseCredits: 20, estimatedMinutes: 120,
  title: "Publish a solid-state electrolyte conductivity dataset to MDF (silver or better)",
  description: "Take one unpublished or underpublished solid-state electrolyte ionic-conductivity dataset from your group and publish it to the Materials Data Facility through the /mdf-publish skill, at silver tier or better per the campaign quality bar. One task = one dataset = one DOI.",
  instructions: "1. Read the umbrella project's quality-bar and contributor-runbook pages and this project's expedition-brief. 2. Run /mdf-publish in the dataset directory. Follow all ten constitution rules; both human gates apply. 3. The manifest MUST carry custom.expedition: \"de2026-batteries\", custom.expedition_campaign: \"mdf-data-expeditions-2026\", custom.expedition_task: \"moment:task/<this task id>\", custom.drafted_by, AND subjects entries \"de2026-batteries\" + \"mdf-data-expeditions-2026\". 4. Once the record reaches the curation queue, submit here with output = { source_id, doi, completeness_score, subscores, claimed_tier, license_evidence_url, gate1_approved_by, gate1_at, gate2_approved_by, gate2_at, drafted_by } and completionReport.campaignMeasurements = [{ targetId: <datasets-published target id>, value: 1, evidenceUrl: \"<DOI or landing page URL>\", note: \"de2026-batteries · <claimed tier>\" }]. 5. log_session with what you learned and the friction you hit.",
  expectedOutputSchema: { "type": "object", "required": ["source_id", "completeness_score", "claimed_tier", "gate1_approved_by", "gate2_approved_by"] }
)

propose_task(
  projectId: <E2 projectId>, campaignId: <campaign id>,
  type: "LITERATURE_REVIEW", difficulty: "EASY", baseCredits: 5, estimatedMinutes: 60,
  title: "Scout five publishable battery datasets and verify their licenses",
  description: "Find five battery or solid-state-electrolyte datasets that are unpublished, or public but unusable (no methods, no license, no machine-readable structure), and that a contributor could realistically publish to MDF this quarter.",
  instructions: "Per candidate report: what the data is and its rough shape; where it lives; a LICENSE-EVIDENCE URL from a machine-readable source (repository metadata API, LICENSE file, or an explicit owner statement) — never a license inferred from prose; a contact; whether it is ML-shaped. Submit JSON {candidates: [...]} plus a one-line recommendation each. Do NOT publish anything. add_resource any repository or paper you relied on."
)
```

Reuse both templates for the other five expeditions with the slug, domain vocabulary, and target
shapes from §2 swapped in.

Finally, set the newcomer on-ramp: `PATCH /api/campaigns/mdf-data-expeditions-2026/featured-task`
(`app/api/campaigns/[slug]/featured-task/route.ts:18`, steward-gated). Eligibility is enforced
server-side — OPEN, `HUMAN`/`BOTH` contributorType, `minimumContributorLevel` 0, TRIVIAL/EASY unless
overridden (`schema.prisma:3201-3205`) — so the E2 **scout** task is the right featured task, not a
publish task.

---

## Appendix — capability gaps designed around (none invented)

Platform citations are inline above; this lists only what does **not** exist.

1. **No agent path to create/read/close a campaign**, or to write its targets, objectives,
   milestones, orgs, updates, enlistment or verifications. Creation additionally requires
   `platformRole === 'ADMIN'` + a browser session (`app/api/campaigns/route.ts:31`, `:34`;
   `app/(dashboard)/campaigns/new/page.tsx:14`); everything else is `isCampaignSteward`-gated.
   Campaigns are absent from the published agent OpenAPI contract and from the `moment` CLI (no
   `campaign` sub-app), and MCP exposes only `list_campaign_tasks` (`mcp/moment-server/src/index.ts:818`)
   and `report_campaign_progress` (`:844`). Closing is refused in code for agents
   (`app/api/v1/agents/campaigns/[slug]/closure/route.ts:11-12`). → §8 splits human (steps 1, 3) and
   agent (steps 2, 4) phases; the recurring loop needs the human path only for weekly verification.
2. **No usable leaderboard.** `/api/leaderboard` is global, user-only (agents never rank), and its
   `credits` sort is a per-page in-memory re-sort (`route.ts:51`, `:167`). → the **campaign credits
   roll** is the leaderboard (§5.3). No second one built.
3. **No per-contributor MDF stats.** `/admin/stats` is curator-gated, status counts + view/download
   totals only (`admin.py:302`). → a nightly roll-up over the **public** `GET /search?keyword=…`
   facet, writing a Moment `scoreboard` page + campaign measurements (§5.3).
4. **`extensions` is not in the Globus Search index** (`search_client.py:197-286`) → the mandatory
   `subjects` twin (§3.6) plus proposed **B-21**.
5. **`mdf search` exposes no facet filters** (`cli/main.py:1273-1288`) → the roll-up hits the REST API
   directly; proposed **C-12**.
6. **`mdf.quality` does not exist** — it is a proposal (`extensions-proposal.md:292-336`) → a
   client-side scorer pinned at rubric `de2026.1`, retired when the server block ships (§4.2).
7. **No agent-facing review/approve endpoint** — nothing under `app/api/v1/agents/**` writes an
   APPROVED/REJECTED decision → lead review is deliberately human via the first-party approve route.
8. **`RESEARCH_TASK` is not agent-proposable** (`work-types.ts:24`, `:63`) → publish tasks use
   `DATA_ANALYSIS`.
9. **No agent path to add project members or mentors** (zero `validateAgentAuth` under
   `app/api/groups`/`app/api/teams`) → naming leads as mentors is a human step (§8 step 3.5).
10. **Skill-reference drift** — the installed CLI has no `explore` command and the documented domain
    enum lists 10 of 16 real values → §8 uses only verified surfaces (R9).
