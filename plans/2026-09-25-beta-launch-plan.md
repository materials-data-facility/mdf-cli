# MDF v2 Public Beta: One-Week Launch Plan

**Written** 2026-09-25 · **Target** public beta announcement **Fri 2026-10-02** (Day 1 = Sat 09-26, Day 7 = Fri 10-02). If the week starts Monday instead, shift every day by two and the target becomes Mon 10-05
**Scope** all three repos. Read `plans/mdf-v2-briefing.md` first for layout, environments and deploy mechanics.

**Where item ids come from:**
- `SEC-*`: `plans/audits/2026-09-25-backend-security.md` (private; do not copy into the public connect_server repo)
- `GAP-*`: `plans/audits/2026-09-25-backend-gaps.md`
- `CLI-*`: `plans/audits/2026-09-25-cli.md`
- `FE-*`: `plans/audits/2026-09-25-frontend-ux.md`
- `B-/C-/D-/F-/O-*`: `plans/2026-07-30-release-burndown.md`

---

## 0. Decisions (maintainer, 2026-09-25)

| # | Decision | What it means for the week |
|---|---|---|
| D1 | **Launch = public beta on staging**: `staging.materialsdatafacility.org` + API `3xicgt0g7l`. | v1 stays the system of record. Prod cutover (O-1, C-1 default flip, DNS/redirects) is a later milestone and is out of scope. |
| D2 | **Discovery beta.** Anyone can search, view, cite, clone, and use the CLI/MCP. Publishing stays gated to the MDF submitter group. | Staging mints DataCite **test** DOIs (`10.23677`), which never resolve on doi.org. Beta chrome on the site and in the CLI must say so. Migrated datasets carry real v1 DOIs (`10.18126`) that resolve to the v1 www site. |
| D3 | **SEC-H3 (token audience) accepted for the beta**, fixed before cutover. | Record the accepted risk in `connect_server/aws/v2/v2.md`. Revisit when the per-env Globus clients land (user track). |
| D4 | **CLI clean break in 0.3.0.** | Delete the 19 hidden aliases, the `backend` and `stream` groups, and the unused `mdf.skill` handlers. One name per action; `setup` becomes `init`. |
| D5 | **SEC-M3 policy kept.** Owner edits to text fields on published records republish immediately; data-location edits re-curate. | Document it as intended in `v2.md`. |
| D6 | (engineering) **GAP-3 is a bug.** Owners and curators must be able to open their own pending, rejected and restricted records. | Backend and frontend fix this week. |
| D7 | (engineering) **Make the GSI swap order-proof.** Add a `ValidationException` → scan fallback at `v2/store.py:517`, so either deploy order is safe. | Both CloudFormation updates are still required (one GSI op per update). |
| D8 | (engineering) **Re-index search from the DynamoDB store**, not from converted legacy JSON. | New `reindex_search_from_store.py`. The documented input `converted-latest.json` doesn't exist, and a legacy rebuild drops v2-native rows. |
| D9 | (engineering) **noindex on staging during the beta.** | Staging URLs move at cutover, and DOIs point at www. Detail-page JSON-LD still ships now so cutover has it. |
| D10 | **Prod prerequisites: user track, about 1 hour, Days 6–7.** | They unblock cutover, not the beta. |

## 1. Beta exit criteria (all must hold by Day 7)

- [ ] **Backend.**
  - Staging runs HEAD plus this week's fixes: `/openapi.json` lists `/admin/link-health/*`, `curation-queue-index` is ACTIVE, `status-submissions` is gone, and `LOG_LEVEL=INFO`.
  - The re-run security probe set passes: no anonymous `/stats` or `/related` leak of restricted data, forged `X-Groups-Token` → 401, `/search?offset=9990` → 4xx fast, not a table scan.
- [ ] **Search.**
  - `/search?q=band` returns five non-empty facet groups.
  - The index count equals the store's published-latest-visible count.
  - v1 datasets published since the last sync (33 prod DOIs registered since June; newest 2026-09-18) are present.
- [ ] **End-to-end publish.** Two runs:
  - CLI: `publish --submit` → curator approve → `published` with a `10.23677` DOI → search hit → card → citation contains the DOI → `clone --plan` returns in under 10 s.
  - Web: the publish form → the same checks.
