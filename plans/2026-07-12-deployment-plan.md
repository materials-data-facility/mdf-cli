# MDF v2 — Path to Production Deployment

**Date:** 2026-07-12
**Scope:** Three repos — backend (`connect_server/aws/v2`), CLI (`mdf_client`), frontend (`mdf-next2`)
**Audience:** An implementing agent (any capability level) plus the maintainer. Every task states its file paths, exact steps, and a verification command. Tasks marked **[JUDGMENT]** involve a design decision or credentials and should be done by a strong agent or a human; everything else is mechanical.

---

## Part 0 — System map (current state)

### How the three pieces fit together

```
 researcher ──> mdf CLI (mdf_agent, Typer)  ──┐
 researcher ──> mdf-next2 (Next.js 16)      ──┼──> MDF Connect v2 API (FastAPI on Lambda, SAM stack mdf-connect-v2-{env})
 agent/LLM  ──> skill/handlers.py           ──┘         │
                                                        ├── DynamoDB  mdf-submissions-{env} / mdf-streams-{env}
                                                        ├── SQS async jobs -> async_worker Lambda (publish, DOI, profile, embeddings)
                                                        ├── Globus Search index (public reads, service-credential ingest)
                                                        ├── Globus Transfer + HTTPS storage (NCSA endpoint) / S3 / local
                                                        ├── DataCite (DOI mint), SES (email), OpenAI (embeddings)
                                                        └── S3 embedding snapshot (client-side semantic search)

 legacy prod (v1): separate stack — per-endpoint Docker Lambdas + Globus Automate Flows,
 deployed from connect_server/aws/*.py via .github/workflows/deploy-lambda.yml on dev/prod branches.
 v1 and v2 run in parallel on disjoint infrastructure.
```

### Backend (`connect_server/aws/v2`) — status: **staging is live and populated**

- FastAPI app (`v2/app/`), Mangum handler for Lambda (`v2/app/main.py`), uvicorn for local dev.
- SAM template `aws/template.yaml` provisions everything app-side: 2 Lambdas, HTTP API, 2 DynamoDB tables (`DeletionPolicy: Retain`), SQS + DLQ, S3 buckets, EventBridge cleanup schedule. `aws/samconfig.toml` already has real staging **and prod** parameters (prod Search index `fed3e94e-ddab-4f2d-97fd-8243f36dc676`, DataCite prefix `10.18126`, curator/submitter group UUIDs).
- Staging deployed at `https://hjccjf3eqg.execute-api.us-east-1.amazonaws.com/staging` with **904 production datasets already migrated** (per `v2/MANUAL_TEST.md`).
- Migration tooling exists and has been run: `v2/scripts/extract_mdf_production_datasets.py` → `convert_production_datasets.py` → `ingest_converted_datasets.py`, plus drift checker `reconcile_migration.py` (non-zero exit on drift). Legacy IDs resolve via sparse GSI `legacy-source-id-index` + `_resolve_published()` fallback in `v2/app/routers/cards.py`.
- Test suite: 126 tests across `v2/test_v2_*.py`, fully mocked (sqlite/mock DataCite/mock Search), run by `.github/workflows/test-v2.yml`.
- Known gaps (each becomes a task below): streams router disabled while docs advertise it; `FRONTEND_API.md` missing 6 live endpoints; `aws/DEPLOYMENT.md` stale; Lambda bundle includes the entire legacy v1 codebase (`CodeUri: .`); in-memory (per-instance) rate limiting; no CloudWatch alarms/dashboards; secrets passed as CFN params → plaintext Lambda env vars; reserved concurrency 10 (API) / 3 (worker); SES/DataCite/Globus prod setup is manual (checklist in `v2/v2.md:427-468`).

### CLI (`mdf_client`) — status: **feature-complete, blocked on repo hygiene**

