<!-- Saved 2026-09-25 from a read-only audit lane (frontend↔backend contract). Numbered P0/P1 items are cited by plans/2026-09-25-beta-launch-plan.md as GAP-<n>. -->

# MDF v2 frontend↔backend contract audit (read-only)

**Scope:** frontend `mdf-next2` @ `release-fixes-2026-09-04`, backend `connect_server/aws/v2` @
`release-burndown-wave1` (3 commits ahead of origin: 7b9f914, 6af624c, 29cd0bb), and 36 GET requests against live
staging. Probe outputs were in the session scratchpad and were not kept.

**Probe side effect:** `GET /card` and `GET /detail` write to the database: they increment `view_count`
(`cards.py:126,223`). Three probe calls added 3 views to `foundry_wei_atom_locating_benchmark` on staging. Nothing
else was written.

## Top findings

1. **Citations drop the DOI on most migrated datasets (confirmed live).** The card falls back to `dataset_doi`, but
   `citation.py:116,146,183,198` read only `record["doi"]`. Records created by the migration (the v1.1 versions)
   carry only `dataset_doi`. BibTeX, RIS and APA therefore have no DOI, and the DataCite XML says `10.xxxxx/pending`.
   The local commits don't fix this, and `backfill_record_shape.py` never touches `doi`.
2. **Legacy ids crash the detail page.** Checked live: `/citation/foundry_wei_atom_locating_benchmark_v1.1`
   resolves (with `redirected_from`), but `/versions/<same id>` returns **200** `{"success":false,...}`. The client
   only throws on non-2xx responses, and `page.tsx:1481` then reads `versions.versions.length` on undefined, which
   throws a TypeError during render. Secondary calls use the id from the URL, not the canonical `card.source_id`
   (`page.tsx:206-207,245-246`).