- [ ] **Frontend.**
  - `main` deployed through Vercel git integration (or `vercel deploy --prod` as fallback) with v2 on.
  - FE-B1…B6 done; beta banner and noindex live; `/detail/does-not-exist` → HTTP 404.
  - A legacy id (`…_v1.1`) and a v1 DOI landing URL (`/detail/<uuid>-1.0`) both render the right dataset and version.
- [ ] **CLI.**
  - `mdf-cli 0.3.0` on PyPI.
  - CLI-U1…U4 and U10 fixed.
  - The `--json` logged-out contract test is green.
  - foundry's test suite passes against 0.3.0.
- [ ] **Security.**
  - SEC-H2, SEC-M1 and SEC-M4 fixed; SEC-L8 alarms added.
  - SEC-H3 and SEC-M3 recorded in `v2.md`.
- [ ] **Suites.** All three are green: backend pytest, CLI pytest, frontend vitest + `npm run build`. CI is green on every pushed branch.
- [ ] **Docs.** Briefing, Moment #204 handoff, and the burndown status block updated.

## 2. Starting state (what the 2026-09-25 audits found)

1. **Staging runs code from 2026-08-05. None of the 2026-09-04 hardening is live.**
   - Every live security probe hit pre-hardening code.
   - Deploying HEAD alone closes SEC-H1: groups-token identity binding, the `X-User-Id` rate-limit bypass, anonymous `/stats`, and the restricted-dataset leak via `/related` and the snapshot.
2. **Deploying HEAD empties every filter and facet** until search is re-indexed, because GMeta was flattened in 7b9f914.
3. **The DataCite fix is already live** (Lambda env has decrypted creds; a 2026-08-05 publish produced a real test DOI). The 2026-09-04 handoff line "publishing is broken" is stale. No one has run an E2E publish on HEAD.
4. **v1 is still minting.** The staging corpus is stale (935 index entries vs 962 store rows, plus everything since), and the 6-hourly sync cron has never run.
5. **CLI: four release blockers.**
   - `mdf show` crashes on every real dataset (CLI-U1).
   - Login-requiring commands open an interactive prompt on stdout under `--json` (CLI-U2).
   - `clone --plan` walks Globus Transfer for minutes (CLI-U3).
   - The documented `publish --update` fails, and the hidden `update` drops metadata (CLI-U4).
6. **Backend correctness.**
   - Citations drop the DOI on migrated records (GAP-1).
   - Legacy ids crash the detail page through a 200 `{success:false}` from `/versions` (GAP-2).
   - Owners can't view their own pending records (GAP-3).
   - A large search `offset` forces a full DynamoDB scan (SEC-H2).
7. **Frontend.**
   - Identifier-trust problems: FE-B1…B5.
   - Mobile layout pushes the first search result to about 2,000px (FE-B6).
   - The v2 flag defaults to v1.

## 3. How the week runs

- **Main loop (orchestrator)** owns the critical path: re-index tool, deploys, data ops, verification. It also owns `connect_server/aws/template.yaml`, every push, every gate, and all cross-lane contracts.
- **Three build lanes** run in parallel from Day 1:
  - **Lane B** (backend): `connect_server/aws/v2/**` except `store.py:517` and the new script on Day 1.
  - **Lane C** (CLI): `mdf_client/src`, `tests`, `README.md`, skills.
  - **Lane D** (frontend): `mdf-next2/**`.
- File ownership is disjoint. Lanes never push, deploy, or edit another lane's repo.
- **Execution** uses the `/wave` skill for model routing. Mandatory **cross-family review** before merge applies to:
  - anything touching auth, tokens or credentials (B4, B5, C2);
  - any template/infra change, with the explicit check "can CloudFormation apply this to the *existing* stack?"
- **Memory budget.** Write this block into every lane prompt:
  > 24 GB laptop. At most two heavy processes (type-check, test runner, production build, headless browser) at
  > once across ALL lanes. Delegated lanes don't type-check; the main loop runs one per wave. Test runners run
  > single-worker (`-p no:xdist`, `vitest --no-file-parallelism`). At most three concurrent build lanes. Lanes run
  > only targeted test files; full suites and `npm run build` run at main-loop gates, one at a time.