- Live package: `mdf_agent` 0.2.0 (`src/mdf_agent/`, PEP 517, console script `mdf`). 200 tests passing. Feature audit (`FEATURE_STATUS.csv`, 105 rows; `feature_audit/SUMMARY.md`) validated the full publish→curate→approve→resubmit lifecycle against live staging.
- Blockers (tasks below): CI job `backend-test` references **untracked** `cs/` and will fail on any clean checkout; dead duplicate legacy package (`mdf_connect_client/` + root `setup.py`) shadows packaging; `LICENSE` (Apache-2.0) vs `pyproject.toml` (`MIT`) mismatch; SDK defaults to `prod` while CLI defaults to `staging` (16 call sites); README documents hidden aliases as primary commands; 4 known behavior bugs (see B4); `skill/skill.md` documents 5 of ~30 handler functions and isn't packaged; Sphinx docs are legacy-only.
- **URL discrepancy to resolve:** CLI staging is `https://3xicgt0g7l.execute-api.us-east-1.amazonaws.com/staging` (`src/mdf_agent/core/backend_client.py:11-16`) but the frontend and backend `MANUAL_TEST.md` use `https://hjccjf3eqg...`. Two different API Gateway IDs — one is stale or there are two staging stacks. Resolve before anything else (task X1).

### Frontend (`mdf-next2`) — status: **functional on staging, needs production config + cleanup**

- Next.js 16 / React 19 App Router, Vercel-shaped deployment (no `vercel.json`; fallback auth redirect is `https://mdf-next2.vercel.app/auth`). Routes: `/`, `/search`, `/detail/[source_id]`, `/publish` (1,861-line page), `/curation`, `/auth`, `/guides`, `/spotlight[/omol25]`, plus proxy API routes.
- Dual v1/v2 data path gated by `NEXT_PUBLIC_USE_V2_API`; v2 client (`lib/api/v2-client.ts`) hardcodes the staging gateway as its default base URL.
- Blockers (tasks below): `/tdb` page renders raw OAuth tokens into the DOM; `typescript.ignoreBuildErrors: true`; heavy `console.log` of auth internals; staging URL as prod fallback; large dead-code inventory (two Tailwind configs, two UI dirs, two DnD libs, two chart libs, three spotlight variants, orphaned components); 119 unit tests but zero component/E2E tests; a prior 125-row feature audit exists at `docs/audit/`.
- Visual identity issues are handled separately in `2026-07-12-frontend-design-doc.md` (same folder).

---

## Part A — Backend to production

Order matters: A1–A3 are safe cleanups; A4–A6 are the production prerequisites; A7 is the deploy; A8 is validation.

### A1. Repo/doc hygiene (mechanical)

1. **Fix stale deployment doc.** Replace the contents of `aws/DEPLOYMENT.md` with a short pointer: "See the root `README.md` and `v2/v2.md`; this file previously documented the pre-v2 layout." (It currently references a nonexistent `cs/aws` path and `v2/submit.py`.)
2. **Document the 6 undocumented endpoints** in `v2/FRONTEND_API.md`: `GET /search/semantic`, `POST /embed`, `GET /datasets/{source_id}/related`, `GET /admin/embeddings/status`, `POST /admin/embeddings/rebuild`, `POST /admin/embeddings/snapshot`. Copy request/response shapes from `v2/app/routers/search.py:118-171` and `v2/app/routers/admin.py:66-190`. Follow the existing doc's format exactly.
3. **Resolve the streams doc/reality mismatch. [JUDGMENT]** The `streams` router is disabled (`v2/app/__init__.py:67,70`) but `v2.md`, root `README.md`, and `template.yaml` document the full stream workflow, and the CLI ships `mdf stream *` commands that 404. Decision needed: (a) re-enable and harden streams, or (b) mark the feature "coming soon" in all three docs and hide the CLI subcommand. Default recommendation: **(b)** for first prod launch — fewer moving parts; the `files.py` router stays as-is.
   - Verify: `grep -rn "stream" v2/v2.md README.md` shows the chosen state consistently.

### A2. Shrink the Lambda bundle (mechanical)

`CodeUri: .` in `aws/template.yaml:205,302` bundles every legacy v1 module into the v2 zip. Fix without moving code: create `aws/.samignore` excluding the legacy v1 files (`auth.py`, `submit.py`, `status.py`, `submissions.py`, `dynamo_manager.py`, `automate_manager.py`, `globus_automate_flow.py`, `flow_action.py`, `organization.py`, `source_id_manager.py`, `tests/`, `infra/`, `*.md`, `.aws-sam/`). If `.samignore` proves unreliable with the installed SAM CLI version, instead restructure `CodeUri` to a build directory populated by `deploy.sh`.
- Verify: `sam build && ls .aws-sam/build/ApiFunction/` no longer contains `submit.py` or `dynamo_manager.py`, and `pytest v2/test_v2_*.py` still passes.

