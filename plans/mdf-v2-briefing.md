# MDF v2 — Agent Briefing: three repos, how they connect, how to deploy staging

Living reference for coding agents. Last verified **2026-09-25** (read-only audit of all three repos, AWS
`describe-*` calls, Vercel/GitHub read APIs, and live staging probes). Items marked **UNVERIFIED** were not
confirmed by execution. When this file and a Moment page disagree, re-verify; the release plan for the current
push is `plans/2026-09-25-beta-launch-plan.md`, and the item ids it cites (B-*, C-*, D-*, F-*, O-*) come from
`plans/2026-07-30-release-burndown.md`.

---

## 1. What MDF v2 is

The Materials Data Facility publishes, curates, and indexes materials-science datasets with DOIs. v2 replaces the
v1 "Connect" service (Globus Automate flow + Docker Lambdas) with:

- a **FastAPI backend on AWS Lambda** (SAM) backed by DynamoDB, SQS, S3, Globus Search, DataCite, SES, OpenAI;
- a **Next.js 16 frontend** on Vercel;
- a **Python CLI / SDK / MCP server / agent skill** (`mdf-cli` on PyPI).

Core invariant: *published implies indexed*. Search shows one entry per dataset (its latest visible version).
v1 remains the system of record until cutover; staging holds a migrated copy of the v1 corpus (≈935 datasets in
the index, 962 rows in DynamoDB).

## 2. The three repos

All checkouts are under `~/Desktop/git`. **Note:** the backend is `connect_server/aws/v2`, *not*
`mdf-next2/aws/v2` (that path does not exist).

| Local dir | Role | Remote (visibility) | Remote default | Working branch (2026-09-25) |
|---|---|---|---|---|
| `connect_server` | Backend. v2 lives in `aws/v2`; the rest of `aws/` plus `automate/`, `infra/`, `dockerize/`, `scripts/`, `tests/` is **v1** | `materials-data-facility/connect_server` (public) | `prod` = **v1, auto-deploys on push** | `release-burndown-wave1` @ 29cd0bb, 3 commits ahead of origin (7b9f914, 6af624c, 29cd0bb), **unpushed and undeployed** |
| `mdf_client` | CLI + SDK. Import package `mdf` (`src/mdf`), PyPI `mdf-cli`. Also holds `plans/` (the source of truth for release planning) | `materials-data-facility/mdf-cli` (private) | `main` @ 2df500f | `cli-styling` @ 0e17769 (v0.3.0), 4 commits ahead of `origin/main`, fast-forwardable, no upstream |
| `mdf-next2` | Frontend (Next.js 16 App Router) | `blaiszik/mdf-frontend` (private) — **not** `blaiszik/mdf-next2` | `main` @ 13bf595 | `release-fixes-2026-09-04` @ 9da837a, 3 commits ahead, fast-forwardable, no upstream |

Look-alikes to ignore: `mdf-next3` (older frontend), `ARCHIVED-cs-orphaned-connect_server-checkout`. Do **not**
delete `mdf-index-backups/`: it holds rollback data (`legacy-index-backup-20260731.jsonl.gz`, 1,988,999 entries;
`legacy-datasets-backup-20260731.jsonl(.gz)`, 940 datasets). `foundry` (MLMI2-CSSI/foundry, branch `mdf-cli`) is a
downstream consumer that imports CLI internals (`mdf.core.config`, `mdf.auth.globus`,
`mdf.core.backend_client.BackendClient`, private `_api_url_for_service`) and pins `mdf-cli>=0.2.0`. Treat those
symbols as a public API.

Stale local metadata: `mdf_client`'s `origin/HEAD -> origin/master` is left over from the old repo; the remote only
has `main`. `connect_server/AGENTS.md` describes v1 (Python 3.7, dev→main flow) and does not apply to v2.

### 2.1 Backend map — `connect_server/aws`