- **Gates.**
  - **G1** (Day 1 end): review and suites for the deploy payload.
  - **G2** (Day 2): deploy chain and live verification.
  - **G3** (Day 4): lane merges and deploy #3.
  - **G4** (Day 6): release.
  - At every gate, snapshot each lane's diff to a patch file so reviewers read frozen input.

## 4. Critical path (main loop)

### Day 1: build the deploy payload
1. **A1 GSI fallback (D7).** In `v2/store.py` (`list_curation_queue` around :517), catch `ValidationException` for a missing index and fall back to the filtered scan. Add a unit test. **S**
2. **A2 `v2/scripts/reindex_search_from_store.py` (D8).**
   - Scan the store and select, per `source_id`, the latest published version with its ACL (`resolve_visible_to`).
   - Call `GlobusSearchClient.build_gmeta_entry(sub, version_count=…)` and `batch_ingest` in pages.
   - Flags: `--env`, `--dry-run` (default), `--execute`, `--limit`, `--json` report. Refuse the legacy index `1a57bbe5-…`, and refuse prod without `--allow-prod`.
   - Test against `MockGlobusSearchClient` and sqlite.
   - **S** (both functions already exist in `v2/search_client.py:415,694`).
3. **Ride-along fixes from Lane B:** B1–B3 (below) merge at G1 so they ship in the first deploy.
4. **Foundry baseline.** Run foundry's suite (single worker) against the *current* `mdf` before any Lane C change lands. Its `mdf-cli` branch has a dirty tree and no upstream, so it may already be red; G4 compares like with like.
5. **G1.**
   - Full backend suite, single worker: `cd connect_server/aws && STORE_BACKEND=sqlite AUTH_MODE=dev USE_MOCK_DATACITE=true USE_MOCK_SEARCH=true PYTHONPATH=. mamba run -n mdf-connect-v2 pytest v2/test_v2_*.py -q -p no:xdist`
   - `sam validate --lint`
   - Commit.

### Day 2: deploy and verify (needs the user for `aws login`, about 5 min)
1. **User:** refresh the root AWS session (briefing §4) and approve the pushes.
2. **Push.** Run `git push origin release-burndown-wave1` for `connect_server`. This is the first `test-v2.yml` run on the branch; fix forward if CI-only failures appear. Push `cli-styling` for `mdf_client`. **Never** push `connect_server` `prod`/`dev`.
3. **Deploy 1.** Temporarily keep the `status-submissions` block beside `curation-queue-index` (add-first), then `./deploy.sh staging`.
   - With A1 in place, remove-first is also safe.
   - Review the changeset first: the harness has no TTY, so `deploy.sh` auto-confirms. Run `sam deploy --no-execute-changeset` or have the user run the command.
4. **Deploy 2.** `git checkout -- template.yaml`, then `./deploy.sh staging`. Confirm the GSI state with `describe-table`.
5. **Record-shape backfill.** `backfill_record_shape.py --env staging --dry-run`, then `--execute`.
6. **Search.**
   - Back up: `backup_search_index.py --env staging -o staging-index-20260927.jsonl.gz` (copy it next to `mdf-index-backups/`).
   - `wipe_search_index.py --env staging --execute` **twice**.
   - `reindex_search_from_store.py --env staging --execute`.
   - Verify facets and the count. Do deploy → re-index in one sitting; facets are empty in between.
7. **v1 catch-up.**
   - `sync_prod_to_v2.py --env staging --run-source manual --dry-run --json`, then without `--dry-run`.
   - `reconcile_migration.py --env staging --legacy-check --check-search --json`.
8. **Derived data.**
   - `mdf admin rebuild-embeddings --yes --service staging`.
   - `POST /admin/link-health/run` (briefing §5.2).
9. **Live verification.**
   - Re-run the SEC and GAP probe sets (GET only).
   - The CLI E2E publish (briefing §5.4) with an `[E2E-TEST]` title, then clean up.
   - Watch the DLQ and alarms.
10. **Frontend baseline deploy.** Once the backend is verified, push Lane D's Day-1 work (flag default, FE-B2, FE-B3) to `mdf-frontend` `main`. Confirm the Vercel git deploy fires (it never has); otherwise run `vercel deploy --prod`.