### A3. Monitoring and alerting (mechanical, high value)

Add to `aws/template.yaml`:
1. CloudWatch alarms: `ApiFunction` Errors > 0 over 5 min; `AsyncWorkerFunction` Errors; `AsyncJobsDLQ` `ApproximateNumberOfMessagesVisible` > 0; API Gateway 5xx rate; both Lambdas Throttles > 0.
2. One SNS topic `mdf-connect-v2-alerts-{env}` with an email-subscription parameter; wire all alarms to it.
3. A minimal CloudWatch dashboard (request count, p50/p99 latency, error rate, DLQ depth, Dynamo throttles).
- Verify: `sam validate`, deploy to dev, `aws cloudwatch describe-alarms --alarm-name-prefix mdf-connect-v2` lists the alarms.

### A4. Production account/service prerequisites **[JUDGMENT — human/credentialed]**

Execute the checklist already written in `v2/v2.md:427-468`. Concretely:
1. **SES:** verify the sending domain, exit sandbox, set `SES_FROM_EMAIL` + `EnableEmails=true` + `CURATOR_EMAILS` for prod in `samconfig.toml`.
2. **DataCite:** confirm prod repository credentials for prefix `10.18126`; run `v2/scripts/setup_datacite_ssm.sh` for the prod env; set `UseMockDatacite=false`. (`deploy.sh` already refuses prod deploys without SSM creds.)
3. **Globus:** register the production confidential client; store in SSM `/mdf/globus-client-id`, `/mdf/globus-client-secret`; grant it ingest rights on the prod Search index with `v2/scripts/grant_search_index_role.py`; confirm the index is publicly readable with `v2/scripts/test_search_token.py`.
4. **OpenAI key** for embeddings in prod (or explicitly deploy with embeddings disabled and skip semantic search at launch).
5. Raise `ApiReservedConcurrency` from 10 to a launch value (recommend 50) and `AsyncWorkerReservedConcurrency` from 3 to 10 in prod overrides — the defaults are cost-control values, too low for public traffic.
6. **Custom domain:** create `api.materialsdatafacility.org` (ACM cert + API Gateway custom domain + Route53). The CLI already expects exactly this hostname for prod (`backend_client.py:12`). Add the domain + base-path mapping to `template.yaml` behind a `CreateCustomDomain` condition so it's code, not console-clicking.

### A5. Secrets at runtime (recommended before prod, not a blocker)

Today DataCite/Globus/OpenAI secrets flow CFN parameter → Lambda env var (plaintext at rest, visible to anyone with `lambda:GetFunctionConfiguration`). Move to runtime resolution:
1. In `v2/config.py`, add a helper that reads a value from env **or**, if the env var is of the form `ssm:/path`, fetches (and caches) it via `boto3` SSM `get_parameter(WithDecryption=True)`.
2. Use it for `DATACITE_PASSWORD`, `GLOBUS_CLIENT_SECRET`, `OPENAI_API_KEY`. Grant both Lambda roles `ssm:GetParameter` on `/mdf/*` in `template.yaml`.
3. Change `samconfig.toml` prod overrides to pass `ssm:/mdf/prod/...` references instead of values.
- Verify: unit test the helper with a mocked boto3 client; deploy to dev; `aws lambda get-function-configuration` shows no secret values.

### A6. Rate limiting posture (decide, small change)

The in-process limiter (`v2/app/middleware.py`) is per-Lambda-instance, so it under-limits when Lambda scales out. Do **not** build a distributed limiter now. Instead: treat API Gateway throttling (`HttpApiThrottleRate`/`Burst` params, already in the template) as the real limit; set prod values deliberately (recommend rate 50, burst 100 to start); keep the in-process limiter as belt-and-suspenders. Document this in `v2/v2.md`.

### A7. Production deploy + data load

