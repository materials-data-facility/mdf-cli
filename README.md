# MDF Agent

Python client and CLI for the [MDF Connect v2](https://github.com/materials-data-facility/connect_server) backend. Submit datasets, stream data from automated labs, curate submissions, and search the Materials Data Facility.

## Install

```bash
pipx install mdf-cli

# or
pip install mdf-cli

# With metadata extractors (PDF, CSV, Excel)
pip install "mdf-cli[extractors]"
```

See [Development](#development) below for an editable/source install.

## Quick start

```bash
# Authenticate with Globus
mdf login

# Publish a dataset directly (one command)
mdf publish ./data/ --title "My Dataset" --author "Jane Doe" --submit

# Or use a manifest for persistent metadata
mdf setup                       # interactive: configure defaults + create mdf.yaml
vim mdf.yaml                    # edit metadata, add data_sources
mdf publish --submit

# Check status
mdf status

# Browse your datasets
mdf list
mdf show my_dataset_v1
mdf dataset versions my_dataset_v1
```

## Agent skill

The package bundles a `/mdf-publish` agent skill (folder scan, manifest creation,
validation, publish) that plugs into Claude, Codex, or Gemini as a slash command.

```bash
mdf skill install                    # installs for Claude at ~/.claude/skills/
mdf skill install --agent codex      # or codex / gemini / all
mdf skill install --project          # into ./.claude/skills/ instead
```

Then invoke `/mdf-publish` from the target agent. See `src/mdf_agent/skill/skill.md`
for the full list of handler actions it exposes.

## CLI commands

### Auth

```bash
mdf login                     # Authenticate via Globus (opens browser)
mdf login --service staging   # Authenticate against staging
mdf logout                    # Clear cached tokens
mdf status --auth             # Show current auth status
```

### Publishing datasets

```bash
# Direct mode (no manifest needed)
mdf publish ./data/ --title "My Dataset" --author "Jane" --submit
mdf publish ./data/ --title "My Dataset" --author "Jane" --dry-run  # Preview payload (default)

# Manifest mode (mdf.yaml in current directory)
mdf setup                                            # Create mdf.yaml (interactive)
mdf config manifest init --title "Title" --author "Name"  # Create mdf.yaml (non-interactive)
mdf publish --preflight-only                         # Run submit-time checks without submitting
mdf publish --submit                                 # Submit to MDF

# Update an existing dataset
mdf publish --update --submit                        # Updates last published dataset
mdf publish --update --title "New" --submit           # Update with metadata overrides
```

### Manifest management

```bash
mdf setup                                     # Configure defaults + create mdf.yaml (interactive)
mdf config manifest init                      # Create mdf.yaml (interactive)
mdf config manifest init --title "T" --author "A"  # Create mdf.yaml (non-interactive)
mdf config manifest discover *.csv *.json     # Extract metadata from files into mdf.yaml
mdf config manifest inspect                   # Show a readable summary of the current manifest
```

### Dataset utilities

```bash
mdf dataset cite my_dataset_v1               # Citation (APA, BibTeX, RIS, DataCite)
mdf dataset open my_dataset_v1               # Open the dataset page in a browser
mdf dataset preview my_dataset_v1            # File listing / data preview
mdf dataset versions my_dataset_v1           # Version history table
mdf dataset diff my_dataset_v1 --from 1.0 --to 2.0  # Metadata diff between versions
mdf dataset edit my_dataset_v1 --title "New Title"  # Edit metadata on a submission
mdf dataset withdraw my_dataset_v1           # Withdraw a pending_curation submission
mdf dataset resubmit my_dataset_v1           # Resubmit a rejected submission
```

### Discoverability

```bash
mdf list                            # List your submitted datasets
mdf list --limit 50                 # More results

mdf show my_dataset_v1              # Formatted dataset card
mdf show my_dataset_v1 --cite       # Include citation
mdf show my_dataset_v1 --json       # Raw JSON output

mdf status                          # Status of last published dataset
mdf status my_dataset_v1            # Status of specific dataset
mdf status my_dataset_v1 --watch    # Poll until terminal state

mdf search "perovskite"             # Keyword search across datasets and streams
mdf search "XRD" --type streams     # Search only streams
mdf search "battery cathodes" --semantic  # Vector search over title + description embeddings

mdf related my_dataset_v1                   # Datasets sharing authors (ORCID-first)
mdf related my_dataset_v1 --by similar      # Nearest neighbors over the embedding snapshot
mdf related my_dataset_v1 --limit 5
```

### Curation

```bash
mdf admin pending                                          # List datasets awaiting review
mdf admin pending --organization argonne                   # Filter by org
mdf admin approve my_dataset_v1                            # Approve for publication
mdf admin approve my_dataset_v1 --notes "LGTM"              # With curator notes
mdf admin reject my_dataset_v1 --reason "Missing methods"   # Reject with reason
mdf admin delete my_dataset_v1 --reason "spam"              # Soft-delete a submission
mdf admin stats                                             # Admin-wide submission statistics
```

### Importing external datasets

```bash
mdf import zenodo:12345                    # Preview metadata (dry run)
mdf import zenodo:12345 --submit           # Download + submit to MDF
mdf import zenodo:12345 -o ./data --submit # Custom output dir
```

### Streaming (automated labs)

File streaming is disabled for the initial MDF v2 release. Datasets publish by
reference to configured Globus data sources instead.

### Configuration

```bash
mdf config show                          # Show all settings
mdf config set defaults.service staging  # Set default service
mdf config set user.email me@example.com # Set user email (validated)
mdf config get defaults.service          # Get a value
mdf config path                          # Show config file location
mdf config doctor                        # Diagnose config, auth, connectivity, manifest
```

### Shortcuts

A handful of older top-level spellings (`mdf versions`, `mdf pending`, `mdf approve`,
`mdf reject`, `mdf validate`, `mdf whoami`, `mdf update`, `mdf cite`, `mdf watch`, ...)
still work as hidden aliases for backward compatibility, but are not shown in `--help`
and may be removed in a future release. Use the `mdf dataset ...` / `mdf admin ...` /
`mdf status --auth` / `mdf publish --preflight-only|--update` forms documented above.

`mdf backend ...` is also a hidden sub-app exposing low-level backend API calls
(`health`, `status`, `submissions`, `card`, `cite`, `preview`, `search`, ...) with
`--json` output for scripting. It's an advanced/debugging escape hatch, not the
primary interface.

### Service targeting

All commands that talk to the backend accept `--service` to choose the target:

```bash
--service prod      # Production
--service staging   # Staging (default)
--service local     # Local dev server (http://127.0.0.1:8080)
```

Or set `MDF_API_URL` to point to any backend URL.

## Python SDK

### MDFAgent (high-level API)

```python
from mdf_agent import MDFAgent

agent = MDFAgent()

# Search
results = agent.search("perovskite", service_instance="staging")

# Dataset info
card = agent.show("my_dataset_v1", service_instance="staging")
versions = agent.versions("my_dataset_v1", service_instance="staging")
citation = agent.cite("my_dataset_v1", format="bibtex", service_instance="staging")

# Curation
pending = agent.pending(service_instance="staging")
agent.approve("my_dataset_v1", notes="LGTM", service_instance="staging")
agent.reject("my_dataset_v1", reason="Missing methods", service_instance="staging")

# Publishing (manifest mode)
agent = MDFAgent.init_manifest(
    "./my_data",
    title="My Dataset",
    authors=["Jane Doe"],
)
agent.manifest.data_sources = ["./data"]
agent.save_manifest()
result = agent.publish(service_instance="staging", dry_run=False)

# File streaming is disabled for the initial MDF v2 release.
```

### BackendClient (low-level API)

```python
from mdf_agent import BackendClient

client = BackendClient.authenticated(service_instance="staging")

# Submit, status, search
result = client.submit({"title": "My Dataset", "authors": [{"name": "Jane"}], ...})
status = client.status(result["source_id"])
results = client.search("iron oxide")

# Versions and citations
versions = client.versions("my_dataset_v1")
citation = client.get_citation("my_dataset_v1", format="bibtex")

client.close()
```

### Programmatic / CI authentication

For scripts, notebooks, and CI pipelines you can skip interactive browser login by
setting environment variables. The recommended approach is to register a **confidential
client** at [developers.globus.org](https://developers.globus.org) and export the
credentials:

```bash
export MDF_CLIENT_ID="your-client-uuid"
export MDF_CLIENT_SECRET="your-client-secret"
mdf publish --submit          # no browser required
```

Alternatively, pass a pre-existing access token via `MDF_CONNECT_TOKEN`.

### Auth resolution

`BackendClient.authenticated()` resolves credentials in this order:

1. Explicit `token` parameter
2. `MDF_CONNECT_TOKEN` environment variable
3. `MDF_CLIENT_ID` + `MDF_CLIENT_SECRET` (confidential client credentials)
4. `MDF_DEV_USER_ID` (dev mode, no real auth)
5. Interactive Globus OAuth login (opens browser, caches tokens)

### Error handling

All HTTP requests automatically retry on transient errors:
- **429** (rate limited): respects `Retry-After` header
- **502, 503, 504** (server errors): exponential backoff
- **Connection errors**: 3 retries with backoff, except the unavailable production
  host fails fast with guidance to use staging

File uploads (`_https_put_file`) also retry on 502/503/504 and connection errors. SSL verification for the Globus HTTPS endpoint is configurable via `MDF_SSL_VERIFY` (default: `true`).

## Semantic search and embeddings

MDF generates OpenAI `text-embedding-3-small` vectors (1536-dim) over each dataset's title + description and stores them in DynamoDB. A periodic S3 snapshot (`Float32Array` binary + JSON sidecar) feeds both the backend `/search/semantic` endpoint and any frontend that wants to scan client-side. There's also a lightweight in-memory author index that powers `mdf related` — no embeddings required, ORCID matched first.

### Usage

```bash
mdf search "perovskite photovoltaic stability" --semantic
mdf related my_dataset_v1                  # Co-author lookup
mdf related my_dataset_v1 --by similar     # Embedding nearest-neighbors
```

The `--by similar` path serves the dataset detail page's "you might also like" widget. It does no OpenAI call — the dataset's own vector is already in the cached snapshot, so it's a single cosine pass over the in-memory matrix. Frontends can also call `GET /datasets/{source_id}/related?by=similar&limit=5` directly.

### Automatic on publish

When a dataset is approved and published, the publish pipeline fires off a `generate_embedding` async job for the new version. If the embed call fails (OpenAI outage, quota), publish still succeeds — the next `rebuild-embeddings` will pick up the gap.

Editing metadata via `mdf dataset edit` or a curator approve with `metadata_updates` bumps `metadata_updated_at`, which makes the skip check treat the existing embedding as stale on the next rebuild.

### Manual rebuild (curator-only)

```bash
# Check coverage, see current snapshot, spot any stale records
mdf admin embedding-status --service staging

# Dispatch a rebuild — returns immediately, work runs in the async worker
mdf admin rebuild-embeddings --service staging --yes

# Watch progress
mdf admin embedding-status --service staging
```

What the rebuild does:

1. Enqueues **one** dispatcher job (SQS) and returns — the endpoint never blocks on the scan.
2. Async worker scans DynamoDB, skips records whose embedding already matches the current model and isn't stale, and fans out one `generate_embedding` job per pending record.
3. Final `build_embedding_snapshot` job packs every vector into `s3://mdf-embeddings-<env>/embeddings/v1/index-<sha>.bin` + `.json`, then atomically swaps `current.json` to point at it. Content-hashed filenames mean browsers and the Lambda in-process cache can keep aggressive TTLs.

Flags:

```bash
--force         # Re-embed every published dataset (use after a model switch)
--limit N       # Cap OpenAI calls per run — good for phased backfills
--no-snapshot   # Fill Dynamo only, skip the S3 publish
```

### Staleness detection

Each embedded record carries `embedding_generated_at`; each metadata write stamps `metadata_updated_at`. `rebuild-embeddings` treats an embedding as stale whenever `embedding_generated_at < metadata_updated_at` (plus whenever `embedding_model` no longer matches `EMBEDDING_MODEL`). You do not need `--force` for normal edits — stale records are picked up automatically.

### First-time deploy

Backend deployment, including the embedding pipeline and its OpenAI/S3
configuration, is maintained in the sibling
[`connect_server`](https://github.com/materials-data-facility/connect_server)
repository. Follow that repository's deployment instructions, then backfill
existing datasets with `mdf admin rebuild-embeddings --service staging --yes`.

Switching models later: update `EmbeddingModel` (and `EmbeddingDims` if changing size) in the SAM template, redeploy, then `mdf admin rebuild-embeddings` — records with the old model stamp get re-embedded automatically; same-model ones are skipped.

### Frontend integration

The snapshot bucket has CORS open for browser reads. If you front it with CloudFront, set `EmbeddingSnapshotPublicUrl=https://<distribution>` at deploy time; `POST /admin/embeddings/rebuild` then returns `public_urls.{bin,json,pointer}` in its response so the UI can fetch the blob directly. Query embedding is done server-side via `POST /embed` so the OpenAI key never ships to the browser.

## Connecting to the backend

| Environment | API URL |
|-------------|---------|
| dev | See the sibling `connect_server` repository |
| staging | `https://3xicgt0g7l.execute-api.us-east-1.amazonaws.com/staging` |
| prod | `https://api.materialsdatafacility.org` (available after production cutover) |
| local | `http://127.0.0.1:8080` |

Deployment commands and backend environment configuration live in the sibling
[`connect_server`](https://github.com/materials-data-facility/connect_server)
repository.

## Running tests

```bash
# Client tests
python -m pytest tests/ -v

# Backend tests are run from the sibling connect_server repository.
```

## Development

```bash
git clone https://github.com/materials-data-facility/mdf_client
cd mdf_client
pip install -e .

# With metadata extractors (PDF, CSV, Excel)
pip install -e ".[extractors]"
```

## Legacy

The original `mdf_forge` and `mdf_connect_client` code is preserved in `legacy/` for reference.

## Support

This work was performed under financial assistance award 70NANB14H012 from U.S. Department of Commerce, National Institute of Standards and Technology as part of the [Center for Hierarchical Material Design (CHiMaD)](http://chimad.northwestern.edu). This work was performed under the following financial assistance award 70NANB19H005 from U.S. Department of Commerce, National Institute of Standards and Technology as part of the Center for Hierarchical Materials Design (CHiMaD). This work was also supported by the National Science Foundation as part of the [Midwest Big Data Hub](http://midwestbigdatahub.org) under NSF Award Number: 1636950 "BD Spokes: SPOKE: MIDWEST: Collaborative: Integrative Materials Design (IMaD): Leverage, Innovate, and Disseminate".