| Path | What |
|---|---|
| `template.yaml` | SAM template (v2 only): `ApiFunction` + `AsyncWorkerFunction` (python3.12, `CodeUri: .`, no Layers), HTTP API, DynamoDB, SQS+DLQ, S3, SNS alarms, EventBridge schedules |
| `samconfig.toml` | `dev` / `staging` / `prod` parameter sets |
| `deploy.sh` | **The only supported deploy entry point** (reads SSM, guards ciphertext, prunes build via `.samignore`) |
| `README.md` | Correct GSI index-migration note |
| `DEPLOYMENT.md` | Generic, partly stale |
| `v2/app/__init__.py` | Router mounting. `streams` and `files` routers are **intentionally unmounted** (B-1) |
| `v2/app/auth.py`, `v2/app/deps.py`, `v2/app/middleware.py` | Globus bearer auth, groups binding, `can_view_dataset`, rate limiter, path guard |
| `v2/app/routers/` | `submissions`, `search`, `cards`, `curation`, `preview`, `admin` (+ unmounted `streams`, `files`) |
| `v2/store.py`, `v2/search_client.py`, `v2/search.py` | DynamoDB/sqlite store; Globus Search client (GMeta shape); search + fallback |
| `v2/async_jobs.py`, `v2/async_worker.py` | Publish pipeline (SQS worker): DOI mint → search ingest → status flip |
| `v2/datacite.py`, `v2/citation.py`, `v2/doi_utils.py`, `v2/link_health.py`, `v2/embedding_snapshot.py`, `v2/email_utils.py` | Integrations |
| `v2/scripts/` | Data ops: sync, reconcile, backfills, search wipe/rebuild/backup, purge |
| `v2/test_v2_*.py` | Test suite (≈632 tests) |
| `v2/v2.md`, `v2/FRONTEND_API.md` | Service docs and the frontend contract |

Tests (mamba env `mdf-connect-v2`, Python 3.12):
```bash
cd ~/Desktop/git/connect_server/aws
STORE_BACKEND=sqlite AUTH_MODE=dev USE_MOCK_DATACITE=true USE_MOCK_SEARCH=true PYTHONPATH=. \
  mamba run -n mdf-connect-v2 pytest v2/test_v2_*.py -q -p no:xdist   # same as `make test`
sam validate --lint
```

### 2.2 CLI map — `mdf_client`

| Path | What |
|---|---|
| `src/mdf/cli/main.py` | Typer app, most commands |
| `src/mdf/cli/{admin_cmd,dataset_cmd,mcp_cmd}.py`, `src/mdf/cli/theme.py` | Sub-apps; shared visual language |
| `src/mdf/core/backend_client.py` | HTTP client + service→URL map (line ~34) |
| `src/mdf/core/config.py` | `DEFAULT_SERVICE = "staging"` ("flip to prod at cutover") |
| `src/mdf/core/agent.py` | High-level publish/clone logic |
| `src/mdf/auth/globus.py` | Native-app login; tokens at `~/.config/mdf/tokens.json` |
| `src/mdf/mcp/server.py` | Read-only MCP server (`mdf mcp serve`) |
| `src/mdf/skill*/` | `/mdf-publish` agent skill + handlers |
| `src/mdf/importers/`, `src/mdf/extractors/`, `src/mdf/models/` | DOI import, metadata extraction, pydantic models |
| `legacy/` | v1 client; do not extend |
| `plans/` | Release planning (burndown, deployment plan, this briefing) |

Tests: `pip install -e ".[extractors]" pytest && pytest tests/ -q -p no:xdist`. `tests/conftest.py` sets
`_TYPER_FORCE_DISABLE_TERMINAL=1` and `NO_COLOR=1` (Typer force-colors under GitHub Actions). After the
`mdf_agent`→`mdf` rename, stale envs need `pip install -e . --no-deps`.

### 2.3 Frontend map — `mdf-next2`

| Path | What |
|---|---|
| `app/` | Routes: `/`, `search`, `detail/[source_id]`, `publish`, `my-datasets`, `curation`, `admin`, `data`, `spotlight`, `cli`, `guides`, `auth` |
| `app/api/*` | Server proxy routes (`submit`, `submissions`, `status`, `doi-lookup`) |
| `app/providers.tsx` | Globus Auth client config and scopes |
| `lib/api/base-url.ts`, `lib/api/v2-client.ts`, `lib/api/auth-header.ts`, `lib/api/feature-flags.ts` | API base URL, typed client, auth headers, `isV2Enabled()` |
| `components/ui` | Design-system primitives (shadcn-style) |
| `DESIGN.md`, `PRODUCT.md`, `AGENTS.md` | Design system, product brief, repo conventions |
| `e2e/`, `__tests__/`, `*.test.ts(x)` | Playwright + Vitest |

Tests: `npm run test:run` (Vitest), `npm run build` (includes type-check), `npm run test:e2e` (Playwright, mocked API).