1. `./deploy.sh prod` (uses existing `samconfig.toml` prod overrides). Confirm stack `mdf-connect-v2-prod` completes.
2. Run the migration pipeline against prod: `extract_mdf_production_datasets.py` → `convert_production_datasets.py` → `ingest_converted_datasets.py --env prod`, then `reconcile_migration.py --env prod` and require exit 0.
3. Smoke test with `v2/MANUAL_TEST.md`, substituting the prod URL. Minimum: health, anonymous search, card/detail/citation for one legacy ID (verifies the `legacy-source-id-index` path), authenticated submit→curate→approve→publish on a throwaway dataset, DOI draft created in DataCite.

### A8. Ongoing verification

- Add a scheduled GitHub Actions workflow (cron, daily) that runs `reconcile_migration.py` against prod and fails loudly on drift (see Part D — this becomes the sync monitor).
- Keep `.github/workflows/test-v2.yml` as the PR gate; add `sam validate` to it.

---

## Part B — CLI to release (PyPI)

### B1. Unbreak CI (mechanical, do first)

`.github/workflows/testing-work.yml` has a `backend-test` job with `working-directory: cs/aws` — but `cs/` is untracked (`git ls-files | grep '^cs/'` is empty), so the job fails on any clean checkout. **Delete the `backend-test` job** (backend CI lives in `connect_server/.github/workflows/test-v2.yml`; this repo should not test the backend). While there, confirm the client job still runs `pip install -e ".[extractors]"` + pytest on 3.10/3.11/3.12.
- Verify: `act` locally or push a branch and watch Actions go green.

### B2. Remove the dead legacy packaging (mechanical)