### Days 3–7
- **Daily:** a manual `sync_prod_to_v2.py` run and a DLQ check. The cron can't run from a non-default branch (backlog).
- **G3** (Day 4) and **G4** (Day 6): see §6.

## 5. Lanes

Effort: S ≤ half a day, M ≈ 1 day, L > 1 day. **Must** = required for the exit criteria. **Should** = done this week if the Must list is done by Day 4.

**Cut line.** The exit criteria in §1 are the definition of done; the Must column is not. If a lane's Must list isn't done at G3, every Should in that lane moves to the backlog without discussion. Lanes C and D each hold about 4–5 lane-days of Must work in a 5-day build window, so Should items are genuinely optional. A lane may split into two sub-lanes only along disjoint files; `cli/main.py`, `app/search/page.tsx` and `app/detail/[source_id]/page.tsx` each have exactly one owner at a time.

### Lane B: backend (`connect_server/aws/v2`)

| Id | Item | Refs | Size | Day | Priority |
|---|---|---|---|---|---|
| B1 | Citation DOI: `doi or dataset_doi` in all four generators (`citation.py:116,146,183,198`); never emit `10.xxxxx/pending` for published records; pass `version` through | GAP-1 | S | 1 (ride-along) | Must |
| B2 | Clamp `offset+limit ≤ 10000`; fall back to the store scan only on transport or 5xx errors, never on a Globus 4xx; treat the scan cap as a ceiling, not a floor (`app/routers/search.py:171`, `search.py:310`) | SEC-H2 | S | 1 (ride-along) | Must |
| B3 | Resolve legacy ids on `/versions`, `/stats`, `/datasets/{id}/related`; return `canonical_source_id`; not-found becomes 404 `{detail}`, not 200 `{success:false}` (also `/status`) | GAP-2 | S | 1 (ride-along) | Must |
| B4 | `can_view_dataset`: owner or curator may read `pending_curation`/`rejected`/`approved` and restricted records; everyone else keeps 404 (`app/auth.py:354`). Cover card, citation, versions, preview. **Cross-family review** | GAP-3 | M | 2–3 | Must |
| B5 | Negative auth cache (token-hash, about 60 s) plus a per-IP 401 budget (`app/auth.py:276`). **Cross-family review** | SEC-M1 | S | 2–3 | Must |
| B6 | Approve pinned to the `metadata_updated_at` the curator saw (conditional write); schema, reserved-key and size checks on `metadata_updates`; record self-approval in `curation_history` | SEC-M4 | S | 3 | Must |
| B7 | Parse `dataset_mdata` before the `latest` filter (it is a JSON string on DynamoDB) in `search.py:634`, `embedding_snapshot.py:203`, `admin.py:362/539`, `async_jobs.py:1130/1279`; give the author index a TTL | SEC-M5 (partial) | S | 3 | Must |
| B8 | `?track=false` for SSR render and prefetch reads; count views only from the client beacon | GAP-4 | S | 3 | Must |
| B9 | Search response cleanup: drop empty facet buckets, sort Year by value, apply filters in the fallback scan (or 503), echo `limit` | GAP-8 (part) | S | 3 | Must |
| B10 | Hygiene: strip embedding fields from public `/status` (SEC-L3); fix the `diff` 500 on empty params and the existence oracle (SEC-L2); path-guard `/detail/{slug}`, `/status?source_id=`, and the `/status/update` body (SEC-L1); stop echoing exception text in 401s (SEC-L10) | SEC-L1/2/3/10 | S | 4 | Must |
| B11 | Alarms: API Gateway 5xx plus a handled-500 metric filter. **Main loop applies the template change** | SEC-L8 | S | 4 | Must |
| B12 | `v2.md`: record SEC-H3 (accepted for beta) and SEC-M3 (policy) | D3, D5 | S | 4 | Must |
| B13 | gzip middleware; `Cache-Control` on public GETs after B8 | GAP-9 | S | 4 | Should |
| B14 | "Newest" sorts by first publication, not `ingest_date` | GAP-7 (part) | S | 4 | Should |
| B15 | `/submissions?include_stats=true`; `/curation/pending` gains `is_update`, `previous_published_version`, and the publish job id from approve | GAP-10, GAP-11 | S | 5 | Should |

