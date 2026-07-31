# MDF v2 Release Waves 1–3 — Change Report (2026-07-30)

What changed, why it matters, and what you need to do. Companion to the burndown
(`plans/2026-07-30-release-burndown.md`). All work is on branch
`release-burndown-wave1` in each repo; every wave was cross-model reviewed and
gated on a green test suite before commit.

## Commits

| Repo | Commits (oldest first) |
|---|---|
| `connect_server` | `fb3ca59` baseline snapshot of pre-existing uncommitted work · `87b4142` wave 1 · `9822f55` wave 2 · `78a43b0` wave 3 |
| `mdf-next2` | `c48395f` wave 1 · `a52c56c` wave 3 |
| `mdf_client` | `d235b76` wave 1 · `6a37a82` burndown update |

Test state at the end: backend **189 passed, 1 skipped, 0 failed** (was 121/5-failing);
frontend **142 unit tests + clean typed build** (was 130, no CI); CLI **200 passed**
(unchanged, but CI now actually runs).

---

# For the maintainer

## 1. The sync is now a real service (waves 2–3)

**What exists now.** One command runs the whole one-way legacy→v2 delta sync:

```bash
python aws/v2/scripts/sync_prod_to_v2.py --env staging --run-source manual
# --dry-run for a preview; --no-ssm --state-dir .sync-state/ for local dev
```

It acquires a lock, reads the watermark, runs extract → convert → ingest →
reconcile, writes a JSON run report, and releases the lock in `finally`.
Exit codes: **0** clean · **3** conflicts-only (sync stalled on records a human
must resolve) · **1** errors · **4** another sync holds the lock.

**State lives in SSM** (per environment):
- `/mdf/{env}/sync-watermark` — the delta cursor. Only advances after a fully
  successful, complete, real-search run.
- `/mdf/{env}/sync-lock` — create-if-absent lock; locks older than 2 h are
  treated as stale and evicted.
- `/mdf/{env}/sync-last-report` — JSON report of the last run (status, counts,
  watermark, run source); this is what the admin UI displays.

**Three ways to run it:**
1. **Scheduled:** `.github/workflows/sync-v1-to-v2.yml` runs every 6 hours
   against **staging** (a loud comment marks where to flip to prod at cutover),
   then gates on `reconcile_migration.py --legacy-check`.
2. **Admin button:** the new `/admin` page in the frontend → `POST /admin/sync`
   → triggers that workflow via `workflow_dispatch`. 409 if a sync is running.
3. **Manually:** the command above, from any machine with AWS + Globus creds.

**Correctness properties you can now rely on** (each adversarially reviewed and
regression-tested):
- Strictly one-way, enforced: the ingest and role-grant scripts hard-fail if
  pointed at the legacy index UUID.
- The watermark can no longer lose data: it advances only after ingest +
  verification succeed; `--limit` extracts can't poison it; `--skip-search` and
  mock-search runs can't advance it.
- Legacy **edits propagate**: records carry `sync_content_hash`; changed
  records update both store and search. Search sync is tracked separately
  (`search_synced_hash`, stamped only after Globus accepts the batch), so
  crashes and partial failures **self-heal on the next run**.
- v2 edits are protected: if any version of a dataset has been touched in v2,
  the whole dataset is **frozen out of sync** and reported as a conflict —
  a v1 edit can never silently revert a v2 edit.
- The search index holds one entry per dataset, always the latest published
  version, derived from the store — a delta batch containing an old version
  can no longer overwrite the newest version's search entry.

**Setup you must do before first unattended run** (one-time, credentialed):
1. Create a Globus **confidential client**, grant it *read* on the legacy index
   (`1a57bbe5-…`) and *ingest* on the v2 index; put its id/secret in the GitHub
   repo secrets `GLOBUS_CLIENT_ID` / `GLOBUS_CLIENT_SECRET`.
2. Create an AWS IAM role for GitHub OIDC (`AWS_SYNC_ROLE_ARN` secret) with:
   SSM get/put/delete on `/mdf/*/sync-*`, `cloudformation:DescribeStacks`,
   `lambda:GetFunctionConfiguration`, and DynamoDB read/write on the
   submissions table. (Documented in the workflow header.)
3. For the admin button: create a GitHub fine-grained token with `actions:write`
   on connect_server and deploy with `GitHubSyncToken` + `GitHubSyncRepo`
   template parameters. Without them, `POST /admin/sync` returns a clean 501
   and the UI shows "run it from GitHub Actions instead".

## 2. Backend behavior changes you'll notice operating it

- **"Published" now means "indexed."** Every path that used to set
  `status=published` without a search entry is closed: publish jobs raise on
  ingest failure (SQS retries → DLQ — and the DLQ actually receives failures
  now, via `ReportBatchItemFailures`); metadata edits create the new version as
  `approved` and let the publish job promote it; `/status/update` to
  `published` runs the real pipeline.