1. `git rm -r mdf_connect_client/ setup.py setup.cfg` (root copies only — `legacy/` keeps its own frozen copy, including `legacy/setup.py`).
2. Delete stale `mdf_connect_client.egg-info/`.
3. Move `tests/test_connect_client.py` to `legacy/tests/` (it imports the legacy package and is already excluded from the normal run).
4. Delete the untracked local `cs/` directory (workboard item #9; backend source of truth is the sibling `connect_server` repo per `.continuity/handoff.md`).
- Verify: `python -m build` produces exactly one wheel, named `mdf_agent-*`; `pytest tests/` → 200 passed.

### B3. Fix packaging metadata (mechanical + one decision)

1. **License [JUDGMENT]:** `LICENSE` is Apache-2.0; `pyproject.toml:11` says MIT. Recommendation: keep Apache-2.0 (matches the project's history and NOTICE file) — change `pyproject.toml` to `license = { text = "Apache-2.0" }`.
2. Add `[project.urls]` (Homepage, Repository, Issues) to `pyproject.toml`.
3. **Package name [JUDGMENT]:** decide the PyPI distribution name (`mdf-agent`? successor release of `mdf_connect_client`?). Recommendation: publish as `mdf-agent` (new name, new API) and add a final `mdf_connect_client` release whose long_description points here.
4. Package the agent skill: rewrite `skill/skill.md` to cover all ~30 functions in `skill/handlers.py` (enumerate them from `skill/handlers.py:53-631`), and either include `skill/` via `[tool.setuptools.package-data]` or explicitly document it as repo-only.
- Verify: `python -m build && twine check dist/*`.

### B4. Fix the four known behavior bugs (mechanical; specs already exist in `feature_audit/SUMMARY.md:99-107`)

1. `mdf setup` writes an `mdf.yaml` missing title/author so its own recommended `publish --preflight-only` fails — pass the collected values through to `manifest_init`.
2. `mdf login --token <bad>` reports success without validating — call `GET /auth/check` and fail on non-200.
3. `mdf list --json` ignores `--status/--search/--latest-only` — apply filters before serialization.
4. `mdf stream upload` exits 0 on total failure — propagate a non-zero exit code. (If A1.3 chose to hide streams, hide the subcommand instead.)
- Verify: add one regression test per fix in `tests/`; `pytest tests/` green.

### B5. Unify the default service (mechanical, single decision) **[JUDGMENT on the value]**

SDK/`BackendClient`/`MDFAgent` default `service_instance="prod"` (16 sites: `backend_client.py:69`; `agent.py:694,780,800,822,844,863,883,904,926,947,965,989,1017,1043,1160`) while the CLI resolves to `staging` (`core/config.py:126-130`). Pick **one** default — recommendation: `prod` everywhere once A7 is done, `staging` everywhere until then — and define it in exactly one constant (`core/config.py`) that both `resolve_service()` and every SDK default import.
- Verify: `grep -rn 'service_instance="' src/ | grep -v DEFAULT` returns nothing.

### B6. Docs truth pass (mechanical)

1. README: mark hidden aliases as aliases (`update`, `validate`, `whoami`, `pending`, `approve`, `reject`, `versions`, `edit`, and the `manifest`/`stream`/`backend` groups are `hidden=True` in Typer); fix `MDF_SSL_VERIFY` (code default is `True` — README says `false`).
2. Replace legacy Sphinx sources (`docs/sphinx/source/mdf_connect_client.rst`) with `mdf_agent` API docs, or delete Sphinx and adopt the `20260301-walkthrough.md` content as a mkdocs site (recommended — the walkthrough is the best doc in the repo).
3. Sweep the root: move `20260201-MDF-PLAN.md`, `20260206-completed.md`, `20260301-*.md`, `v2-review.md`, `MDF_BACKEND_ARCHITECTURE*.md`, `work-completed.md`, `scratch.md`, `walkthrough*.md`, `gpt54-*.md` into `plans/archive/` so the repo root is a package, not a diary.

### B7. Backend catch-up for client-ready fields

`domains`, `external_doi`, `external_url`, `external_source` are wired client-side (`work-completed.md`) but the backend drops them. In `connect_server/aws/v2/metadata.py`, add the four fields to `DatasetMetadata`, include them in the search GMeta entry (`search_client.py`) and the card builder (`dataset_card.py`). Run `examples/test_domains_external_e2e.py` from this repo against local backend to confirm round-trip.

### B8. Release

Tag → GitHub release → existing `python-publish.yml` builds and uploads. Then `pip install mdf-agent` on a clean machine; run `mdf --version`, `mdf search "perovskite"` anonymously, and one authenticated `mdf publish --preflight-only`.

---

## Part C — Frontend to production

Visual redesign is the design doc's job; this part is functional/deployment only.

### C1. Security and hygiene (mechanical, do first)

1. **Delete `app/tdb/page.tsx`** (renders raw OAuth tokens into the DOM at line 20) and the empty `app/pages/thermodynamic-data.tsx`. If the thermo visualization matters, re-home the component behind auth without the token dump.
2. Strip the auth-debug `console.log`s from `app/providers.tsx`, `app/hooks/useGlobusAuthInit.ts`, `app/api/submit/route.ts` (token lengths, localStorage contents). Keep error-path `console.error`s.
3. Set `typescript.ignoreBuildErrors: false` in `next.config.js:10-13`; fix whatever type errors surface (budget a few hours). Also bump `eslint-config-next` from 14.0.1 to match Next 16.
4. Dead-code sweep: delete `app/components/home-sections.tsx`, `home-infrastructure.tsx`, `home-narrative.tsx`, `home-features.tsx`, `home-stats.tsx`, `public/glowing-books.png`, empty `mdf-test/`; remove `react-beautiful-dnd` (keep `@hello-pangea/dnd`) and pick one chart lib (keep `recharts`, drop `chart.js` + `react-chartjs-2` unless the thermo component needs them).
- Verify: `npm run build` green with type checking on; `npx vitest run` → 119 passing.

### C2. Configuration for production (mechanical)

1. `lib/api/v2-client.ts:25`: replace the hardcoded staging gateway default with `https://api.materialsdatafacility.org` (created in A4.6) and make the build **fail** if `NEXT_PUBLIC_MDF_V2_API_URL` is unset in production (`assert` in a config module rather than silent fallback).
2. `app/providers.tsx:30` + `.env.example`: default `NEXT_PUBLIC_AUTH_REDIRECT` to `https://www.materialsdatafacility.org/auth`; register that redirect on the Globus OAuth client.
3. Make the OAuth client ID (`app/providers.tsx:15`) and the v1 Search index UUID (duplicated in `app/search/page.tsx:35` and `app/detail/[source_id]/page.tsx:36`) env-configurable with the current values as defaults; single source in `lib/config.ts`.
4. `.env.example`: document every `NEXT_PUBLIC_*` var actually read; fix `AGENTS.md:19` (references `GLOBUS_SCOPES` vars that don't exist).
5. Vercel project: set prod env (`NEXT_PUBLIC_USE_V2_API=true`, prod API URL, prod redirect), attach the production domain.

### C3. Feature parity before flipping the flag **[JUDGMENT on scope]**

The v2 backend supports things the UI never exposes (`docs/audit/contract-surface.md`): withdraw/resubmit, version diff, file preview/sample, semantic search, related datasets on non-Foundry pages, sort control on search. Minimum bar to flip `NEXT_PUBLIC_USE_V2_API=true` as default: **sort control on `/search` + withdraw/resubmit buttons on `/detail` for owners** (both have client methods already in `lib/api/v2-client.ts`; UI-only work). The rest land with the redesign (see design doc, which sequences them).

### C4. Test floor (mechanical)

Add Playwright with three smoke tests against a preview deploy: (1) home renders and nav works; (2) `/search?q=perovskite` shows results from staging; (3) `/detail/<known-source-id>` renders title, authors, citation block. Wire into CI on PR. This is deliberately small — it guards deploys, not correctness.

### C5. Launch checklist

Preview deploy → run C4 smokes + the 10-step manual checklist in `__tests__/README.md:209-274` → promote to production → verify auth round-trip on the prod domain (Globus redirect whitelist is the classic failure) → watch Vercel logs for the first day.

---

## Part D — Operating v1 (prod) and v2 side-by-side: one-way sync tooling

**Goal:** researchers keep publishing to current prod; v2 stays a faithful, continuously-updated mirror until cutover. Direction is strictly prod → v2.

### What already exists (reuse, don't rebuild)

The one-shot pipeline in `connect_server/aws/v2/scripts/` is 80% of the answer: `extract_mdf_production_datasets.py` (pull from prod), `convert_production_datasets.py` (v1→v2 schema, already handles `dc/mdf/custom` → flat), `ingest_converted_datasets.py` (idempotent-ish writes into store + search, exposes reusable `build_submission_record`/`apply_env`), `reconcile_migration.py` (drift detection, CI-friendly exit codes). Records carry `legacy_source_id`; the GSI + card-router fallback keep old links working. Data files never move — both systems point at the same Globus endpoints, so sync is **metadata-only** (cheap, fast, safe).

### What to build (the sync service)

**D1. `sync_prod_to_v2.py`** — a new script in the same `scripts/` dir composing the existing three stages with:
- **A watermark** (last successful sync timestamp) stored in SSM parameter `/mdf/{env}/sync-watermark`. Each run extracts only v1 records ingested/updated after the watermark (the v1 Globus Search index is public and queryable by ingest date; fall back to full extract + diff if timestamps prove unreliable).
- **Idempotent upsert keyed by `legacy_source_id`:** before writing, hash the converted record (stable JSON, sorted keys) and compare with a `sync_content_hash` attribute stored on the v2 record; skip if equal, upsert + re-ingest to search if changed. Add `sync_content_hash` handling in `ingest_converted_datasets.py`'s record builder.
- **DOI policy — never mint during sync.** Synced records carry their existing prod DOI verbatim; `mint_doi` is never triggered. (The publish job path is bypassed entirely — sync writes records directly as `status="published"`, as the migration already does.)
- **Deletion policy — tombstone, don't delete.** If a record present in v2 (with `legacy_source_id`) disappears from the prod extract, flag it in the sync report; a human decides. Never auto-delete.
- **Collision guard:** v2-native submissions must not claim a `source_id` that an incoming legacy record will map to. Add a check in `submission_utils.generate_source_id()` that consults the legacy index.
- **Dry-run mode** (`--dry-run` prints the change set) and a JSON run-report (created/updated/skipped/tombstoned counts).

**D2. Scheduling + monitoring.** Two acceptable homes; recommend the first:
- *GitHub Actions cron* (every 6h) in `connect_server`: runs D1 with prod-read + v2-write credentials from repo secrets, then runs `reconcile_migration.py`; any non-zero exit or nonempty tombstone list fails the workflow → notification. Cheapest to build, fully visible in PR-land.
- *EventBridge → Lambda* inside the SAM stack if you want it fully in-AWS later (reuse the async-worker pattern).
- Either way: emit the run-report as a workflow artifact and (optionally) a CloudWatch metric `SyncedDatasets`/`DriftCount` so A3's dashboard shows sync health.

**D3. User-visible freshness.** Add `last_synced_at` to the v2 record during sync and surface it in `GET /admin/stats`, so the frontend can show "mirror current as of …" during the dual-operation period.

**D4. Cutover checklist** (write it in `connect_server/aws/v2/v2.md` when D1–D3 land): announce a submission freeze window → final sync run → `reconcile_migration.py` clean → flip CLI default service (B5) and frontend flag (C2/C5) → point `publish-prod.materialsdatafacility.org` DNS or add HTTP 308 redirects from the v1 API Gateway to v2 equivalents → keep the sync job running in report-only mode for two weeks as a canary → decommission v1 flows.

---

## Part E — What would make this an amazing project for researchers

Ranked by leverage; the first group is mostly wiring what already exists.

**Expose what the backend already does (cheap, high impact):**
1. **Semantic search in the UI** — the `/search/semantic` endpoint, embeddings pipeline, and even an S3 client-side snapshot all exist. "Describe the data you need" is a headline feature for researchers; today it's invisible.
2. **File preview and sample browsing** on dataset pages — `/preview/{id}`, `/files`, `/sample` endpoints exist; the UI never calls them. Seeing inside a dataset before downloading is the #1 trust builder for data reuse.
3. **Related datasets everywhere** — `/datasets/{id}/related` exists; render it on every detail page, not just Foundry ones.
4. **Author-facing impact stats** — `view_count`/`download_count` are already in the store; show them to dataset owners ("your dataset was viewed 214 times this month" emails are a retention loop).

**Reduce time-to-first-publication:**
5. **A 10-minute golden path**: `pip install mdf-agent && mdf setup && mdf publish` with the B4.1 fix so the scaffold actually validates; a matching "publish your first dataset" page in the frontend that mirrors it. Measure the funnel.
6. **DOI-first import** — the frontend's CrossRef/DataCite importer is genuinely good; extend it to the CLI (`mdf import <doi>`) so metadata entry starts at 80% complete in both surfaces.

**Meet researchers where their tools are:**
7. **An MDF MCP server** — thin wrapper over `/search`, `/card/{id}`, `/citation/{id}`, `/preview`. The `card` endpoint was practically designed for LLM consumption, and `skill/handlers.py` already implements ~30 agent-facing operations; packaging this as an MCP server makes every AI assistant a MDF client. Distinctive, low-cost, very 2026.
8. **"Open in Colab" / notebook launchers** on dataset pages — generate a starter notebook (foundry-ml load snippet + citation cell) per dataset.
9. **conda-forge feedstock** for the CLI alongside PyPI; materials people live in conda.

**Trust and identity:**
10. **ORCID linkage** on authorship (Globus Auth exposes linked identities) so publications count for careers.
11. **Versioned-DOI story told well** — the backend's version/DOI chain (`IsVersionOf`/`HasVersion`) is more rigorous than most repositories; document it and render version history + diffs in the UI (endpoint exists).
12. **A real docs site** (mkdocs-material from the `20260301-walkthrough.md` content) at `docs.materialsdatafacility.org`, and a public **status page** (even a static one fed by the A3 alarms).

**Community/curation flywheel:**
13. **Organization pages** (org filter already exists in the API) — give MIP/DOE projects branded collection pages; this is how facilities recruit depositors.
14. **Curation SLA + transparency** — show submitters where they are in the queue and median approval time; the curation history data already exists.

---

## Suggested execution order (across parts)

| Wave | Tasks | Notes |
|---|---|---|
| 1 | X1 (resolve staging-URL mismatch), B1, B2, C1 | Unblocks everything; all mechanical |
| 2 | A1, A2, A3, B3, B4, B6, C2 | Parallel-safe, disjoint repos |
| 3 | A4, A5, A6 | Credentialed/human |
| 4 | A7, A8, B5, B7, C3, C4 | Prod exists; defaults flip |
| 5 | D1–D3 | Sync service while both run |
| 6 | C5 launch, E-items, D4 cutover | Steady state |

**X1 (do first):** determine whether `3xicgt0g7l` (CLI) or `hjccjf3eqg` (frontend + backend MANUAL_TEST.md) is the live staging gateway — `curl <url>/health` on both — and update the loser (`src/mdf_agent/core/backend_client.py:11-16` or `lib/api/v2-client.ts:25` / `.env.example`). If both are live, document which stack is canonical in `connect_server/aws/v2/v2.md` and tear the other down.
