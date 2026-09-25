<!-- Saved 2026-09-25 from a read-only audit lane (impeccable critique method, dev server with NEXT_PUBLIC_USE_V2_API=true against the staging API). Items are cited by plans/2026-09-25-beta-launch-plan.md as FE-B<n> (launch-blocking) and FE-S<n> (should-fix). Screenshots were in the session scratchpad and were not kept; re-capture with the `capture` skill. -->

# MDF v2 frontend: pre-launch design and UX audit

Branch `release-fixes-2026-09-04`, run with `NEXT_PUBLIC_USE_V2_API=true` against the staging API.

## 1. Overall assessment

The visual system (Instrument) mostly holds up and is better than most research-data portals. The homepage hero
has a real micrograph, a working search box, amber data ticks and dark panel folds, and DESIGN.md is visibly
followed on the brand surfaces. The problems are about **trust**, not looks:

- Identifiers don't resolve: v1-style DOI landing URLs 404, and one featured homepage dataset is among them.
- The CLI page tells users to log in to staging.
- Citations for new records have no DOI.
- The dataset count contradicts itself: 935 on the homepage, 1,600+ on the 404 page.

Search and detail work on desktop but are weak on mobile: the first search result sits about 2,000px down, and
the download button about 4,400px down. The dataset page breaks DESIGN.md with justified, hyphenated serif text,
and the file preview is empty for essentially every dataset. The main code risk is three very large client pages
still carrying v1 and v2 paths behind a flag that defaults to v1.

**Direction for launch: a trust pass, not a redesign.**
- Every identifier (DOI, citation, CLI command, count) must be correct and resolve.
- Make the dataset page an instrument readout: DOI, license, size and the access button in the first viewport on
  every device.
- Hide features that aren't real yet ("Most viewed" sort, the empty Files panel, the debug label).
- Don't add new visuals before launch.

**Heuristic scores: 26/40**

| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | System status | 3 | Preview fetch has no timeout; the Files skeleton sticks |
| 2 | Real-world match | 3 | "Recently published" is really ingest order |
| 3 | User control | 2 | Sign-in always lands on `/`; the back link always goes to bare `/search` |
| 4 | Consistency | 2 | 4 different sign-in gates; pills vs rounded corners; 935 vs 1,600+ |
| 5 | Error prevention | 3 | Publish form validates inline and autosaves a draft |
| 6 | Recognition | 3 | Filters are in the URL; active-filter chips are shown |
| 7 | Efficiency | 3 | CLI, notebook download, deep links |
| 8 | Minimalist design | 3 | Boxes nested inside boxes on the citation card |
| 9 | Error recovery | 2 | DOI 404s; `/auth` spins forever |
| 10 | Help / docs | 2 | CLI docs point at staging |

## 2. Issues by severity

### Launch-blocking

- **FE-B1. v1-style DOI landing URLs land on a 404.** S frontend, plus a backend follow-up.
  - **Problem:** v1 prod mints DOIs whose landing URL is
    `https://materialsdatafacility.org/detail/<uuid>-<version>` (example: `10.18126/4h7n-c587` →
    `/detail/de6b89c7-…-1.0`). `/card/<uuid>-1.0` 404s, so `layout.tsx:94` calls `notFound()`. The `_v1.2` form
    resolves, but the API returns the v1.0 card (the requested version is ignored). About 22% of records have
    UUID-style ids.
  - **Beta vs cutover:** during the beta, DOIs still resolve to the v1 www site, so this bites fully at cutover.
    It matters on staging for any hand-typed or linked v1 URL.
  - **Fix:** in `lookupCard`, when `/card/{id}` 404s, match a trailing `-(\d+(\.\d+)+)$` or
    `_v(\d+(\.\d+)+)$`, retry with the base id, and `redirect()` to `/detail/<base>?version=<v>`. Ask the backend
    to accept these aliases in `/card` too.
- **FE-B2. `/cli` tells users to `mdf login --service staging`.** S.
  - The command appears in 3 places. The demo also shows the staging target URL and `Service: staging`.
  - **Fix:** use bare `mdf login` and update the demo's target and service label.
  - **Where:** `app/cli/_components/cli-quickstart.tsx:15`, `cli-tasks.tsx:28`,
    `app/cli/_lib/session-script.ts:40,123,137,166,202`.
