# Metadata checklist — the drafting contract

This is the field-by-field contract for writing `mdf.yaml`. For each key: what it is,
which v2 metadata field it becomes, what evidence justifies it, and what will bite you.

This file mirrors the server's `DatasetMetadata` Pydantic model, but it is a copy.
`mdf validate --json --service staging` runs client-side manifest/preflight checks;
the **server's validation at submit time and the `mdf publish` dry-run payload are the
ground truth**. If they disagree with this document, trust them and tell the user the
skill needs a refresh.

**The omission rule applies to every field below.** No evidence → leave the key out.
Never write `TODO`, `Unknown`, `N/A`, or a plausible guess.

---

## Required

### `title:` → `title`
A specific, searchable sentence fragment. Not the directory name, not "Dataset".
Include the material/system, the measurement or method, and the scope.

- **Evidence:** the paper title (Crossref-verified), the README H1, or the user.
- **Bad:** `perovskite data`. **Good:**
  `DFT-computed formation energies for 4,832 halide perovskite compositions`.
- A list is accepted; only the first entry is used. Prefer a plain string.

### `authors:` → `authors`
A list. Each entry may be a bare string, but **prefer the full mapping** — bare strings
are the single biggest source of mangled records.

```yaml
authors:
  - name: "van der Berg, Anneke"
    given_name: "Anneke"
    family_name: "van der Berg"
    affiliations: ["University of Amsterdam"]
    orcid: "0000-0002-1825-0097"
```

Pitfalls, in order of how often they bite:

1. **Always emit explicit `given_name` and `family_name`.** If you omit them, the server
   falls back to parsing `name`: it splits on a comma if there is one, otherwise it
   splits on the **last whitespace**. That turns "Anneke van der Berg" into
   given="Anneke van der", family="Berg". Multi-word surnames, particles ("van", "de",
   "bin"), and mononyms all break. Setting both fields explicitly bypasses the parser
   entirely.
2. **`name` should be `"Family, Given"`** — that is the form the server reconstructs and
   what DataCite expects.
3. **`orcid` must be the bare ID** — `0000-0002-1825-0097`. Do **not** prefix
   `https://orcid.org/`; the server adds it when building the DataCite record. A URL
   here produces a doubled prefix. (`mdf validate` warns about this.)
4. **Affiliations come from the README or the paper only.** Never infer an institution
   from an email domain, a name, or general knowledge.
5. Author order is meaningful — preserve the paper's order.

---

## Strongly recommended — these are the point of this skill

### `description:` → `description`
The sparsest field in the MDF corpus, and the one that determines whether anyone ever
finds or reuses this dataset. Write **at least two paragraphs**:

- **Paragraph 1 — what / how / why.** What the dataset contains, how it was produced
  (experiment, simulation, or derivation), and what question it was made to answer.
- **Paragraph 2 — contents and structure.** What is in the files: how many records,
  what the directory layout means, what the columns/keys are, units, and any known
  limitations, exclusions, or caveats.

- **Evidence:** README, paper abstract + methods section, plus your own file-walk and
  sampling results (the file counts and column names are yours to state as fact).
- Use a YAML block scalar (`description: |`) so paragraphs survive.

### `methods:` → `methods` (list of strings)
The other sparse field. Instruments, simulation codes, and techniques — one entry each.

```yaml
methods:
  - "VASP 6.3.0, PBE functional, 520 eV cutoff"
  - "Powder X-ray diffraction (Cu Kα, Bruker D8 Advance)"
```

- **Evidence:** the paper's methods section, README, or the data files themselves
  (an `INCAR` tells you the DFT parameters; instrument headers tell you the instrument).
- Include version numbers and key parameters when you have them.

### `subjects:` → `keywords`
Note the rename: the manifest key is **`subjects`**, and it becomes `keywords` in the
v2 metadata. There is no `keywords:` key in `mdf.yaml`.

- 6–12 entries. Specific over generic. Include material systems, methods, properties,
  and any accepted community terms.
- **Evidence:** paper keywords, README headings, recurring column names, directory names.
- Skip filler like "data", "science", "research".

---

## Attribution and provenance

### `license:` → `license`
Accepts either a bare SPDX identifier string or a mapping:

```yaml
license: "CC-BY-4.0"
# or
license:
  name: "Creative Commons Attribution 4.0 International"
  identifier: "CC-BY-4.0"
  url: "https://creativecommons.org/licenses/by/4.0/"
```

- **Evidence, in priority order:** a `LICENSE`/`COPYING` file whose text you matched to
  an SPDX id; a license field in the source repository's metadata (Zenodo,
  GitHub, `pyproject.toml`); an explicit statement from the user.
- **Never infer a license from prose** ("freely available", "please cite us") and never
  guess. An unlicensed dataset is a real, publishable state — omit the key and mention
  it at the gate.

### `funding:` → `funding` (list of mappings)
```yaml
funding:
  - funder_name: "U.S. Department of Energy"
    award_number: "DE-AC02-06CH11357"
    award_title: "Materials Genome Initiative"
    funder_id: "10.13039/100000015"
    funder_id_type: "Crossref Funder ID"
```
Only `funder_name` is required. **Evidence:** the paper's acknowledgements section, or
the README. Do not guess award numbers.

### `related_dois:` and `related_works:` → `related_works`
Two ways in. `related_dois` is the shorthand:

```yaml
related_dois:
  - "10.1038/s41597-024-00000-0"     # becomes relation_type: References
```