Lane B contract:
- Every item lands with tests.
- Run only the targeted `test_v2_*.py` files.
- Don't touch `template.yaml`; send those diffs to the main loop.
- The `source_id` grammar is frozen: validate any new validator against live ids.

### Lane C: CLI (`mdf_client`)

Design goal: **one obvious path per task, output that is honest on a terminal and clean when piped, and a core that never prompts.**

Target command tree (CLI-§4):
```
Get started   login · logout · whoami · init [PATH] · doctor
Find & reuse  search [Q] · show ID [--files] [--cite] · cite ID [-f] [-o] · clone ID [DIR] [--plan] · related ID · versions ID
Publish       check · publish [--submit] [--update [ID]] · status [ID] [--watch] · list [--all] · import DOI|zenodo:N
Manage        dataset edit|withdraw|resubmit|diff|open
More          admin … · config show|get|set|path · manifest discover|inspect · skill … · mcp …
```

| Id | Item | Refs | Size | Day | Priority |
|---|---|---|---|---|---|
| C1 | One `format_authors()` helper for object-shaped authors, used at `cli/main.py:2206`, `:2958` and `cli/backend.py:446`. Flatten `show --json` (no nested `card.card`) | CLI-U1 | S | 1 | Must |
| C2 | Core never prompts: `AuthRequired` from `BackendClient` (`core/backend_client.py:110`); the CLI prompts only on a TTY without `--json`; prompts go to **stderr** (`auth/globus.py:240,280`); exit 4 with the JSON error envelope. SDK default stays interactive so foundry keeps working. Remove core `print()`s (CLI-D1). **Cross-family review** | CLI-U2, D1 | M | 1–2 | Must |
| C3 | Test infrastructure first: recorded staging responses in `tests/fixtures/staging/`; a parametrized test that runs every `--json` command logged out with no stdin and asserts valid JSON and exit ∈ {0,2,3,4,5}; `--help` snapshot tests | CLI-D5 | M | 1–2 | Must |
| C4 | `publish --update [ID]` defaults to the ID in mdf.yaml, then the last publish; fetches the full previous metadata and merges the user's changes; sends a top-level `source_id` (not `extensions.mdf_source_id`); writes `source_id` back to mdf.yaml after the first publish. Fix README/SKILL.md | CLI-U4 | M | 2 | Must |
| C5 | `clone --plan` reads the card only (list files only with `--files`); spinner with file count; overall timeout; failed listings are errors, not warnings (`core/agent.py:562-660`); the message says when a login is required. Keep `_resolve_globus_files`'s signature (foundry imports it) | CLI-U3 | M | 2–3 | Must |
| C6 | Service handling: `setup`/`init` never persists `defaults.service`; `mcp install` omits `MDF_SERVICE` unless `--service` is given (`cli/main.py:715`, `cli/mcp_cmd.py:79`); `login`, `whoami` and `status --auth` print identity plus service; `login --token` persists when asked; single `logout` message | CLI-U10 | S | 2 | Must |
| C7a | Full IDs, never truncated, in the first column of `search`/`list`/`related`; no box drawing when stdout isn't a TTY; `cite` prints plain text when piped, adds `-o FILE`, rejects unknown formats | CLI-U5 (part), U6 | S | 3 | Must |
| C7b | Rest of the output pass: one-line titles, drop Type/Status from public search, `—` for missing values, version column (CLI-U5). Choice validation for `--type/--format/--sort/--by/--client`; importer 404 → not-found; every early exit through the JSON helper (CLI-U8). `list` shows latest version only and hides deleted, `--all` shows everything, relative dates (CLI-U9) | CLI-U5/8/9 | M | 4 | Should |
| C8 | Clean break (D4): delete the 19 aliases, `cli/backend.py`, `cli/stream.py`, `MDFAgent.stream_*` plus client stream methods, and `mdf.skill` (`handlers.py`, `mdf_tools.py`, `skill.md`; migrate the 3 tests that import it). `setup` + `manifest init` become `init`; `validate`/`--preflight-only` become `check`; add `whoami`. Add a test that every `mdf …` string in `src/`, README and skills resolves to a real command (CLI-U7). CHANGELOG breaking-changes list | CLI-D2/D4/U7 | M | 3–4 | Must |
| C9 | Shared options (`cli/options.py`) and a `with open_client(ctx, public=…)` context manager replacing 40–65 hand-copied option declarations and 55 manual `close()` calls; unify the error envelope in `mdf/core/errors.py`; the MCP server stops importing `mdf.cli.formatting` | CLI-D2/D3 | M | 4 | Should |
| C10a | `publish --submit` on staging prints "beta: test DOI (10.23677), does not resolve" | D2 | S | 3 | Must |
| C10b | The welcome screen orders Find → Publish → Agents, with login before publish; "MDF Agent" → "MDF CLI" everywhere; drop stream references; hide `--dev-user`; give `mcp` a labeled panel | CLI-U10/U11 | S | 4 | Should |
| C11 | Split `cli/main.py` (3,148 lines) into `cli/commands/{auth,publish,clone,search,show,list,status,import}.py`, done mechanically after C8/C9 | CLI-D2 | M | 5 | Should |
| C12 | Nightly read-only staging smoke in CI (`search`, `show`, `cite`, `versions`, `clone --plan`) | CLI-D5 | S | 5 | Should |