- **FE-B3. Citations omit the DOI.** S frontend fallback; the real fix is backend GAP-1.
  - APA has no DOI, BibTeX has no `doi` field, and the DataCite XML says `10.xxxxx/pending`, while the card shows
    the DOI.
  - **Fix this week:** if a citation string lacks the card's DOI, append `https://doi.org/<card.doi>`. Hide the
    DataCite tab when it contains `pending`.
- **FE-B4. Homepage featured cards link off-site through doi.org** (`home-highlights.tsx:26-56`). That's a
  different host and a double redirect, and one target is FE-B1. **Fix:** link to `/detail/<source_id>` and show
  the DOI as text. S.
- **FE-B5. Dataset counts contradict each other.**
  - Homepage "935 datasets · 1,000 TB+"; 404 page "1,600+" (`app/not-found.tsx:48`); PRODUCT.md 1,600+ / 600 TB.
  - `SITE_STATS` in `app/lib/site-stats.ts:14` is hand-maintained.
  - **Fix:** one source (the live `total` from `/search`, with `SITE_STATS` as fallback); check the TB figure. S.
- **FE-B6. Mobile search puts every filter above the results.** Four expanded filter groups plus a publish promo
  come before the first result (about y=2,000 at 390px).
  - **Fix:** below `lg`, a "Filters (n)" button that opens a sheet; move the promo below the results. S/M.

### Should-fix this week

- **FE-S1.** Mobile detail page buries the DOI and access block about 4,400px down. Add a compact
  "DOI · License · Access" strip under the title below `lg`. The "Download with the CLI ↓" arrow points the wrong
  way on mobile. M.
- **FE-S2.** Justified, hyphenated serif abstract (`detail/[source_id]/page.tsx:1087`) violates DESIGN.md. Use
  `font-sans`, left-aligned, `max-w-prose`. S.
- **FE-S3.** "Most viewed" sort is fake (identical to newest). Hide it until GAP-7 lands. S.
- **FE-S4.** "Recently published" is really ingest order (`search/page.tsx:765`). Rename it "Recently added" or
  sort by `publication_year`. S.
- **FE-S5.** Blank Organization facet value (count 100). Drop it or label it "Unspecified". S.
- **FE-S6.** Year facet is sorted by count. Sort years descending. S.
- **FE-S7.** Debug label "MDF v2 API" at `search/page.tsx:772`. Delete it. S.
- **FE-S8.** Result cards omit authors. Show the first 3 plus "et al." and humanize taxonomy keywords
  (`Computational File Repository Categories::…`). S.
- **FE-S9.** Search fires duplicate requests (3 identical calls on a deep link). A stale response can overwrite a
  newer one, and a zero-result query sometimes shows all 935. **Fix:** a request sequence or AbortController, and
  hydrate from the URL in one effect (`search/page.tsx:159-213`). S/M.
- **FE-S10.** Sign-in loses the return path: `app/auth/page.tsx:100` always goes to `/`. Store `returnTo`. `/auth`
  with no `code` spins forever; show "Nothing to finish — go back". The page uses `slate-*` colours, not tokens. M.
- **FE-S11.** The Files section is effectively empty: 0 of 60 sampled datasets have profiles. The fetch has no
  timeout (`file-browser.tsx:137`). Add a timeout and collapse the section to one line when there's no profile. S.
- **FE-S12.** Search engine discoverability.
  - No `schema.org/Dataset` JSON-LD on detail pages.
  - The server HTML has about 22 words.
  - The homepage JSON-LD uses `next/script`, so it isn't in the server HTML.
  - `sitemap.xml` is static.
  - No `metadataBase`, `og:image` or canonical URL.
  - `robots.txt` allows everything, so staging gets indexed.

  **Fix:** JSON-LD as a plain `<script>` in `detail/[source_id]/layout.tsx`, a dynamic `app/sitemap.ts`, and
  noindex on non-production hosts. M.