## 3. How the pieces connect

```
 browser ──► Vercel (mdf-frontend) ──NEXT_PUBLIC_MDF_V2_API_URL──┐
 terminal ─► mdf CLI / MCP / skill ──service map / MDF_API_URL───┤
                                                                 ▼
                         API Gateway HTTP API ─► ApiFunction (FastAPI/Mangum)
                                                   │  ├─ DynamoDB mdf-submissions-{env}, mdf-streams-{env}
                                                   │  ├─ Globus Search index (read; per-env UUID)
                                                   │  └─ SQS mdf-async-jobs-{env} ─► AsyncWorkerFunction
                                                   │         DataCite mint → Search ingest → status=published
                                                   └─ S3 embeddings / stream files; OpenAI embeddings; SES
```

**API base URLs**
- CLI (`src/mdf/core/backend_client.py`): `staging` → `https://3xicgt0g7l.execute-api.us-east-1.amazonaws.com/staging`
  (live); `prod` → `https://api.materialsdatafacility.org` (**not live**, custom domain not created); `dev` →
  `https://api-dev.materialsdatafacility.org` (not live); `local` → `http://127.0.0.1:8080`. Resolution:
  `--service` > `MDF_SERVICE` > `~/.config/mdf/config.json defaults.service` > `DEFAULT_SERVICE`. `MDF_API_URL`
  overrides outright.
- Frontend: all `NEXT_PUBLIC_*`, **inlined at build time**. `NEXT_PUBLIC_MDF_V2_API_URL` (required in production
  builds; dev falls back to staging), `NEXT_PUBLIC_USE_V2_API=true` selects v2 (otherwise the v1 path queries the
  legacy index directly and posts to `publish-prod.materialsdatafacility.org`), `NEXT_PUBLIC_AUTH_REDIRECT`
  (optional; default `${origin}/auth`).

**Globus Auth**
| Thing | Id |
|---|---|
| Backend confidential client (SSM `/mdf/globus-client-id`, `/mdf/globus-client-secret`; **shared by all envs**) | `86e4853e-9bdd-4ea5-9130-e4a0b0638400` (search-index *writer*, not admin) |
| MDF resource server / scope clients request | `https://auth.globus.org/scopes/4d5f8e8b-a61d-40d8-bb58-5a3f5d1d200a/connect` |
| Groups token (clients send `X-Groups-Token`, server binds it to the bearer identity) | scope `urn:globus:auth:scope:groups.api.globus.org:view_my_groups_and_memberships` |
| CLI native app | `074cebcc-19ad-4332-bbf2-78402291b659` |
| Frontend client (redirects must be registered here) | `88166393-89d3-4905-bdba-8759d7ad1f55` |
| Curator group | `3ce2c53e-3752-11e8-891c-0e00fd09bf20` |
| Submitter group (`RequiredGroupMembership`) | `cc192dca-3751-11e8-90c1-0a7c735d220a` |
| Break-glass `CuratorUserIds` | `c8741264-d274-11e5-bee7-f30dff9f1ea8`, `c8745ef4-d274-11e5-bee8-3b6845397ac9` (plain comma, never `\,`) |
| NCSA MDF collection | `82f1b5c6-6e9b-11e5-ba47-22000b92c6ec` |

Read Moment #204 page `globus-groups-corrected` before touching auth. Dependent-token exchange between the two app
ids fails (400 UNAUTHORIZED_CLIENT) and was abandoned in favor of scope-matched direct grants.

**Globus Search indexes**
- staging `ab19b80b-0887-4337-b9f8-b8cc7feb1fdc` (also `TestSearchIndexUUID` on staging)
- prod `fed3e94e-ddab-4f2d-97fd-8243f36dc676` (empty)
- **legacy v1 `1a57bbe5-5272-477f-9d31-343b8258b7a5`: read-only forever.** Only the scripts refuse to write it;
  the runtime relies on `SearchIndexUUID` alone.
- `SearchSubjectBase=https://materialsdatafacility.org/detail` is pinned deliberately; changing it orphans subjects.

**DataCite**: staging → `https://api.test.datacite.org`, prefix `10.23677` (real *test* DOIs; they do not resolve
on doi.org). prod → `https://api.datacite.org`, prefix `10.18126`. Mock prefix `10.99999` only with
`UseMockDatacite=true`. **OpenAI**: SSM `/mdf/{env}/openai-api-key`, `text-embedding-3-small` (1536-d).

