# MDF Frontend Design Document — "Specimen"

**Date:** 2026-07-12
**Target repo:** `mdf-next2`
**Audience:** An implementing agent. Every phase has exact file paths, concrete values, and acceptance criteria. Do the phases in order — Phase 0 fixes a bug that invalidates any visual judgment made before it.
**Companion:** `2026-07-12-deployment-plan.md` Part C covers functional/deployment work; this doc covers identity and experience. C1 (security/dead-code cleanup) should land before or with Phase 0 here.

---

## 1. Diagnosis — why it reads as "uninspired," concretely

The current site is competent SaaS-template work wearing a science costume. Specific causes, all verified in code:

1. **The typographic identity literally does not render.** `font-serif` (Instrument Serif) is used in ~20 files as the brand differentiator, but a stale shadcn-generated `tailwind.config.js` shadows `tailwind.config.ts` (Tailwind resolves `.js` first), so none of the custom `fontFamily` mapping applies. Compiled CSS proves it: `.font-serif { font-family: ui-serif, Georgia, ... }`. Every headline on the site is system Georgia. The most-invested-in design decision is a no-op.
2. **One visual device repeated everywhere:** blue→indigo gradients — on every primary button (`app/components/home-hero.tsx:64`), as gradient-clipped headline text (`home-hero.tsx:50-52`), on `/spotlight`, on `/detail`. Gradient text and gradient-everything is the single most recognizable AI/template tell of this era.
3. **Stock SaaS scaffolding:** faint grid background + radial mask + blurred gradient orbs + pulsing-dot eyebrow pill + giant headline + CTA pair, repeated near-verbatim on `/`, `/search`, `/spotlight`. The hero "visual" is nested rotated rounded rectangles — an abstract placeholder where an identity should be.
4. **The hero-metric template:** dark stat band with big serif numbers and uppercase tracked labels (`home-hero.tsx:80-95`) — the canonical SaaS cliché.
5. **Zero domain imagery.** A materials-science facility with no micrographs, no crystal structures, no instruments, no data. `public/` holds institution logos and two unused decorative photos.
6. **No committed system:** shadcn tokens scaffolded then bypassed (pages hardcode `slate`/`blue` classes); two Tailwind configs; two `components/ui` dirs; three parallel spotlight variants (original/academic/modern) shipping simultaneously; dark-mode tokens and `next-themes` installed but never wired. Nobody — human or agent — can currently answer "what is the MDF brand color?" from the code.

The good news: spacing discipline, loading/error states, and the reduced-motion handling are already solid. This is a re-skin on a healthy skeleton, not a rebuild.

## 2. Design direction

### The idea

**"Specimen."** MDF is a scientific instrument, and its datasets are specimens — labeled, measured, catalogued, beautiful under magnification. The design should feel like the best of scientific material culture: the specimen label, the lab notebook rule, the SEM micrograph, the handbook table. Calm, precise, quietly monumental. The data is the decoration — real chemical formulas, real dataset identifiers, real micrographs — never abstract blobs.

Three voice words: **precise, archival, instrument-grade.**

One sentence for the scene: a computational materials scientist, mid-afternoon in a bright office, hunting for a training dataset between meetings — the interface should feel like a well-organized reference room, not a product pitch. (This is why the theme is light. Dark mode is a later, deliberate addition — remove the dead dark scaffolding for now.)

### Register

Dual, with a clear split:
- **Product register** (design serves the task): `/search`, `/detail`, `/publish`, `/curation`. Earned familiarity, density where useful, zero decorative motion, one accent color used only for actions/selection/state.
- **Brand register** (design is the message): `/`, `/spotlight/*`, `/guides`. This is where the identity gets to be iconic — one decisive image, committed color, typographic confidence. Still calm: single-purpose folds, no orchestration for its own sake.

### References (named, specific)

- **NASA JPL / Swiss-international scientific documentation** — hairline rules, strict grid, monochrome + one signal color. The confidence of a technical drawing.
- **SEM micrograph aesthetics** — monochrome scientific imagery is inherently striking; treated in duotone it becomes a brand, not a stock photo.
- **Specimen/museum labels** — small mono captions with measured facts (`source_id`, DOI, formula, size) presented as objects of care.