- **FE-S13.** Accessibility. S.
  - The `--input` border is 1.34:1 against white; WCAG 1.4.11 needs 3:1. Darken it to about 60% lightness.
  - No `<main>` on the detail page.
  - No `aria-current` on nav links.
  - No skip link.
  - The publish "Data location" `<label>` has no `htmlFor`; use a fieldset/legend.
- **FE-S14.** Four inconsistent signed-out gates (`curation/page.tsx:386`, `admin/page.tsx:289`,
  `my-datasets/page.tsx:339`, `publish/page.tsx:1179`). Build one `<SignInGate>` with "Sign in with Globus"
  everywhere. On `/publish`, say what's needed before signing in: title, authors, license, data location, about
  10 minutes, and the MDF group requirement. S.
- **FE-S15.** The publish success screen is weak. Say what happens next (curator review → DOI, typical
  turnaround) and link to `/my-datasets` and the dataset page. S.

### Nice-to-have

- The OMol25 spotlight uses an unrelated NASA detector image labelled "Illustrative specimen".
- The OMol25 citation still says "Full citation will be provided upon official publication"; use arXiv 2505.08762.
- `/spotlight` has a 4-figure stats band for a single release (the metric-hero anti-pattern).
- The homepage spotlight image shows the microscope's info bar cropped at the bottom edge.
- `rounded-full` appears 70 times, though DESIGN.md allows pills only for status badges. Move to `rounded-md`.
- Version dates use the browser locale; use ISO. Consider collapsing the 10 migration versions dated 7/31.
- Fold the lone "Statistics: Views" card into the facts table.
- Mobile menu buttons have different widths.
- Mobile CLI text is about 9px and commands wrap mid-flag; scroll code blocks instead of wrapping.
- Show "License: not specified" when a record has no license.

## 3. Code quality relevant to shipping

- **The v1/v2 flag defaults to v1** (`lib/api/feature-flags.ts:7`). Make v2 the default, or fail the build when
  it's unset.
- **Flag branches:** 38 in `detail/page.tsx`, 20 in `publish/page.tsx`, 13 in `search/page.tsx`, 7 in
  `citationSection.tsx`. Delete the v1 paths after launch.
- **Very large client pages:** `app/publish/page.tsx` 2,082 lines (36 `useState`),
  `app/detail/[source_id]/page.tsx` 1,531, `app/curation/page.tsx` 935, `app/search/page.tsx` 813,
  `app/my-datasets/page.tsx` 724.
- **Duplicated UI:** a "My datasets" tab in `/publish` (`publish/page.tsx:1774`) alongside `/my-datasets`, and
  four copy-pasted sign-in gates.
- **Hardcoded colours instead of tokens:** `slate-*` in `FoundryDataTable.tsx` (31), `auth/page.tsx` (14),
  `FoundryCodeBlock.tsx` (11); `amber-*` text on light backgrounds in `publish/page.tsx` (15).
- **Unused global CSS:** `app/layout.tsx:5-6` imports `@radix-ui/themes/styles.css` (806 KB unminified) for a bare
  `<Theme>` wrapper.
- **Unused dependencies:** `recharts`, `easymde`, `react-simplemde-editor`, `cmdk`, `lodash`,
  `react-copy-to-clipboard`. `axios` is used only in publish and the API routes.
- **Fragile search effects** (`search/page.tsx:159-213`) cause the stray request and the triple fetch.

## 4. Top 10 for a one-week window (auditor's order)

1. FE-B1 DOI landing alias.
2. FE-B2 staging copy on `/cli`.
3. FE-B3 DOI in citations.
4. FE-B4 and FE-B5: internal featured links and one source for counts.
5. v2 flag default / fail the build when unset.
6. FE-B6 and FE-S1: mobile filter sheet and detail readout strip.
7. Search honesty pass: FE-S3 through FE-S8.
8. FE-S10, FE-S14, FE-S15: return path, `/auth` without a code, `<SignInGate>`, publish success screen.
9. FE-S2 and FE-S11: detail typography and file-preview timeout/collapse; license row; `<main>`.
10. FE-S12 and FE-S13: JSON-LD, sitemap, noindex on staging, `--input` contrast, `aria-current`, skip link.