## 4. Environments and AWS resources (account 557062710055, us-east-1)

Only **staging** exists. There is no `mdf-connect-v2-prod` or `-dev` stack, and `./deploy.sh prod` hard-errors
today because `/mdf/prod/datacite-*` and `/mdf/prod/openai-api-key` are absent.

| Resource | Staging |
|---|---|
| Stack | `mdf-connect-v2-staging` (UPDATE_COMPLETE, last updated 2026-08-05T04:20Z) |
| API | `https://3xicgt0g7l.execute-api.us-east-1.amazonaws.com/staging` (canonical) |
| Lambdas | `mdf-connect-v2-staging-ApiFunction-*`, `mdf-connect-v2-staging-AsyncWorkerFunction-*` |
| DynamoDB | `mdf-submissions-staging` (PK `source_id`, SK `version`; PITR, deletion protection, Retain; GSIs `org-submissions`, `status-submissions`, `user-submissions`, `legacy-source-id-index`), `mdf-streams-staging` |
| SQS | `mdf-async-jobs-staging` (visibility 180 s, maxReceive 3), `mdf-async-jobs-dlq-staging` (14 d) |
| S3 | `mdf-sam-deployments-staging-557062710055`, `mdf-stream-files-staging-557062710055`, `mdf-embeddings-staging-557062710055` |
| SNS | `mdf-connect-v2-alarms-staging` → `blaiszik@uchicago.edu` (10 alarms) |
| SES | `EnableEmails=false` on staging (no mail is sent) |
| SSM | `/mdf/globus-client-{id,secret}`, `/mdf/staging/datacite-{username,password,api-url,prefix}`, `/mdf/staging/openai-api-key` |
| Frontend | Vercel project `mdf-frontend`; its **"Production" target is MDF staging** → `staging.materialsdatafacility.org` |

`hjccjf3eqg.execute-api…` is a **foreign-account duplicate** of staging that shares the staging search index but
has its own DynamoDB. It still answers. Never use it (O-6: decommission).

The public **www.materialsdatafacility.org** site is a *different* Vercel project (`mdf-next2`, git-linked to
`blaiszik/mdf-next2`). Pushing to that repo changes the public site.

**AWS credentials**: the `default` profile is a root `aws login` session, the one used for deploys and backfills.
It expires; the fix is `aws logout`, move `~/.aws/sso/cache` aside, `aws login`, then paste the URL into a browser
already signed in to 557062710055. A human must do this. Profile `mdf-deploy` (IAM user `mdf-deployer`) works for
read-only `describe-*`/`get-*` but is denied `dynamodb:ListTables`/`Query`, `logs:FilterLogEvents`, `ses:*`,
`iam:List*`. Whether it can `sam deploy` is UNVERIFIED; assume you need the root session.

## 5. Deploying to staging

### 5.1 Backend

**Prerequisites**: `aws sts get-caller-identity` shows 557062710055; `sam` ≥ 1.162; `python3.12` on PATH
(`use_container=false`); a `python3` with `tomllib` (if samconfig parsing fails, `deploy.sh` silently falls back to
`Environment=$env AuthMode=production`); `kms:Decrypt` for SecureStrings.

1. Push the working branch: `git -C ~/Desktop/git/connect_server push origin release-burndown-wave1`.
   **Never push or merge to `prod` or `dev`** (see Hazards). The first push is also the first run of
   `test-v2.yml` on this branch.
2. Pre-flight:
   ```bash
   cd ~/Desktop/git/connect_server/aws
   ./deploy.sh status staging
   aws dynamodb describe-table --table-name mdf-submissions-staging \
     --query 'Table.GlobalSecondaryIndexes[].[IndexName,IndexStatus]'
   ```
3. **Pending one-time GSI swap** (`status-submissions` → `curation-queue-index`). CloudFormation allows one GSI
   create *or* delete per update, and HEAD's template has both changes. The in-file comment at
   `template.yaml:604-611` claiming the file is already in deploy-1 state is wrong. Use two deploys. Until
   beta-plan item A1 lands (a `ValidationException` → scan fallback at `v2/store.py:517`), new code that queries a
   missing index makes `/curation/pending` return 500. **Use add-first** until then; once A1 lands, either order is
   safe:
   - Deploy 1: temporarily restore the `status-submissions` GSI block (and its `status` AttributeDefinition)
     alongside the new `curation-queue-index`. That is one net GSI add. Run `./deploy.sh staging` and wait for
     `curation-queue-index` ACTIVE.
   - Deploy 2: `git checkout -- template.yaml` (HEAD, without `status-submissions`), then `./deploy.sh staging`.
     That is one GSI delete.
   - Never commit the temporary deploy-1 edit. The documented alternative (remove-first) works but opens the 500
     window; `aws/README.md` describes it.