3. **Owners can't open their own pending, rejected or restricted datasets.** `can_view_dataset` rejects any status
   other than `published` before it checks ownership (`auth.py:354`). That contradicts `cards.py:49` ("owners get
   their in-flight version"), the /my-datasets links to `/detail/{id}?version=…`, and the frontend tests
   (`my-datasets-flow.test.tsx:175,179`).
   - The server layout (`layout.tsx`) calls `/card` without a token, gets a 404, and returns a hard `notFound()`.
     It assumes restricted datasets answer 401/403 (`layout.test.tsx:78-79`), but the backend deliberately answers
     404.
   - As a result, `OwnerTools` withdraw/resubmit (`owner-tools.tsx:31-34`) can never render.
4. **Deploying the 3 local commits breaks search filters until the index is rebuilt.** The filter map is flattened
   (`dc.year` becomes `publication_year`, and so on). Per `search_client.py:11-29`, filters and all five facets
   return nothing until the 935-entry index is wiped and re-ingested.
5. **Several stats are fake or inflated:**
   - "Most viewed" returns the same list as "newest" (`search.py:65-72` is a placeholder).
   - `score` is 0 on every search result.
   - `download_count` never increments; only the unmounted files router updates it.
   - One detail view counts 2–3 times: server layout `getCard`, client `getCard`, and hover prefetch `getCard`.

## 1. Path gap (local 46 paths / 47 operations vs live 34)

- **(a) Deliberately unmounted (10):** the streams router (`/stream/create`, `/stream/{id}`,
  `/stream/{id}/append`, `/stream/{id}/close`, `/stream/{id}/snapshot`) and the files router
  (`/stream/{id}/upload`, `/upload-url`, `/upload-confirm`, `/download-url`, `/files`). Both are commented out in
  `app/__init__.py:67-73`.
- **(b) Only in undeployed commit 29cd0bb (2):** `POST /admin/link-health/run` and
  `GET /admin/link-health/summary`.
- **(c) Contract changes in the local commits without new paths:**
  - `/card?format=agent` is ignored on staging.
  - The card gains `link_health`, `root_version` and `previous_version`.
  - Search filter fields are flattened (re-ingest required).
  - A mismatched `X-Groups-Token` now gets a 401.
  - Rate limiting adds a per-IP bucket.
  - All routers get a path guard that turns a malformed `source_id` into a 404.
  - The curation index changes (needs two deploys).

## 2. Contract table

| Frontend call (file) | Endpoint | Mismatch / risk |
|---|---|---|
| search page `v2Client.search` (`search/page.tsx:283`) | `GET /search` | Types say `version` and `root_version` are numbers (`lib/types/v2.ts:10,25`); the API sends strings. The Organization facet has a `""` bucket (100 datasets) rendered as a blank checkbox (`SearchFilters.tsx:88-106`). Domains facet always empty. Facets capped at top 20. `most_viewed` = `newest`. `score` = 0. `limit` not echoed. |
| search hover prefetch (`search/page.tsx:400-414`) | `/card` + `/citation` per hovered result | Every hover increments `view_count` |
| detail layout, server side, no token (`layout.tsx:41-49,95`) | `GET /card` | Assumes restricted = 401/403; backend returns 404, so `notFound()`. Also increments `view_count`. |
| detail page `getCard` (`page.tsx:226`) | `GET /card?version=` | Discards `canonical_source_id`/`redirected_from` (no canonical redirect). `links` typed as an array but sent as an object. `stats` shows 0 files / `"0 B"` for migrated records. |
| `getCitation` (`page.tsx:243`) | `GET /citation?format=all` | DOI missing (finding 1). Never passes `version`, so `?version=1.0` shows the latest version's citation. |
| `getStats` (`page.tsx:245`) | `GET /stats/{id}` | `download_count` always 0; legacy id → 404 (handled) |
| `getVersions` (`page.tsx:246`) | `GET /versions/{id}` | Unknown/legacy id → 200 `{success:false}` → page crash (finding 2) |
| `getRelated(by=author)` (`page.tsx:191`) | `GET /datasets/{id}/related` | 2.6 s cold. `by=similar` returns `available:false` (no embedding snapshot on staging). |
| file browser (`file-browser.tsx:137-174`) | `/preview/{id}/files`, `/sample`, `/files/{path}` | 404 "No profile found" on 3 of 3 records. No `X-Groups-Token` sent. |
| version timeline | `/versions/{id}/diff` | OK |
| detail edit `updateMetadata` | `POST /submissions/{id}/metadata` | OK |
| OwnerTools, my-datasets withdraw/resubmit | `POST /submissions/{id}/withdraw\|resubmit` | Bodies OK, but the detail page can't load pending/rejected records (finding 3) |
| my-datasets `getSubmissions` | `GET /submissions?start_key=` | OK, but one `/stats` call per published row (`my-datasets/page.tsx:224-259`) |
| publish page → `/api/submissions` proxy (`api/submissions/route.ts:61`) | `GET /submissions` | Proxy forwards `next_key`; the backend reads `start_key`. Page fetches only 50 rows and dedupes itself (`publish/page.tsx:1003-1024`). |
| `/api/submit` proxy | `POST /submit` | Errors re-wrapped as `{error, details:<raw string>}` |
| `/api/status/[id]?v2=true` proxy | `GET /status/{id}` | Not-found comes back as 200 `{success:false}` and passes through as success |
| `useCurator` → `authCheck` | `GET /auth/check` | OK. Publish also queries Globus Groups directly (`publish/page.tsx:499`). |
| curation `getCurationPending` (`v2-client.ts:322-332`) | `GET /curation/pending` | Client sends `next_key`; backend pages by `offset`. UI never pages past 50. Queue keyed by `source_id` only. |
| curation approve | `POST /curation/{id}/approve` | In queued mode the response is `approved` with no DOI, yet the UI toasts "Approved and published." |
| admin page | `/admin/stats`, `GET`/`POST /admin/sync` | OK. No link-health UI. |
| not called by the frontend | `/search/semantic`, `/embed`, `/detail/{slug}`, `/preview/{id}`, `/status?source_id=`, `/status/update`, `/submissions/{id}/delete`, `/admin/embeddings/*`, `?format=agent` | |

Error formats differ by endpoint:

| Response | Endpoints |
|---|---|
| 4xx `{detail}` | `/card`, `/stats`, `/preview`, auth failures |
| 200 `{success:false, error}` | `/versions`, `/status` |
| 200 `{available:false, reason}` | related `by=similar`, `/search/semantic` |
| 500 `{detail, request_id}` | unhandled errors |

## 3. Places the frontend degrades, stubs or falls back

- **The whole v1 path** (flag off): search and detail query the legacy index `1a57bbe5…` directly
  (`search/page.tsx:56-69,308-368`, `detail/[source_id]/page.tsx:295-330`), and citations are built in the
  browser. The `api/{submit,submissions,status}` routes post to `publish-prod.materialsdatafacility.org`.
  `providers.tsx:16` keeps requesting the Search scope for v1.
- File size and count rows are removed ("file stats are unpopulated", `page.tsx:823-824`). The file browser shows
  "File listing not yet available" (`file-browser.tsx:210`).
- Downloads are hidden when 0, which is always (`page.tsx:1451-1472`).
- Homepage numbers are hand-maintained (`935`, `1,000 TB+`, `60+`, `app/lib/site-stats.ts:14-19`). Featured
  datasets are hardcoded and link off-site to doi.org (`home-highlights.tsx:19-60`).
- The page's own comment admits "most viewed" falls back to recency (`search/page.tsx:591-598`).
- **Request fan-out:** hover prefetch sends 2 requests per hovered result, and a detail view costs about 8
  requests. my-datasets makes N `/stats` calls; publish dedupes client-side; curation stops at 50.
- **SEO:** `public/sitemap.xml` is static with no `/detail` URLs, and JSON-LD exists only on the homepage
  (`app/page.tsx:35-60`).
- **Backend fallback:** when Globus Search fails, the DB scan drops filters silently (`search.py:308-341`).
- **Search ignores auth for datasets:** `search_all` passes auth only to the stream search, so owners never see
  their restricted datasets in search.
- `/cli` marketing transcript shows the staging URL (`app/cli/_lib/session-script.ts:40`).

## 4. Missing backend features, ranked

### P0
1. **GAP-1 Citation DOI fallback.** Use `doi or dataset_doi` in all four generators; never emit
   `10.xxxxx/pending` for published records. S, backend only. `citation.py:116`
2. **GAP-2 Legacy/unknown ids consistent on `/versions`, `/stats`, `/datasets/{id}/related`.** Resolve through
   `get_by_legacy_source_id` like `/card`. Return `canonical_source_id`. Answer 404 `{detail}` instead of 200
   `{success:false}`.
   - S backend.
   - S frontend: guard `versions?.versions`, use the canonical id for secondary calls, redirect on
     `redirected_from`. `app/detail/[source_id]/page.tsx:1481`
3. **GAP-3 Owner and curator access to non-published and restricted records.** `can_view_dataset` (or `/card`)
   serves `pending_curation`/`rejected`/`approved` to the owner or a curator, and keeps 404 for everyone else.
   The frontend layout must stop turning an anonymous 404 into `notFound()`. Render client-side with
   `robots: noindex`. M, backend + frontend. `app/auth.py:354`, `app/detail/[source_id]/layout.tsx:95`

### P1
4. **GAP-4 View counting that doesn't inflate.** Add `?track=false` for the SSR render and prefetch, or a
   `POST /datasets/{id}/view` beacon. This is a prerequisite for caching. S.
5. **GAP-5 Real download counts.** `GET /datasets/{id}/download` increments the count and returns a 302 to
   `download_url`. `mdf clone` should call it too. M.
6. **GAP-6 Profile and file-list backfill for migrated records (C-7).** An async job lists files and fills
   `file_count`/`total_bytes` plus a basic profile. It restores the size/files rows, the file browser and search
   sizes. M–L.
7. **GAP-7 Honest sorting.** Copy `view_count` into the index for a real "most viewed". "Newest" should sort by
   first publication, not `ingest_date` (today it means "recently migrated"). S–M.
8. **GAP-8 Search response cleanup.**
   - Drop or label empty facet buckets.
   - Sort Year by value.
   - Populate or remove Domains.
   - Return a real `score` and echo `limit`.
   - Apply filters in the fallback path, or return 503.
   - Add `GET /search/facets/{name}?prefix=` (also covers autocomplete).

   S–M.
9. **GAP-9 Performance.**
   - gzip middleware: the HTTP API doesn't compress, and responses are 19–60 KB.
   - `Cache-Control`/ETag on public GETs (after GAP-4).
   - Optional combined `GET /datasets/{id}/page`.
   - Consider one warm instance: search takes 5.2 s cold vs 0.4 s warm.

   S–M.
10. **GAP-10 `/submissions?include_stats=true`** removes the my-datasets N+1. S.
11. **GAP-11 Curation detail.**
    - Backend (S): `is_update` and `previous_published_version`, an inline diff against the published version,
      and the publish job id returned from approve.
    - Frontend: offset paging, key the queue by (source_id, version), fix the approve toast.
12. **GAP-12 Sitemap feed.** Public paged `GET /datasets/index` → `{source_id, version, updated_at}`. Optional
    `/card?format=schemaorg`. S.
13. **GAP-13 Public summary stats.** Cached `GET /stats/summary`. The dataset count alone is `total` from
    `/search?limit=1`. S.

### P2
- Semantic search UI (F-11): needs a snapshot and auth; consider `mode=hybrid` on `/search`. M.
- Author/ORCID pages (`GET /authors?prefix=`, `GET /authors/{key}`). M.
- Curator-managed `GET /featured` / `GET /collections`. M.
- Link-health badge and admin panel after deploying 29cd0bb. S.
- `POST /citation/batch` (BibTeX/RIS). S.
- `GET /submissions/{id}/timeline` from `curation_history`. S.
- Uploads through the backend (mount the files router) instead of browser → Globus Transfer
  (`publish-dataselector.tsx:217`). L.
- Search that respects the signed-in user's access to restricted datasets. M.

## 5. The v1/v2 flag

`isV2Enabled()` checks `NEXT_PUBLIC_USE_V2_API === 'true'` (baked in at build time; default false). The v1 path
queries the legacy index directly and posts to `publish-prod`. The two paths are not interchangeable (facet
field names and URL filter encoding differ). **Ship v2.**

Launch order:
1. Deploy backend (GSI in two deploys).
2. Wipe and re-ingest search.
3. Backfills.
4. Embedding snapshot.
5. Verify: facets non-empty, legacy-id detail renders, citations carry DOIs.
6. Build the frontend with v2 on.

## Latency (staging, anonymous)

| Endpoint | Time |
|---|---|
| `/health` | 0.18 s |
| `/search` browse | 5.16 s cold / 0.42 s warm |
| `/search?q=band` | 2.72 s first, then 0.3–0.6 s |
| `/card`, `/detail`, `/citation`, `/versions`, `/stats` | 0.34–0.51 s |
| related `by=author` | 2.61 s cold / 0.16 s warm |
