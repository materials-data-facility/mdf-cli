<!-- Saved 2026-09-25 from a read-only audit lane. IDs (H-*, M-*, L-*) are cited by plans/2026-09-25-beta-launch-plan.md as SEC-<id>. Credential fragments redacted. PRIVATE: keep out of the public connect_server repo. -->

# MDF v2 backend pre-launch security audit (read-only)

I read the code, ran 26 unauthenticated GET/OPTIONS probes against staging, and did a bounded search of git history. The biggest finding: **deployed staging does not have the prior hardening.** All three undeployed commits (7b9f914, 6af624c, 29cd0bb) are ahead of what staging runs. That includes the Groups-token binding, the rate-limiter fix, the path guard, the visibility fixes, the source_id grammar and DOI guard, and the SSRF guard. Two pieces of evidence: the live `openapi.json` shows `GET /stats/{source_id}` with no auth parameters and `/card` with no `format` parameter, and `git show origin/release-burndown-wave1` confirms the older code. Every "verified" item below therefore says whether it holds in HEAD and whether it is live.

## 1. Route and auth table (local HEAD)

Mounted routers: submissions, search, cards, curation, preview, admin, plus app-level `/health` and `/auth/check`. The streams and files routers are not mounted.

| Route | Auth | Ownership / role check | Visibility filter | Gaps |
|---|---|---|---|---|
| GET /health, /docs, /redoc, /openapi.json | none | – | – | docs are public (informational) |
| GET /auth/check | required | – | – | – |
| POST /submit | required + submitter group | update=true needs root owner or curator (B-3) | – | – |
| GET /submissions | required | own records; `organization=` needs curator | – | `limit` has no upper bound (DynamoDB caps the page) |
| POST /submissions/{id}/metadata | required | owner of that version, or curator | status gate | edits to non-location fields auto-publish (M-3) |
| POST …/withdraw, …/resubmit | required | owner or curator | pending_curation / rejected only | – |
| POST …/delete | curator | – | – | – |
| POST /status/update | required + curator check in handler | – | – | bypasses the state machine and search reconciliation (L-4); body id not path-guarded |
| GET /status/{id} | optional | owner or curator gets the raw record | `can_view_dataset`, sanitized view | embedding fields not stripped (L-3) |
| GET /status?source_id= | optional | same | same | no path validation (L-1) |
| GET /versions/{id} | optional | curator, or owns any version | `can_view_dataset` | – |
| GET /versions/{id}/diff | optional | – | `can_view_dataset` on both versions | existence oracle; empty version returns 500 (L-2) |
| GET /stats/{id} | optional | – | `can_view_dataset` | **staging: no auth, published-only filter** |
| GET /search | optional | – | Globus index queried anonymously (public entries only); fallback scan is public + published | unbounded offset forces a full table scan (H-2) |
| GET /search/semantic, POST /embed | required (any Globus user) | – | snapshot is public-only (HEAD) | – |
| GET /datasets/{id}/related | none | – | author index and snapshot are public-only in HEAD; **staging includes restricted datasets** | stale caches (M-5) |
| GET /card/{id}, /citation/{id} | optional | – | `can_view_dataset` | – |
| GET /detail/{slug} | optional | – | `can_view_dataset` | path param is named `slug`, so the guard never runs (L-1) |
| GET /preview/{id}[/files[/{path}]\|/sample] | optional | – | `can_view_dataset` | `{path}` is only matched against the stored profile; nothing is read from the filesystem |
| /curation/pending, /curation/{id}, approve, reject | curator | – | – | self-approval allowed; unpinned approve (M-4) |
| /admin/sync, /admin/stats, /admin/embeddings/*, /admin/link-health/* | curator | – | – | link-health routes exist in HEAD only |

## 2. Findings

### Critical
None found in HEAD code.

### High

**H-1. Staging runs code from before the hardening (staging only).**
- **Evidence:** `openapi.json` (probe 1) and `git diff origin/release-burndown-wave1..HEAD`.
- **What an attacker can do on staging today:**
  - Pair their own bearer token with a leaked curator `X-Groups-Token` and get curator rights. The deployed `auth.py` has no identity binding and caches by bearer token only.
  - Bypass the rate limiter by rotating `X-User-Id`. The deployed `_actor_key` trusts that header in production mode.
  - Read restricted-but-published datasets anonymously. `/datasets/{id}/related?by=author` and the embedding snapshot (`build_author_index` / `build_snapshot`) filter on `status=="published"` only. `/stats` has no auth. `/status`, `/versions` and `/diff` also treat "published" as viewable.
  - Staging also lacks the DOI collision guard, the source_id grammar and the path guard.
- **Fix:** deploy HEAD, then re-fetch `openapi.json` and confirm `/stats` lists auth headers and `/card` has `format`. **Effort: S.**

**H-2. Anonymous request forces a full DynamoDB scan (both).**
- **Location:** `ag aws/v2/app/routers/search.py:171`, `ag aws/v2/search.py:310`. `offset` is clamped to ≥0 but has no upper bound.
- **Mechanism:** When `offset + limit > 10000`, Globus Search rejects the query. The rejection is treated like an outage, so the code falls through to `store.list_all(limit=max(limit+offset, 1000))`. That loops `Scan` over the whole table and scores every item.
- **Live proof (probe 5):** `GET /search?q=band&offset=9990` took 2.53 s and returned `total:41, facets:{}` (the fallback). The Globus path takes 0.47 s and returns `total:25` with facets.
- **Impact:** Each call reads every item, including embeddings and profiles, on a pay-per-request table. At 50 reserved concurrency, a single client can pin every Lambda slot (denial of service) and run up the DynamoDB read bill.
- **Fix:** clamp `offset + limit` to ≤ 10000. Fall back only on transport or 5xx errors, never on a Globus 4xx. Cap the fallback scan at `SEARCH_MAX_DATASET_SCAN` instead of using it as a floor. **Effort: S.**

**H-3. The token's intended service is never checked (both).**
- **Location:** `ag aws/v2/app/auth.py:171`. The code accepts any Globus token that `userinfo` accepts, and says so in its own comment.
- **Exploit:** Any third-party Globus app the user has logged into with the `openid` scope holds a token that works as that user against MDF. If that app also got the Groups scope, it can act as a curator: approve, delete, mint DOIs. The Groups binding does not help, because both tokens belong to the same identity.
- **Fix:** introspect tokens with the MDF resource-server client (4d5f8e8b) and require the MDF `connect` scope. This is the same credential change that would make the dependent-token path work. **Effort: M.** If you choose to accept the risk instead, record that decision explicitly.

### Medium

**M-1. No negative auth cache (both).** `ag aws/v2/app/auth.py:276` caches only successful logins. Every request carrying a garbage bearer token on an optional-auth route costs one Globus `userinfo` call. The rate-limit budget is 600/min per IP, per first path segment, per container. A token spray can push MDF's egress into Globus Auth rate limits and break sign-in for real users. **Fix:** cache failures by token hash for about 60 s, and add a per-IP limit on 401 responses. **Effort: S.**

**M-2. The rate limiter is advisory on Lambda (both).** `ag aws/v2/app/middleware.py:16`. State lives in process memory, so the real ceiling is limit × up to 50 containers, and it resets on cold start. The bucket key is IP plus the first path segment, so random first segments get fresh buckets. The dict of buckets never evicts entries. The only hard bound is the gateway's 100 rps / 200 burst. **Fix:** a WAF rate rule, or DynamoDB/ElastiCache counters. **Effort: M.**

**M-3. Owner edits after approval bypass curation (both; policy decision).** `ag aws/v2/app/routers/submissions.py:587`. Only `data_sources`, `download_url` and `external` require re-curation. Edits to title, description, related_works, license, publisher, extensions and ACL publish immediately, with `approved_by` set to the owner (:634). They also push DataCite metadata updates under the MDF prefix. **Fix:** either confirm this is the intended policy, or route text and link fields through curation. **Effort: M.**

**M-4. Approval is not pinned to what the curator reviewed (both).** `ag aws/v2/app/routers/curation.py:116`.
- Approve reads the record at click time. The owner can edit a pending record in place (including `data_sources`) between the curator's review and the approve click, and the unreviewed version gets published.
- Curators can approve their own submissions.
- `metadata_updates` is deep-merged with no schema check, no reserved-key check, and no size cap.
- **Fix:** have approve send the `metadata_updated_at` the curator saw, and make the write conditional on it. Block self-approval, or record it explicitly. **Effort: S.**

**M-5. Derived indexes go stale and keep serving hidden datasets (HEAD).**
- **Location:** `ag aws/v2/search.py:668`, `ag aws/v2/async_jobs.py:1090`.
- The author index in the API Lambda is never invalidated. The only invalidation call runs in the worker Lambda, which is a different process.
- The embedding snapshot is rebuilt only when an admin triggers it.
- The `latest is False` filters (search.py:634, embedding_snapshot.py:203, admin.py:362/539, async_jobs.py:1130/1279) never fire on DynamoDB, because `dataset_mdata` is stored as a JSON string there. Superseded versions are therefore indexed too.
- **Result:** datasets that were deleted, or restricted in a later version, keep appearing in `/related` and semantic results (title, authors, DOI) until the container recycles or someone rebuilds.
- **Fix:** parse `dataset_mdata` before the latest filter; rebuild or invalidate on delete, publish and ACL change; give the author cache a TTL. **Effort: M.**

**M-6. One Globus client for every environment, and secrets in plain Lambda env vars (both).**
- **Location:** `ag aws/deploy.sh:338`. `/mdf/globus-client-secret` is not scoped by environment, so dev, staging and prod all share one confidential client.
- Staging runs older code and its index is shared with a gateway in a foreign AWS account (O-6), yet it holds a credential that can write the prod search index.
- The Globus secret, DataCite password, OpenAI key and GitHub token are passed as CLI `--parameter-overrides` and stored as plaintext environment variables (template.yaml:256/262/289/295). Anyone with `lambda:GetFunctionConfiguration` can read them.
- **Fix:** one Globus client per environment, and read secrets from SSM SecureString or Secrets Manager at runtime. **Effort: M.**

**M-7. B-5 is wider than the plan records.** The history holds two different DataCite TEST credentials:
- username `globus` with an 8-character password (redacted), added in 542907c on 2026-02-06 and removed in 906d5be;
- `Globus.TEST` / (redacted), in 906d5be, a6ae919 and 648dd9b, and copied into an audit report file in cf2e959.

Both are reachable from `origin/release-burndown-wave1` and `origin/v2-backend-curation`. HEAD's working tree is clean. **Fix:** rotate both credentials. Confirm whether `globus` is a provider- or consortium-level account (higher blast radius). Then purge history. **Effort: S to rotate, M to purge.**

### Low

- **L-1. Path-guard gaps (HEAD).** `/detail/{slug}` (probe 16 confirms the handler runs on `..\..\x`), `GET /status?source_id=`, and the `POST /status/update` body are not guarded. They are harmless against DynamoDB keys, but "path guard on all routers" is not literally true.
- **L-2. /versions/{id}/diff (both).**
  - It returns "Version X not found" for a missing version but "Submission not found" for one you can't see, which reveals which pending or restricted versions exist.
  - `from=&to=` returns 500 (probe 24; the body is generic).
- **L-3. Public `/status` view keeps embedding fields (HEAD).** `_SENSITIVE_RECORD_FIELDS` (`submissions.py:412`) does not include `title_description_embedding`, `embedding_model` or `embedding_generated_at`. Once embeddings exist, every public status call ships a 1536-float vector.
- **L-4. `POST /status/update` (curator only).** It can move a published record to rejected or pending with no search reconciliation (the index entry is orphaned) and no `curation_history` entry.
- **L-5. Leftover SSRF risks in link-health (HEAD only).**
  - The address check and the actual request resolve DNS separately, so DNS rebinding is possible.
  - The check uses `is_private` etc. rather than `not ip.is_global`, which misses ranges like 100.64/10.
  - A DNS failure is admitted.
  - Impact is small: https only, only the status code is kept, results are curator-visible, and the Lambda is not in a VPC.
- **L-6. Wasted work on anonymous requests (both).**
  - Every anonymous `/search?q=` scans up to 2000 Streams items, even though anonymous callers can never see streams (`search.py:747`).
  - `/related?by=similar` does an S3 pointer GET on every call (2.1 s live).
- **L-7. No URL scheme validation (both).** `download_url`, `license.url` and `external.url` accept `javascript:` URLs. React 19 blocks these in the frontend, but DataCite `rightsUri` and the CLI do not.
- **L-8. Alerting gaps.**
  - Prod `AlarmEmail=""` (samconfig.toml:141), so no alarms exist at launch.
  - The Lambda `Errors` alarm misses handled 500s, because the global handler returns a response instead of crashing.
  - There is no alarm on API Gateway 5xx.
- **L-9. CORS.** The gateway's `*` overrides the app's CORS settings (probe 2). The app's prod origin list is dead code and omits `www.materialsdatafacility.org`; don't tighten the gateway without adding it. Preflights reach the Lambda (`x-request-id` on OPTIONS) and use concurrency. Bearer-only auth makes `*` safe.
- **L-10. Minor information exposure.**
  - `Authentication failed: {e}` echoes exception text (`auth.py:281`).
  - Recipient emails are logged at INFO (`email_utils.py:74`).
  - Anyone can inflate `view_count`; every anonymous card or preview GET is a DynamoDB write.
- **L-11. Dependencies.** Only floor pins and no lockfile. `mdf-toolbox==0.6.0` is never imported, but it pulls `jsonschema<=4.3.0`, fair-research-login and globus-nexus-client into the bundle. The last *local* SAM build had fastapi 0.141.1, starlette 1.3.1, h11 0.16.0 and urllib3 2.7.0. I am not asserting any specific CVEs. **Fix:** add a lockfile and run `pip-audit` in CI.
- **L-12. Restricting a dataset only hides new versions (design).** The ACL is stored per version, so earlier public versions remain available via `?version=`.
- **L-13. Editing a non-latest published version breaks the version chain.** It either creates a fork with two `latest=True` versions, or hits a version collision and returns 500 (after `_flip_latest_on_prior` has already written).
- **L-14. Hypothesis, not checked.** A deployed dev stack (`AuthMode=dev`) falls back to production auth but keeps `ALLOW_ALL_CURATORS=true` and an empty submitter group. If a dev stack exists, every Globus user is a curator there.

## 3. Prior fixes: verified in HEAD, and live on staging?

| Fix | HEAD | Live on staging |
|---|---|---|
| Groups-token identity binding (`auth.py:249-266`) | holds | NO |
| Auth cache keyed on bearer + groups token | holds | NO |
| Rate limiter ignores X-User-Id in prod; per-IP ceiling | holds (but see M-2) | NO |
| `AUTH_MODE=dev` refused without LOCAL_DEV_AUTH / AWS_SAM_LOCAL | holds | yes |
| source_id grammar, collision suffix, `_doi_belongs_to_source` DOI guard | holds | NO |
| `can_view_dataset` on card, citation, detail, preview | holds | yes |
| `can_view_dataset` on status, versions, diff, stats | holds | NO (published-only; stats has no auth) |
| Author index and snapshot built from public records only | holds (but see M-5) | NO |
| B-3 root-owner check on update (`submissions.py:308`) | holds | yes |
| /embed and /search/semantic require auth | holds | yes (probe 25) |
| SES scoped to `identity/*`; structured Subject (no header injection); HTML escaped | holds | yes (emails off on staging) |
| Link-health https-only, public-host check, per-hop redirect check | holds (but see L-5) | NO |

Other checks that passed:
- Transfer tokens stay in memory and are never written to the SQS queue.
- No tokens are logged.
- The 500 handler returns a generic body.
- S3 buckets have public access blocked and are encrypted.
- SQS is encrypted with a DLQ (max receive 3; visibility timeout 180 s is longer than the 120 s worker timeout).
- DynamoDB has point-in-time recovery and deletion protection.
- IAM is scoped to the stack's own resources.
- The v1→v2 migration carries `mdf.acl` into `acl`.
- Security headers are present live (HSTS, nosniff, X-Frame-Options DENY, no-referrer).
- The deploy workflow is manual-dispatch only, with a typed confirmation, a GitHub environment and OIDC.

## 4. Launch blockers vs fast-follows

**Blockers:**
1. Deploy HEAD and re-verify: `openapi.json`, and the `/stats` and `/related` probes.
2. Rotate both DataCite credentials (M-7).
3. Cap the search offset and stop the 4xx fallback scan (H-2).
4. Add a negative auth cache and a 401 limit (M-1).
5. Set the prod `AlarmEmail` and add an API Gateway 5xx alarm (L-8).
6. Decide on token audience (H-3): either implement introspection with the MDF client or record the accepted risk.
7. Strongly recommended: a separate Globus client per environment, so staging cannot write the prod index (M-6, and relevant to O-6).

**Fast-follows:** M-2, M-3 (policy decision), M-4, M-5, and L-1 through L-14.

Probe count: 26 GET/OPTIONS requests to staging, no authentication used, no writes. I did not probe the foreign gateway (O-6) because it is out of scope.