4. Normal deploys (after the swap): `cd ~/Desktop/git/connect_server/aws && ./deploy.sh staging`.
   `deploy.sh` runs `sam build`, prunes via `.samignore`, then
   `sam deploy --config-env staging --parameter-overrides "<samconfig overrides> GlobusClientId=… DataCite…=… OpenAIApiKey=…"`.
   It refuses if DataCite creds are missing while `UseMockDatacite=false`, or if a value looks like `AQICAH…`
   ciphertext.
   **When stdin is not a TTY (any agent harness), `deploy.sh` passes `--no-confirm-changeset`** (`deploy.sh:47`).
   Review `sam deploy --no-execute-changeset` output first, or have a human run the command.
5. Smoke tests:
   ```bash
   API=https://3xicgt0g7l.execute-api.us-east-1.amazonaws.com/staging
   curl -s $API/health                                   # {"status":"ok","service":"mdf-v2"}
   curl -s $API/openapi.json | python3 -c 'import json,sys;print(sorted(json.load(sys.stdin)["paths"]))' \
     | grep -c link-health                              # 1+ once HEAD is live
   aws sqs get-queue-attributes --queue-url https://sqs.us-east-1.amazonaws.com/557062710055/mdf-async-jobs-dlq-staging \
     --attribute-names ApproximateNumberOfMessages     # 0
   mdf admin pending --service staging                 # curation queue works
   ```
   Then check `LOG_LEVEL=INFO` on the Lambda (it is DEBUG today), watch the DLQ and alarms for a day, and run one
   end-to-end publish (§5.4).

**Do not** use `make deploy-staging`, `make deploy-prod`, a bare `sam deploy` (they bypass the prune and the SSM
and ciphertext guards), or `./deploy.sh quick <env>` (zips `v2/` without dependencies; the template has no
Layers, so it likely breaks both Lambdas, UNVERIFIED). CloudFormation parameters left out of the overrides keep
their *previous stack values*, so keep samconfig overrides explicit. `CorsConfiguration` must stay literal
values only, and `ApiReservedConcurrency` must be ≥ 50.

### 5.2 Post-deploy data ops (run from `connect_server/aws`)

Prefix every command with `PYTHONPATH=. mamba run -n mdf-connect-v2 python v2/scripts/`. The scripts need AWS
creds (root session) and Globus confidential creds (`GLOBUS_CLIENT_ID`/`GLOBUS_CLIENT_SECRET` or SSM). Always
dry-run first.

| Step | Command | Notes |
|---|---|---|
| Record-shape backfill | `backfill_record_shape.py --env staging --dry-run` then `--execute` | Idempotent; writes are conditional on `updated_at`; stamps `curation_queue`. Run after the GSI swap. |
| License backfill | `backfill_license_mdf_open.py --env staging --dry-run\|--execute` | **Already executed on staging** 2026-09-04 (163 rows). |
| Search index backup | `backup_search_index.py --env staging -o staging-index-YYYYMMDD.jsonl.gz` | Always before a wipe |
| Search wipe | `wipe_search_index.py --env staging --execute` | **Run twice** (offset paging skips during deletes; client is writer, not admin). Prod needs `--allow-prod`. |
| Search rebuild | `rebuild_search_from_converted.py --env staging -i <converted.json> --execute` | **Required after deploying HEAD**: GMeta is flattened, so filters and facets return nothing until re-ingest. `converted-latest.json` does not exist locally, and a legacy-derived rebuild drops v2-native datasets. Replaced by `reindex_search_from_store.py` (beta-plan item A2) once it lands. |
| Embeddings | `mdf admin rebuild-embeddings --yes --service staging` (or POST `/admin/embeddings/rebuild` as a curator) | Needed for related-by-similar and semantic search |
| Link health | `POST $API/admin/link-health/run`; then `GET /admin/link-health/summary` | No CLI wrapper yet. Easiest: `BackendClient.authenticated(service_instance="staging", interactive=False)._request("POST", "/admin/link-health/run", json_data={})`. By hand, mirror the CLI's headers: `Authorization: Bearer <auth.globus.org token>`, `X-MDF-Token: <4d5f8e8b… connect token>`, `X-Groups-Token: <groups token>` (tokens in `~/.config/mdf/tokens.json` under `data.DEFAULT.<resource_server>`). The connect token as the bearer gets 401 |
| v1→v2 sync | `sync_prod_to_v2.py --env staging --run-source manual --dry-run [--json]` | Exit 3 means conflicts only. SSM lock and watermark under `/mdf/staging/sync-*`. The 6-hourly cron is **not running** (see CI). |
| Parity check | `reconcile_migration.py --env staging --legacy-check --check-search --json [--strict-search]` | |
| Stale search entries | `purge_stale_search_entries.py --env staging [--execute] [--resolve-via-api $API]` | Use `--resolve-via-api` without `dynamodb:Query` |