**Foundry compatibility contract (C2, C5, C8, C9):** these symbols keep their names and signatures:
- `mdf.core.agent.{MDFAgent, _resolve_globus_files, _extract_doi, _resolve_doi}`
- `mdf.core.backend_client.{BackendClient, ApiResult, _api_url_for_service}`
- `mdf.core.config`
- `mdf.auth.globus.{APP_NAME, DEFAULT_TOKEN_PATH, MDF_AGENT_CLIENT_ID, DATA_MDF_SCOPE, TRANSFER_SCOPE, GROUPS_SCOPE, NCSA_MDF_COLLECTION_UUID, get_authorizer_for_scopes}`
- `mdf.models.config.{Author, ManifestConfig}`

At G4, run foundry's tests against the release candidate (`pip install -e ../mdf_client` in foundry's env, single worker).

### Lane D: frontend (`mdf-next2`)

Design direction (FE-§1): **a trust pass, not a redesign.**
- Every identifier on screen (DOI, citation, CLI command, count) is correct and resolves.
- The dataset page reads like an instrument readout: DOI, license, size and access sit in the first viewport on every device.
- Anything not real yet gets hidden.
- DESIGN.md governs: no justified text, no pills except status badges, tokens not raw `slate-*`/`amber-*`.

| Id | Item | Refs | Size | Day | Priority |
|---|---|---|---|---|---|
| D1 | v2 by default: `lib/api/feature-flags.ts:7` fails the build when `NEXT_PUBLIC_USE_V2_API` is unset in production (or treats unset as v2). Confirm the Vercel env | FE-§3 | S | 1 | Must |
| D2 | `/cli` uses bare `mdf login` with no staging URL or label (`app/cli/_components/cli-quickstart.tsx:15`, `cli-tasks.tsx:28`, `app/cli/_lib/session-script.ts:40,123,137,166,202`) | FE-B2 | S | 1 | Must |
| D3 | Citation safety net: append `https://doi.org/<card.doi>` when a citation lacks it; hide the DataCite tab when it contains `pending` | FE-B3 | S | 1 | Must |
| D4 | Beta chrome: site-wide "Public beta" ribbon linking to a short `/beta` page (what works, test DOIs on new publishes, real DOIs on existing datasets resolve to the current MDF site, how to give feedback); a test-DOI notice on `/publish` and the success screen; `noindex` via `robots.txt` and metadata on non-production hosts | D2, D9, FE-S12 | S | 2 | Must |
| D5 | Detail routing: `/detail/<id>-<ver>` and `…_v<ver>` retry the base id and `redirect()` to `?version=` (`app/detail/[source_id]/layout.tsx:94`); redirect on `redirected_from`; canonical id for secondary calls; guard `versions?.versions` (`page.tsx:1481`) | FE-B1, GAP-2 | S | 2 | Must |
| D6 | Owner view: the layout stops turning an anonymous 404 into `notFound()` when the browser holds a token. Render client-side with `robots: noindex` and show OwnerTools on pending/rejected records. Pair with B4 | GAP-3 | M | 3 | Must |
| D7 | Featured cards link internally and show the DOI as text (`app/components/home-highlights.tsx:26`); one source for counts (live `/search` `total`, `SITE_STATS` fallback) on the homepage, the 404 page (`app/not-found.tsx:48`) and PRODUCT.md; check the TB figure | FE-B4, FE-B5 | S | 2 | Must |
| D8 | Mobile: a "Filters (n)" sheet below `lg` with the promo after the results (FE-B6); a DOI · License · Size · Access strip under the title (FE-S1) | FE-B6, FE-S1 | M | 3 | Must |
| D9 | Search honesty pass: hide "Most viewed" (FE-S3), "Recently added" (FE-S4), drop blank facets and sort years (FE-S5/6, also B9), delete the "MDF v2 API" label (FE-S7), authors on result cards and humanized keywords (FE-S8), a request sequence or AbortController with single-effect URL hydration (FE-S9, `app/search/page.tsx:159`); use `?track=false` on the hover prefetch (B8) | FE-S3…S9 | M | 3 | Must |
| D10 | Auth flow: `returnTo` survives sign-in (`app/auth/page.tsx:100`); `/auth` without a `code` shows a way back; one `<SignInGate>` replaces the four copies, and on `/publish` it lists what's needed before signing in; the publish success screen says what happens next and links to `/my-datasets` | FE-S10, S14, S15 | M | 4 | Should |
| D11 | Detail polish: sans-serif, left-aligned abstract (`page.tsx:1087`); file preview timeout that collapses to one line with no profile (`file-browser.tsx:137`); always show a License row; `<main>` landmark | FE-S2, S11 | S | 4 | Should |
| D12 | Accessibility: `--input` border ≥ 3:1, `aria-current` on nav, skip link, fieldset/legend for "Data location" | FE-S13 | S | 4 | Should |
| D13 | Server-rendered `schema.org/Dataset` JSON-LD in `detail/[source_id]/layout.tsx`; `metadataBase`, canonical and `og:image` | FE-S12 | S | 5 | Should |
| D14 | Curation: offset paging, queue keyed by (source_id, version), correct approve toast; `/api/submissions` proxy uses `start_key` | GAP-11, GAP §2 | S | 5 | Should |
| D15 | Drop `@radix-ui/themes/styles.css` (`app/layout.tsx:5`) and unused dependencies (recharts, easymde, react-simplemde-editor, cmdk, lodash, react-copy-to-clipboard), with a visual re-check via `capture` | FE-§3 | S | 5 | Should |
| D16 | Nice-to-haves: tokens instead of `rounded-full`/`slate-*`, OMol25 spotlight image and citation, ISO dates | FE nice-to-have | S each | 5 | Should |