- **Curation approve can return 502** (inline mode) when publish fails —
  previously it returned 200 and left an unindexed "published" record.
  Curators can **re-approve** an approved-but-unpublished record to retry;
  `curation_history` records `publish_failed` / `publish_retried`.
- **DOI minting is idempotent** under queue redelivery — no more duplicate
  `-v1.0` DOIs from retried jobs; the "dataset is live" email sends only on the
  actual transition to published.
- **Ownership is enforced on updates**: `POST /submit` with `update:true` (and
  the streams snapshot path, for when streams return) requires the dataset's
  **root-version owner** or a curator. Migrated records (owner `v1-migration`)
  are curator-only until reassigned. Anchoring to the root version means a
  curator edit can't lock out the original owner.
- **File streaming stayed disabled** and is now honest about it: the dead
  files router is unmounted (404, not mystery 400s), the two stream-preview
  endpoints are gone, README/FRONTEND_API say so. Re-enabling is one block in
  `app/__init__.py` — the hardening tests still cover the routers.
- **DynamoDB PITR is enabled** on both tables (deploy to take effect —
  do this before any bulk migration write to prod).
- `make test` now runs the v2 suite (`make test-v1` for the legacy one).

## 3. CI (all three repos — none of it ran before)

- **connect_server**: suite fixed (5 failing tests at HEAD → 0), now 190 tests.
- **mdf-next2**: new `.github/workflows/ci.yml` — `npm ci` + vitest + typed
  production build on every push. The broken `next lint` script and dead
  eslint@8 pins were removed (~170 packages); ESLint 9 flat config is a
  tracked follow-up.
- **mdf_client**: broken `backend-test` job deleted (it referenced the
  untracked `cs/` directory); triggers now cover pushes to every branch.

## 4. New admin surface

- `GET /admin/stats` → gains `sync` health block (report, lock, staleness).
- `GET /admin/sync` → cheap poll endpoint (use this for polling, not stats —
  stats does a full table scan).
- `POST /admin/sync` → curator-gated trigger; 202/409/501/502 semantics above.
- Frontend `/admin` (curator-gated, Specimen style): stats overview + sync
  card (status badge, count grid, watermark, lock state) + "Sync from legacy
  MDF" button with live polling. Admin link in the navbar for curators.

## 5. What's still open (highest-priority remainder of the burndown)

1. **Environment truth** (phase 5): create `api.materialsdatafacility.org`
   (the CLI's prod default is a dead host — C-1/O-5); remove the frontend's
   staging-URL and vercel-preview auth fallbacks (F-2/F-3); unify the CLI's
   three default-service values (~48 sites, C-8); pick one staging gateway and
   tear down the orphan (O-6 — they're confirmed identical).
2. **Release mechanics** (phase 6): merge `design/specimen-phase0` → main
   (F-1); CLI license conflict + PyPI trusted publishing (C-3/C-4); the four
   known CLI bugs (C-6); hide the CLI's 404ing stream commands (C-5); delete
   the two unauthenticated frontend debug routes (F-4); backend deploy
   pipeline + CloudWatch alarms (B-11/B-12).
3. **Known residual risks** (tracked, reviewed, deliberately deferred):
   same-second concurrent approvals of two versions of one dataset can still
   race the search entry (B-16); Globus Search ingest is acceptance-not-
   completion (B-17); delete/withdraw don't clean the search index (B-18);
   DataCite failure doesn't block publish (B-19).
4. **Cutover sequence** (O-1): freeze v1 → final sync → clean
   `--legacy-check` reconcile → flip frontend flag + CLI default → DNS/308s →
   2-week canary of the 6-hourly reconcile gate → decommission v1.

---

# For users (researchers submitting/consuming datasets)

- **Your datasets can no longer be hijacked.** Previously any approved
  submitter could push a "new version" of anyone's dataset; now only the
  original submitter or a curator can update a dataset.
- **Search results are correct for versioned datasets.** Publishing a new
  version used to be able to leave the search index showing the old version
  (or worse, overwrite the new one with it). The index now always reflects
  the latest published version.
- **No more ghost datasets.** A dataset either publishes fully (metadata +
  DOI + searchable) or stays visibly in review while the system retries —
  previously it could show "published" yet be unfindable in search.
- **No duplicate DOIs** from infrastructure retries.
- **Edits made on the legacy MDF now reach the new MDF within ~6 hours**
  during the transition, and edits made on the new MDF can't be overwritten
  by the sync.
- **Datasets migrated from the old MDF are curator-managed for now** — if you
  need to update one you originally submitted, contact a curator (ownership
  reassignment is on the roadmap).
- **File streaming upload is not part of this release** — datasets publish by
  reference to Globus data sources (the CLI's `mdf stream …` commands and the
  documented streaming endpoints will be hidden/removed before release).
- Curators additionally get: retryable approvals (a transient failure no
  longer wedges a submission) and the new Admin page with sync health and a
  one-click "Sync from legacy MDF".