Verify after re-ingest: `curl "$API/search?q=band"` returns five non-empty facet groups; a legacy-id detail page
renders; citations include DOIs.

### 5.3 Frontend (Vercel)

- Project `mdf-frontend` (scope `ben-blaisziks-projects`), git-linked to `blaiszik/mdf-frontend`,
  `productionBranch main`, Node 24.x. `.vercel/project.json` → `prj_C1gUCNJPhH7jPBiszT1zLCh5HlyI`.
- **Deploy to staging = push to `main`** (production target = `staging.materialsdatafacility.org`). Git
  auto-deploy has never fired: the only deployment (`dpl_rEj6f2LDHeDKWXE68b6qaSX6fKSy`, commit 13bf595) came from
  the CLI. Fallback: `cd ~/Desktop/git/mdf-next2 && vercel deploy --prod`.
- Env vars (`NEXT_PUBLIC_AUTH_REDIRECT`, `NEXT_PUBLIC_MDF_V2_API_URL`, `NEXT_PUBLIC_USE_V2_API`) are set for
  **Production only**, so branch preview builds have no API URL and throw at runtime. `*.vercel.app` URLs are
  behind Vercel SSO; verify through the custom domain.
- Verify:
  ```bash
  vercel ls mdf-frontend
  curl -s -o /dev/null -w '%{http_code}\n' https://staging.materialsdatafacility.org/detail/does-not-exist-xyz  # expect 404
  ```
  Also check a per-dataset `<title>` and a Globus login round trip (redirect registered on client `88166393…`).

### 5.4 End-to-end publish check (staging)

As a submitter-group member: `mdf publish ./tiny-dir --title "[E2E-TEST] …" --author "…" --submit`, approve with
`mdf admin approve <id>` (or `/curation`), and poll `mdf status <id>` until `published` with a `10.23677/…` DOI.
Then confirm the card, search hit, and citation. Clean up with the delete route. Examples in the backend suite are
gated on `MDF_E2E_ALLOW_STAGING_WRITES=1` and tag records `[E2E-TEST]`.

### 5.5 CLI release

- Version is `src/mdf/version.py` (`__version__`). **The git tag does not set the version** (tags `v0.1`/`v0.11`
  both shipped 0.2.0). PyPI latest is **0.2.0**.
- `.github/workflows/python-publish.yml` runs on GitHub **release published**: build, `twine check`, then PyPI
  trusted publishing (environment `pypi`).
- Steps: fast-forward `main` to the release branch; date the `CHANGELOG.md` entry; create a GitHub release
  (tag `vX.Y.Z` matching `version.py`); verify with `pipx install mdf-cli==X.Y.Z && mdf --version`.
- Keep `DEFAULT_SERVICE="staging"` until `api.materialsdatafacility.org` exists.

## 6. CI