Lane D contract:
- Vitest for logic changes.
- Screenshots of every changed route at 1440 and 390 via the `capture` skill (one headless browser at a time) for the G3/G4 review.
- No `npm run build` in the lane; the main loop runs it.
- Preview deploys don't work (env vars are production-only), so verification happens on the local dev server against staging.

## 6. Gates and release

- **G3 (Day 4).**
  - Merge B4–B12. Cross-family review of B4, B5 and C2. The main loop applies B11 to the template, then runs `sam validate --lint` and the full backend suite, and deploys (#3).
  - Re-run the probe sets.
  - Merge Lane D Must items so far, run vitest and `npm run build`, then push `main` and verify on `staging.materialsdatafacility.org`.
  - Run the CLI suite.
- **G4 (Day 6), release candidates.**
  - **Backend:** final deploy with Should items.
  - **CLI:**
    - Date the CHANGELOG 0.3.0 entry and fast-forward `main` to `cli-styling`.
    - Run the full suite and foundry's suite.
    - PyPI versions are immutable, so verify the wheel first: `python -m build && pip install dist/*.whl` in a fresh venv, then `mdf --version && mdf show <real id>`.
    - Create GitHub release `v0.3.0` (PyPI trusted publishing).
    - Verify with `pipx install mdf-cli==0.3.0 && mdf --version && mdf show <real id>`.
  - **Frontend:** final push; screenshot pass of the key routes on the live staging domain.
  - **Full regression:** all three suites (sequential), the second E2E publish through the web form, and the probe sets.
- **Day 7:**
  - Soak (DLQ, alarms, Vercel logs).
  - Update Moment #204 handoff, the burndown status block, and this plan's checkboxes.
  - The user approves the announcement copy, then announces.

## 7. User track (you)

| When | What | Time |
|---|---|---|
| Day 2 AM | `aws login` root session (briefing §4); approve pushes of the three branches and the staging deploys | 5–10 min |
| Day 2 | Be reachable if the CLI's stored Globus tokens have expired, for the E2E publish and link-health run | — |
| Day 6 | **Web E2E publish.** The form calls Globus Transfer from the browser, so it needs your signed-in Globus session. Run it yourself, or the main loop drives it through claude-in-chrome while you're logged in | ~10 min |
| Day 6–7 | **Prod prerequisites** (unblock cutover, not the beta):<br>• DataCite prod creds into `/mdf/prod/datacite-*`.<br>• Rotate *both* test credentials found in public git history (SEC-M7, B-5).<br>• SES production access.<br>• A **separate Globus confidential client per environment**, plus the prod ingest role on `fed3e94e-…` (SEC-M6).<br>• `/mdf/prod/openai-api-key`.<br>• ACM cert plus `api.materialsdatafacility.org`: DNS answers today at 18.210.250.94 (probably v1), so check what serves it before repointing (O-5).<br>• Decommission the foreign-account gateway `hjccjf3eqg` (O-6). | ~1 hr |
| Day 7 | Approve beta announcement copy and the feedback channel | 10 min |

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Facets empty between deploy and re-index | Back up first; deploy and re-index in one sitting; A2 dry-run on Day 1 |
| `deploy.sh` auto-confirms changesets without a TTY | `--no-execute-changeset` review, or the user runs the command |
| First `test-v2.yml` run on the branch fails for CI-only reasons | Budget 1 h on Day 2; fix forward on the branch |
| Vercel git auto-deploy never fired | `vercel ls` after push; fallback `vercel deploy --prod` from the linked checkout |
| CLI refactor breaks foundry | Compatibility contract in Lane C, plus foundry suite at G4 |
| Root AWS session expires mid-run | Do Day 2 data ops in one sitting; the scripts are idempotent and dry-run first |
| Staging lags v1 during the beta | Daily manual sync; the beta page says new v1 datasets appear within a day |
| Accepted SEC-H3 risk | Small curator set; fix before cutover; recorded in `v2.md` |
| Laptop memory | Budget block in every lane prompt; the main loop serializes suites and builds |
| Pushing to the wrong remote or branch (v1 redeploy, public www) | Briefing §8 hazards; the main loop does every push |
| Foreign gateway `hjccjf3eqg` shares the staging index and runs old code. A publish through it would write nested-shape GMeta into the freshly flattened index | Low likelihood; one more reason O-6 is in the user track. Re-run `reconcile_migration.py --check-search` before the announcement |

## 9. Out of scope for this week (backlog, with ids)

- **Cutover:** O-1 sequence, C-1 CLI default flip to prod, DNS and 308 redirects, v1 freeze. Also SEC-H3 token audience, which must land before cutover.
- **Sync automation:** the cron can't fire from a non-default branch, and `connect_server`'s default branch `prod` is v1 (any workflow change there redeploys v1). Needs a default-branch decision or another scheduler.
- **Backend features:**
  - GAP-5 (download counts)
  - GAP-6 / C-7 (profile and file-list backfill, which restores sizes and the file browser)
  - GAP-12 (sitemap feed)
  - GAP-13 (public summary stats)
  - GAP-8 facet-prefix/autocomplete endpoint
  - F-11 semantic search UI
  - author pages, curated collections, `POST /citation/batch`, submission timeline, backend uploads
- **Security fast-follows:**
  - SEC-M2 (WAF or shared rate limiter)
  - SEC-M5 remainder (invalidation on delete/ACL change)
  - SEC-M6 runtime secret reads from SSM or Secrets Manager (per-env clients are in the user track)
  - SEC-L4, L5, L6, L7, L9, L11 (lockfile plus `pip-audit`), L12, L13, L14
- **Frontend:** delete the v1 code paths after cutover; split `app/publish/page.tsx` (2,082 lines) and `app/detail/[source_id]/page.tsx` (1,531); dynamic sitemap (it matters once indexing is on).
- **Hygiene:** `mdf_client/context/` holds a committed grant report (`RPPR Supplemental…`); remove it before the repo ever goes public. Purge the DataCite test credentials from `connect_server` history after rotation.