### Anti-references (what to refuse)

- Blue→indigo gradient SaaS (the current site). No gradients on text or buttons, anywhere.
- Apple-keynote product theatre (the `SPOTLIGHT_MODERN` direction — delete it).
- Editorial-magazine affectation (italic display serif + drop caps). The `SPOTLIGHT_ACADEMIC` layout structure (numbered sections, affiliations, abstract) is worth keeping; its journal-cosplay typography is not.
- Periodic-table motifs, atom-orbit icons, DNA helices — first-reflex science clip-art.

---

## 3. Foundations (the token layer)

### 3.1 Color — strategy: Restrained (product) / Committed (brand hero)

Monochrome ink-on-white carries the interface. **One** accent: **cobalt**. Cobalt is a real materials color — the glaze pigment, the alloy, element 27 — saturated enough to be a brand, serious enough for an instrument. It is used **solid, never as a gradient**, and on product surfaces only for actions, selection, links, and focus.

Define in `app/globals.css` (replace the current shadcn HSL block's values; keep the variable names so shadcn components keep working):

```css
:root {
  /* Ground */
  --background: 0 0% 100%;          /* pure white */
  --surface-2: 220 14% 97%;         /* panels, table stripes — cool, not cream */
  /* Ink */
  --foreground: 228 20% 13%;        /* near-black, cool cast — body text */
  --muted-foreground: 228 10% 38%;  /* secondary text — 4.5:1+ on white; do NOT go lighter */
  /* Brand */
  --primary: 219 100% 36%;          /* cobalt #0052B8 — buttons, links, selection */
  --primary-foreground: 0 0% 100%;
  --cobalt-deep: 222 85% 24%;       /* hover/active, duotone shadow tone */
  --cobalt-wash: 219 100% 96%;      /* selected-row / chip background */
  /* Structure */
  --border: 228 12% 88%;            /* hairlines */
  --ring: 219 100% 36%;
  /* Semantic: keep shadcn defaults for destructive; add: */
  --success: 152 60% 30%;
  --warning: 36 90% 40%;
  /* Data-viz secondary (charts only, never UI chrome): */
  --viz-amber: 36 95% 55%;
}
```

Rules for the implementer:
- Replace every `bg-gradient-to-*`, `bg-clip-text`, `from-blue-*`, `to-indigo-*` in `app/` and `components/` with solid tokens. `grep -rn "gradient" app/ components/` must return only the two allowed cases: the duotone image overlay (3.4) and the hero fade-to-white seam if kept.
- Replace hardcoded `slate-*`/`blue-*`/`indigo-*` classes with semantic tokens (`bg-background`, `text-foreground`, `text-muted-foreground`, `bg-primary`, `border-border`) file by file. New rule: **raw palette classes are banned outside `globals.css`.**
- Contrast floor: body text ≥ 4.5:1, large text ≥ 3:1. The tokens above pass on white; don't "lighten for elegance."
- Delete the `.dark` token block, the `prefers-color-scheme` block at the top of `globals.css`, and the `next-themes` dependency. Dark mode returns later as a project, not as dead weight.

### 3.2 Typography

Drop Instrument Serif entirely (it never rendered, so nothing visually shipped is lost). Two families:

- **UI / body / display: Hanken Grotesk** (Google Fonts, variable). A humanist grotesk — warm precision; carries everything from 12px labels to 72px display via weight contrast (400/500/650/800).
- **Data / labels: Spline Sans Mono** (Google Fonts). For `source_id`s, DOIs, formulas, file sizes, table numerics, code snippets. The mono is a *domain* voice here — identifiers are first-class objects — not a "technical" costume.

Implementation: in `app/layout.tsx` replace the Geist/Instrument `next/font` imports with `Hanken_Grotesk` (`variable: '--font-sans'`) and `Spline_Sans_Mono` (`variable: '--font-mono'`). In the (single, see Phase 0) Tailwind config: `fontFamily: { sans: ['var(--font-sans)', 'system-ui', 'sans-serif'], mono: ['var(--font-mono)', 'ui-monospace', 'monospace'] }`. Remove all `font-serif` usages (20 files — mechanical replace with weight/size per the scale below).

Scale (fixed rem, product-first; ratio ~1.2):

| Step | Size / weight / tracking | Use |
|---|---|---|
| display | 4.5rem, 800, -0.03em, leading 1.02 | Home hero only; `sm:` 3.25rem |
| h1 | 2.25rem, 800, -0.02em | Page titles, dataset titles |
| h2 | 1.5rem, 650, -0.01em | Section heads |
| h3 | 1.17rem, 650 | Card/subsection heads |
| body | 1rem, 400, leading 1.65 | Prose; max width `65ch` |
| small | 0.875rem, 400 | Secondary text |
| label | 0.8125rem, 500 | Form labels, table headers (sentence case — **no uppercase tracked eyebrows**) |
| data | 0.875rem mono, 450 | IDs, DOIs, formulas, numerics (`tabular-nums`) |

Keep `text-wrap: balance` on h1–h3 (already in `globals.css`); add `text-wrap: pretty` to prose containers. Render chemical formulas properly: create `app/components/formula.tsx` that wraps digits in `<sub>` (`LiFePO4` → LiFePO₄) for use in titles, chips, and specimen labels.

### 3.3 Shape, space, elevation

- `--radius: 0.375rem` (6px). Buttons/inputs 6px, cards/panels 8px (`rounded-lg`), images 8px. **Global replace `rounded-2xl`/`rounded-3xl`/`rounded-[2.5rem]`-style arbitrary radii** — the pillowy corners are half of the "template" feel. `rounded-full` only for genuine pills (status chips, avatar).
- Structure with **hairlines, not shadows**: `border border-border` and 1px rules replace `shadow-lg`/`shadow-2xl`/colored `shadow-blue-600/25`. Permitted shadows: a single `shadow-sm` on sticky headers and open popovers/dialogs. Delete all colored shadows.
- Section rhythm on brand pages: vary it — hero generous (`py-20`), content sections `py-16`, related strips `py-10`. Product pages: page header `py-8`, then content density as the task needs.
- Z-index scale in the Tailwind config: `dropdown:20, sticky:30, overlay:40, modal:50, toast:60, tooltip:70`. Replace any arbitrary `z-50`+ values.

### 3.4 Imagery — the signature

**One decisive treatment: duotone micrographs.** Acquire 4–6 public-domain/CC scanning-electron-microscope images (sources for the implementer: NIST image galleries, Wikimedia Commons "Category:Scanning electron microscope images", ZEISS Microscopy Flickr CC pool — verify each license, record attribution in `public/micrographs/CREDITS.md`). Choose crystalline/dendritic/lattice subjects with strong texture at 1600px+.

Standard treatment, as a reusable component `app/components/micrograph.tsx`:

```tsx
// <Micrograph src alt credit priority?/> — grayscale base + cobalt duotone via CSS
<figure className="relative overflow-hidden rounded-lg border border-border">
  <Image ... className="grayscale contrast-110" />
  <div aria-hidden className="absolute inset-0 mix-blend-multiply"
       style={{ background: 'linear-gradient(160deg, hsl(var(--cobalt-deep)/.55), hsl(var(--primary)/.18))' }} />
  <figcaption className="absolute bottom-2 right-3 font-mono text-[11px] text-white/70">{credit}</figcaption>
</figure>
```

This is the one permitted gradient (it's an image treatment, not chrome). Alt text is voice: "Dendritic solidification in an aluminum alloy, SEM" — never "science image."

### 3.5 The specimen label — signature component

A reusable metadata block that becomes the site's most recognizable element. `app/components/specimen-label.tsx`: a bordered panel (`border border-border rounded-lg`, white bg) with hairline-ruled rows; each row = sentence-case label (small, `text-muted-foreground`) left, mono value right (`source_id`, version, DOI as a link, size, files, license, date). One cobalt element max: the DOI link. Use it on `/detail` (sidebar), search-result hover cards, and the spotlight header. No icons in it. It should look like it was typed by a careful archivist.

---

## 4. Page-by-page specification

### 4.1 Navigation & footer (do first — sets the vocabulary)

- `app/components/navbar.tsx`: white bg, 1px bottom hairline, wordmark left ("Materials Data Facility" — full name, settle the naming drift; footer's "MDF Next" goes away), links center-right in 500-weight sentence case, active link = cobalt text (no pill, no underline animation). One primary button: "Publish data" (solid cobalt, 6px radius). Sticky with `shadow-sm` after scroll. Mobile menu: full-width white sheet, same link list, no motion beyond a 150ms fade.
- `app/components/footer.tsx`: swap `bg-slate-900` for white-with-top-hairline. Three columns: about + institution logos (keep, they're real credibility), product links, "Cite MDF" block with the paper citation in mono small. Quiet.

### 4.2 Home `/`

Replaces the current hero+blobs+stat-band. Five folds, each single-purpose:

1. **Hero.** Two-column: left — display headline in Hanken 800: **"The archive of record for materials data."** (or the incumbent "Publish. Discover." pair rewritten as a statement, not three imperatives); one sentence of body; two buttons (solid cobalt "Search datasets", hairline-outline "Publish data"). Right — one full-height duotone `<Micrograph>`, the best image of the set. No grid overlay, no orbs, no eyebrow pill, no geometric composition. Delete all of `home-hero.tsx`'s decorative background layers.
2. **Live search strip.** A functional search input (submits to `/search`) on a `--surface-2` band with 4–5 example-query chips ("perovskite band gaps", "high-entropy alloys", …). Function as decoration.
3. **Facts row.** The 880+ datasets / 600 TB / 60+ ML-ready stats survive but as a single hairline-ruled row: mono numerals (`tabular-nums`, h2 size), sentence-case labels beneath, white background, thin top-and-bottom rules. Not a dark card. Not the hero-metric template — it reads as a table row from a handbook.
4. **Two paths.** "For data authors" / "For data users" — two halves split by a vertical hairline (not identical cards): each with an h2, three short steps in prose, and one link. Content from the current Discover/Host sections, tightened.
5. **Spotlight teaser.** Current spotlight preview restyled: micrograph thumbnail, title, one-line summary, specimen-label fragments (formula count, size), "Read the spotlight →" in cobalt.

Delete: `home-features.tsx`, `home-stats.tsx`, `home-sections.tsx`, `home-infrastructure.tsx`, `home-narrative.tsx` (already orphaned), and the citation section moves into the footer.

### 4.3 Search `/search` (product register)

Keep the architecture (filters, URL sync, skeletons, error/empty states — all good). Restyle + densify:
- Kill the hero band; a compact header row: "Search" h1, result count in mono, sort control (**add it** — `sort` is supported by `lib/api/v2-client.ts`; options: relevance, newest, most viewed).
- Results as **list rows, not card grid**: title (h3, cobalt on hover), one-line description, then a mono metadata line (`source_id · v2.1 · 14 files · 3.2 GB · 2026-05-12`). Hairline between rows. Denser = more results per viewport = better product. Keep the hover-prefetch behavior.
- Filters in a left rail on `lg:`, sentence-case labels, checkbox counts in mono. Selected filter chips get `--cobalt-wash` bg + cobalt text.
- Semantic search: add a "Describe it instead" toggle above the input that switches to `/search/semantic` (endpoint + client method exist). Placeholder: "e.g. DFT-computed formation energies for ternary oxides". This is the flagship feature — give it the position, not a buried tab.

### 4.4 Detail `/detail/[source_id]` (product register)

Keep the structure (it's the strongest page). Changes:
- Header: dataset title h1; authors as plain text with ORCID links; a `<Formula>`-aware chip row for materials; the **specimen label** panel top-right (replaces scattered metadata), containing the DOI as the single cobalt link.
- Citation block: mono, in a `--surface-2` panel with a copy button per format. Already good functionally.
- Add the missing capability UIs (client methods exist, see audit): **file browser + sample preview** (new section, table of files in mono with size/type, expandable preview via `/preview` endpoints); **version history with diff** (timeline in the sidebar; "compare" renders `getVersionDiff` as added/changed/removed rows); **withdraw/resubmit** for owners (quiet outline buttons in an "Owner tools" hairline box — never modal-first; confirm inline).
- Strip framer-motion entrance animations (`detail/[source_id]/page.tsx:691,855,1087`) — product pages load into the task, no choreography.

### 4.5 Publish `/publish` (product register)

Functionally rich; visually it needs calm and orientation, not redesign:
- Add a left step-rail (Metadata → Authors → Data location → Review) with the current step in cobalt; the 1,861-line page gets visual chunking without a rewrite.
- Form vocabulary from §5 everywhere; auto-suggested tags become **opt-in** (render as outlined suggestions the user clicks to add — fixes audit `PUB-15`).
- Review step renders the draft as a **specimen label + abstract** — "here is your dataset's catalog entry" is both a delight moment and an error-catching device.

### 4.6 Curation `/curation` (product register)

Restyle to the vocabulary (hairline table rows, chips, mono IDs). Approve = solid cobalt, Reject = outline destructive; both with inline note field, no modals. Add queue-position/median-time header (data exists in `curation_history`).

### 4.7 Spotlight `/spotlight/*` (brand register)

Consolidate: **keep the `-academic` component family as the base, delete `-modern` and the unsuffixed originals** (six files). Restyle the academic layout with §3 tokens: Hanken everywhere (no journal-serif cosplay), full-bleed duotone micrograph hero with title overlaid, numbered sections **allowed here** (a spotlight genuinely is a sequential article — this is the one earned use), specimen label for the dataset facts, hairline-ruled references. This becomes the template for future spotlights; document the recipe in a comment at the top of `app/spotlight/omol25/page.tsx`.

---

## 5. Component vocabulary (single source of truth)

Consolidate to **one** UI directory: keep `components/ui/` (shadcn), fold the five files in `app/components/ui/` into it (dedupe the second `button.tsx`/`popover.tsx`; move `institution-autocomplete.tsx`, `section.tsx`, `command.tsx` in). Update imports. Then restyle the shadcn layer once — every page inherits:

- **Button**: primary = solid cobalt, white text, 6px radius, `hover:bg-[hsl(var(--cobalt-deep))]`, 150ms; secondary = white, 1px border, ink text; destructive = outline red until confirm; all with visible `focus-visible:ring-2 ring-ring`. No gradient, no colored shadow, no translate-on-hover.
- **Card**: white, 1px border, 8px radius, no shadow at rest. Never nested.
- **Table**: hairline rows, `--surface-2` header band, mono numerics right-aligned, sentence-case headers.
- **Chip/Badge**: `rounded-full`, `--surface-2` bg default; selected = `--cobalt-wash` + cobalt text; status colors only for status (published green / pending amber / rejected red at the §3.1 semantic tokens).
- **Inputs**: white, 1px border, 6px radius, cobalt focus ring, placeholder at ≥4.5:1 (`text-muted-foreground`, not lighter).
- **States**: every interactive component ships default/hover/focus/active/disabled/loading. Keep skeletons (already good). Empty states teach: "No datasets match — try removing the year filter or switch to semantic search," with the action inline.
- Mount `components/ui/sonner.tsx`'s `<Toaster>` in `app/layout.tsx` (it's installed but never mounted) and use it for publish/curation confirmations.

## 6. Motion specification

Philosophy: motion conveys state; the identity is calm. Total inventory:
- Micro: 150ms ease-out color/border transitions on interactive elements; 200ms fade+2px-rise for popovers/dropdowns.
- Skeleton shimmer (existing) is the loading language; no spinners mid-content.
- Brand pages only: hero micrograph crossfades in on load (300ms, `ease-[cubic-bezier(0.22,1,0.36,1)]`); one stagger allowed on the spotlight references list. Nothing scroll-triggered.
- Product pages: zero entrance animation. Remove framer-motion from `/detail`; after Phase 4, if no import of `framer-motion` remains, remove the dependency.
- Keep the existing global `prefers-reduced-motion` block exactly as is.
- Delete `blob`, `shimmer`-on-hero, `animation-delay-*` utilities from `globals.css` once unreferenced.

## 7. Accessibility floor

- WCAG 2.1 AA. Contrast per §3.1 (the token values are chosen to pass; verify any new pairing).
- `aria-label` audit: currently only 5 files have any. Sweep every icon-only button (navbar toggle, copy buttons, curation actions, pagination) — each gets a label. Add a skip-link ("Skip to results" on `/search`, "Skip to content" globally) as the first element in `app/layout.tsx`.
- Focus visible everywhere (§5 button spec); keyboard-test the publish wizard and the new file browser.
- `<Formula>` must keep screen-reader-legible text (aria-label with the plain string).
- Duotone images: the overlay is `aria-hidden`; meaning never carried by color alone (status chips also carry text).

## 8. Implementation plan (phased, agent-executable)

**Phase 0 — Fix the foundation bug (½ day).**
1. Merge the `tailwind.config.ts` content into a single config; **delete one file so exactly one remains** (recommended: keep `tailwind.config.ts`, delete `tailwind.config.js`, update `components.json`'s `"config"` field to `"tailwind.config.ts"`).
2. Add the §3.2 `fontFamily` and §3.3 z-scale to it.
3. Acceptance: `npm run build` succeeds; in the built CSS (`.next/static/chunks/*.css`), `.font-mono` resolves to `var(--font-mono)`; exactly one `tailwind.config.*` exists.
4. Do deployment-plan C1 (delete `/tdb`, dead files, console.logs, `ignoreBuildErrors: false`) in the same PR.

**Phase 1 — Tokens + fonts (1 day).** §3.1 CSS variables into `globals.css` (delete dark block, blob/shimmer keyframes, dot-pattern utilities); §3.2 fonts into `app/layout.tsx`; global mechanical replaces: `font-serif` → scale classes, `rounded-2xl/3xl` → `rounded-lg`, gradient/colored-shadow classes → solid tokens. Acceptance: `grep -rn "font-serif\|bg-clip-text\|from-blue\|to-indigo\|shadow-blue" app/ components/` → empty; build green; all 119 tests pass.

**Phase 2 — Vocabulary + nav/footer (1–2 days).** §5 UI-directory merge and shadcn restyle; navbar/footer per §4.1; mount `<Toaster>`. Acceptance: one `ui/` dir; every page renders with new chrome; keyboard focus visible on nav.

**Phase 3 — Home (1–2 days).** §4.2, including micrograph acquisition + `<Micrograph>` + `CREDITS.md`. Acceptance: no grid/orb/eyebrow/gradient remnants (`grep -n "blur-3xl\|bg-dot-pattern" app/` → empty); Lighthouse a11y ≥ 95 on `/`; hero image has real alt text and `priority`.

**Phase 4 — Search + Detail (2–3 days).** §4.3–4.4 including sort control, semantic toggle, specimen label, file browser, version diff, withdraw/resubmit, framer-motion removal. Acceptance: the six audit gaps (`docs/audit/contract-surface.md`) for these pages closed; `npx vitest run` green; new components get unit tests for their formatting logic (specimen label rows, formula subscripting).

**Phase 5 — Publish + Curation (2 days).** §4.5–4.6. Acceptance: publish flow keyboard-navigable end-to-end; auto-tags opt-in; curation actions inline (no new modals).

**Phase 6 — Spotlight consolidation (1 day).** §4.7; delete 2 of 3 component families. Acceptance: `ls app/components/spotlight-*` shows one family; `/spotlight/omol25` renders; template recipe comment present.

Each phase = one PR. After each: `npm run build && npx vitest run`, plus a screenshot pass at 375px/768px/1440px checking for headline overflow and horizontal scroll.

## 9. What NOT to do (guardrails for the implementing agent)

- No gradients on text, buttons, or backgrounds (duotone image overlay is the sole exception).
- No uppercase-tracked eyebrow labels above sections; no `01/02/03` section numbers **except** inside spotlight articles.
- No new cards where a ruled row or plain section works; never nest cards.
- No colored left-border accent stripes on callouts (use full hairline border + `--surface-2`).
- No glassmorphism/`backdrop-blur` chrome; no decorative blur orbs.
- No new fonts beyond Hanken Grotesk + Spline Sans Mono; no italic display type.
- No scroll-triggered reveals; no hover `translate-y` lifts.
- Don't invent new colors — if a hue isn't in §3.1, it doesn't exist. Charts use `--primary`, `--viz-amber`, then neutral grays.
- Don't touch `lib/api/*` logic while restyling; presentation-only diffs per phase.

---

## Appendix — proposed PRODUCT.md and DESIGN.md for `mdf-next2`

These were **not** written into `mdf-next2` (owner approval pending). On approval, commit verbatim as `PRODUCT.md` and `DESIGN.md` at that repo's root; the impeccable tooling and future agents will read them.

### PRODUCT.md

```markdown
# Product

## Register

product

## Users
Materials researchers (graduate students → PIs) and ML practitioners. Two jobs:
(1) find and evaluate datasets for research/training — often mid-task, time-boxed;
(2) publish datasets to satisfy funder mandates and earn citable credit with minimal friction.
Curators (facility staff) review submissions. Increasingly: AI agents via API/CLI/MCP.

## Product Purpose
The Materials Data Facility is the archive of record for materials-science data:
publication with DOIs and versioning, curation, and search (keyword + semantic) over
900+ datasets / 600+ TB. Success = time-to-first-publication under 10 minutes and
researchers trusting a dataset enough to download it after previewing on-site.

## Brand Personality
Precise, archival, instrument-grade. The interface is a scientific instrument and a
reference room, not a product pitch. Calm confidence; data as the decoration.

## Anti-references
Blue/indigo-gradient SaaS templates; Apple-keynote product theatre; editorial-magazine
cosplay (italic display serifs, drop caps); periodic-table/atom clip-art; dashboard
"metric hero" bands.

## Design Principles
1. The data is the decoration — real formulas, identifiers, and micrographs over abstractions.
2. Specimen-label care — metadata presented as objects of craftsmanship.
3. Earned familiarity — standard affordances, density where the task wants it.
4. One signal color — cobalt marks action and selection, nothing else.
5. Calm is credibility — motion conveys state; stillness conveys authority.

## Accessibility & Inclusion
WCAG 2.1 AA. Body contrast ≥ 4.5:1. Full keyboard operability (publish wizard included).
prefers-reduced-motion respected globally. Formulas readable by screen readers.
```

### DESIGN.md (abridged — token source of truth is §3 of the design doc)

```markdown
# Design

## Theme
Light, white ground, hairline-structured. No dark mode (deliberate, revisit later).

## Color
Ink #1A1D29-equivalent (hsl 228 20% 13%) on white. Accent: cobalt hsl(219 100% 36%),
solid only — actions, links, selection, focus. Panels hsl(220 14% 97%). Hairlines
hsl(228 12% 88%). Semantic: success 152/60/30, warning 36/90/40, destructive shadcn
default. Chart secondary: amber 36/95/55. No gradients except the cobalt duotone
image overlay.

## Typography
Hanken Grotesk (400/500/650/800) for everything; Spline Sans Mono for identifiers,
DOIs, formulas, numerics (tabular-nums). Fixed rem scale, ratio ~1.2; display 4.5rem/800
on home only. Sentence case labels; no uppercase tracked eyebrows.

## Components
shadcn/ui in components/ui/ restyled: 6px radius controls, 8px panels, 1px borders,
no resting shadows, cobalt focus rings. Signature components: SpecimenLabel
(ruled metadata panel), Micrograph (duotone SEM imagery), Formula (subscripted).

## Layout
max-w-7xl product shells, 65ch prose. Hairlines over shadows. Search results are
ruled list rows, not card grids.

## Motion
150–250ms ease-out state transitions; skeletons for loading; hero crossfade on
brand pages only; zero entrance animation on product pages; reduced-motion honored.
```