| Repo | Workflow | Trigger / effect |
|---|---|---|
| connect_server | `deploy-lambda.yml` (**v1**) | Push to `dev`/`prod` → rebuilds and deploys **v1** Lambdas. No path filter on origin/prod; the filter exists only in unpushed 6af624c, and even that filter matches files changed on this branch. |
| connect_server | `deploy_flow.yml` (v1) | Release published → deploys the Globus Automate flow |
| connect_server | `test-v2.yml` | Push/PR to main, prod, v2-backend-curation, release-burndown-wave1: `sam validate --lint` + pytest |
| connect_server | `deploy-v2.yml` | `workflow_dispatch` + typed confirmation. **Inoperable**: not on the default branch, no `AWS_DEPLOY_ROLE_ARN` secret, no GitHub environments |
| connect_server | `sync-v1-to-v2.yml` | 6-hourly cron. **Not running** (schedules only fire from the default branch; secrets missing) |
| mdf-cli | `testing-work.yml` | Any push / PRs to main; pytest on 3.10–3.12 |
| mdf-cli | `python-publish.yml` | Release published → PyPI |
| mdf-frontend | `ci.yml` | Every push: `npm ci`, `test:run`, `build`, Playwright (mocked API). Both runs on `main` failed 2026-08-04; the fix (e450ae1) is unpushed |

## 7. State on 2026-09-25 (deployed vs local)

- **Backend staging** runs code from about 2026-08-05. All three local commits are undeployed. They add:
  - Groups-token identity binding
  - spoof-proof rate limiter
  - path guard
  - `can_view_dataset` on status/versions/diff/stats
  - public-only author index and snapshot
  - source_id grammar and DOI collision guard
  - flattened GMeta
  - the GSI swap
  - link-health endpoints and sweep
  - versioned DOI landing URLs
  - agent-format card

  The DataCite deploy fix *is* live (the Lambda env has decrypted creds, and a 2026-08-05 run published with a
  real test DOI). The 2026-09-04 handoff line "publishing is broken on staging" is stale; confirm with §5.4.
- **Frontend staging** runs 13bf595 (origin/main); three local commits are undeployed.
- **CLI**: 0.2.0 on PyPI; 0.3.0 local and unreleased.
- **Data**: the license backfill is applied in DynamoDB but not in the search index; the index still uses nested
  GMeta.

## 8. Hazards (never-do list)

1. **Never push or merge to `connect_server` `prod` or `dev`.** Both redeploy v1 Lambdas.
2. **Never push to `blaiszik/mdf-next2`.** It serves the public www site. The v2 frontend repo is
   `blaiszik/mdf-frontend`.
3. **Never write the legacy index `1a57bbe5-…`.**
4. **Never use or deploy through `hjccjf3eqg`.**
5. Don't bypass `deploy.sh`. Remember it auto-confirms changesets without a TTY.
6. The GSI swap needs two deploys. Never put two GSI mutations in one update. Never change an existing GSI's
   projection in place.
7. Mock DOIs are decommissioned. Deploys require real DataCite creds via SSM `--with-decryption`; ciphertext only
   fails at publish time (DataCite 404).
8. A live DataCite **test** credential is in public git history (`648dd9b:aws/samconfig.toml`; also 542907c,
   906d5be, a6ae919, cf2e959). Rotation is pending (maintainer).
9. `GET /card` and `GET /detail` **write** (increment `view_count`). Probing them is not side-effect free.
10. Deploying HEAD without re-ingesting search leaves every filter and facet empty.
11. Frontend preview builds break (env vars are production-only).
12. Related-works field names: use explicit relation types and descriptions. The old generic relation labels were
    silently discarded before; never reintroduce them.
13. Cheap-lane code that touches credentials needs cross-family review (a lane once attached a user's Globus data
    token to publisher-controlled URLs). Infra changes need a "can CloudFormation apply this to the *existing*
    stack" check, not just `sam validate`. Validate strict validators against live ids (a source_id regex once
    broke 32 of 1,547 real ids).
14. Laptop memory budget (24 GB): at most two heavy processes (type-check, test runner, build, headless browser)
    at once across all agents; test runners single-worker; delegated lanes don't type-check.

## 9. Where else to look

- `plans/2026-09-25-beta-launch-plan.md`: current one-week plan.
- `plans/2026-07-30-release-burndown.md`: item ids and history.
- `plans/2026-07-12-deployment-plan.md`: original cutover design (sync service, DNS, redirects).
- Moment project 204:
  - CLI: `mamba run -n moment moment --api-url http://localhost:3000 --json --quiet -p 204 orient`. The
    `--api-url` flag is mandatory.
  - Trust order: `globus-groups-corrected` and `wave-2026-09-04-release-fixes` supersede `next-steps-runbook`,
    which wrongly says staging uses mock DataCite, `/mdf/staging/globus-client-id`, and `blaiszik/mdf-next2`.
- `connect_server/aws/v2/v2.md` ("Production Deploy Checklist"), `aws/README.md` (index migration).