For typed relations, use the full form. Both are merged, deduped by `identifier`, with
the full-form entry winning:

```yaml
related_works:
  - identifier: "10.1038/s41597-024-00000-0"
    identifier_type: "DOI"
    relation_type: "IsSupplementTo"
    description: "Paper this dataset supports"
```

- Common `relation_type` values: `References`, `IsSupplementTo`, `IsDerivedFrom`,
  `IsPartOf`, `Cites`, `IsNewVersionOf`, `IsDocumentedBy`.
- **Every DOI must be Crossref- or DataCite-verified.** Fetch
  `https://api.crossref.org/works/{doi}` and confirm the title matches what you expect.
  A DOI recalled from memory is a fabrication; omit it instead.

### `dataset_doi:`
Only if the dataset already has its own DOI. MDF mints one otherwise. Must start `10.`.

### `external_doi:` / `external_url:` / `external_source:`
Cross-publication provenance — set these only when the dataset already lives in another
repository (Zenodo, Figshare, NOMAD) and this is a mirror.

---

## Scientific context

| Key | → v2 field | Notes |
| --- | --- | --- |
| `facility:` | `facility` | The instrument facility or compute resource: "Advanced Photon Station 11-ID-B", "NERSC Perlmutter". README/paper only. |
| `fields_of_science:` | `fields_of_science` | Broad discipline labels; becomes a DataCite subject scheme. |
| `domains:` | `domains` | MDF domain tags, e.g. `materials science`, `chemistry`. |
| `organization:` | `organization` | The owning MDF organization. Only if the user names one — it is an MDF-side concept, not inferable from the data. |
| `tags:` | `tags` | Free-form platform tags. Use `subjects` for discovery keywords; use `tags` for the user's own labels. |
| `data_type:` | `data_type` | One-word shape of the data: `tabular`, `image`, `spectra`, `trajectory`. |
| `formats:` | `formats` | File formats actually present, from your file walk: `["text/csv", "application/x-hdf5"]` or plain extensions. |
| `geo_locations:` | `geo_locations` | Field/sample sites. Entries: `place`, `point: {latitude, longitude}`, `box`. Only for genuinely geospatial data. |

---

## ML / Foundry datasets

### `ml:` → `ml`
Draft this whenever the data is tabular or otherwise ML-shaped. **It is all-or-nothing:
if you write an `ml:` block at all, `data_format` is required** — the server rejects the
block without it, and `mdf validate` warns you first.

```yaml
ml:
  data_format: "csv"           # REQUIRED whenever ml: is present
  short_name: "perovskite_gaps"
  task_type: ["regression"]
  domain: ["materials science"]
  n_items: 4832
  splits:
    - type: "train"
      path: "data/train.csv"
      n_items: 3865
    - type: "test"
      path: "data/test.csv"
      n_items: 967
  keys:
    - name: "composition"
      role: "input"
      dtype: "str"
      description: "Chemical formula"
    - name: "band_gap"
      role: "target"
      units: "eV"
      dtype: "float"
```

- `keys` entries: `name`, `role` (`input` / `target`), `description`, `units`, `dtype`,
  `shape`, `classes`. **Populate these from actual column sampling** (see
  `evidence-recipes.md`), not from the README's prose description of the columns.
- `units` are worth real effort — grep the README and column headers for them.
- Only declare `splits` if split files or a split column actually exist.

### Do not use `projects.foundry`
The v1 `projects.foundry` block is **silently dropped by the client** — it is excluded
from `extensions` and is not mapped to `ml` anywhere client-side. (The server can migrate
`projects.foundry → ml`, but only for payloads that reach it with that key, which these
do not.) Write `ml:` directly. If you are enriching a legacy record whose metadata came
back with `projects.foundry`, translate it into an `ml:` block by hand.

---

## Everything else

### `custom:` → `extensions`
The escape hatch. Anything that does not fit a schema field goes here and survives into
`extensions`. This is where `drafted_by` lives (see SKILL.md Stage 3).

**Unknown *top-level* keys are silently ignored.** The manifest model accepts them
without error and then drops them on the way to the payload — no warning, no trace. If
you are unsure whether a key is real, either check this document or put it under
`custom:`.

`projects:` is also folded into `extensions` (minus `foundry`, per above).

### `data_sources:`
Paths or globs relative to the manifest directory. If omitted, `mdf publish` scans the
current directory and includes every non-hidden file except `mdf.yaml` and
`.gitignore`. Set it explicitly when the directory holds anything that should not be
published — scratch output, credentials, unrelated work.

### `publisher:` / `publication_year:` / `resource_type:`
`publisher` defaults to "Materials Data Facility"; leave it unless the user is publishing
on behalf of a named repository. `publication_year` is an integer. `resource_type`
defaults to `Dataset`. `publisher` and `publication_year` are correctable after
publication via the metadata-edit path (a minor version bump); `resource_type` is
**not editable** — still get `publication_year` right the first time.

### `update_metadata_only: true`
Metadata-only resubmission — no data upload. Required for enrichment mode (SKILL.md
Stage 7) and it relaxes the `data_sources` requirement. Any update also needs
`custom.mdf_source_id: "<source_id>"` — the server resolves the update target from it.

### `acl:`
Access control. Default is public. Only set this if the user explicitly asks for
restricted access. Do **not** use `dataset_acl:` — it is a legacy client field, not
part of the v2 `DatasetMetadata` schema, and is not equivalent to `acl`.
