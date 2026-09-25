# MDF CLI

Python client and CLI for the [MDF Connect v2](https://github.com/materials-data-facility/connect_server) backend. Find, cite and download datasets, publish your own, curate submissions, and search the Materials Data Facility.

## Install

```bash
pipx install mdf-cli

# or
pip install mdf-cli

# With metadata extractors (PDF, CSV, Excel)
pip install "mdf-cli[extractors]"

# With the MCP server for AI assistants
pip install "mdf-cli[mcp]"
```

See [Development](#development) below for an editable/source install.

## Quick start

```bash
# Find and reuse data (no login needed for public datasets)
mdf search "perovskite"
mdf show bennett_origin_metalinsulator_metals --cite
mdf cite bennett_origin_metalinsulator_metals -f bibtex -o ref.bib
mdf clone bennett_origin_metalinsulator_metals --plan

# Publish your own
mdf login                       # sign in with Globus
mdf init ./data                 # create mdf.yaml (asks on a terminal; flags otherwise)
cd ./data
mdf check                       # everything `publish --submit` checks, without uploading
mdf publish --submit            # send it for curation; writes source_id into mdf.yaml

# Follow up
mdf status                      # your last publish
mdf list                        # your datasets
```

The command tree:

```
Get started   login · logout · whoami · init [PATH] · doctor
Find & reuse  search [Q] · show ID [--files] [--cite] · cite ID [-f] [-o] · clone ID [DIR] [--plan] · related ID · versions ID
Publish       check · publish [--submit] [--update [ID]] · status [ID] [--watch] · list [--all] · import DOI|zenodo:N
Manage        dataset edit|withdraw|resubmit|diff|open
More          admin … · config show|get|set|path · manifest discover|inspect · skill … · mcp …
```

## Agent skill

The package bundles a `/mdf-publish` agent skill (folder scan, manifest creation,
validation, publish) that plugs into Claude, Codex, or Gemini as a slash command.

```bash
mdf skill install                    # installs for Claude at ~/.claude/skills/
mdf skill install --agent codex      # or codex / gemini / all
mdf skill install --project          # into ./.claude/skills/ instead
```

Then invoke `/mdf-publish` from the target agent. The skill's instructions live in
`src/mdf/skills/mdf-publish/SKILL.md`.

## AI assistants (MCP)

`mdf mcp serve` runs a [Model Context Protocol](https://modelcontextprotocol.io)
server that exposes MDF to any MCP client — Claude Code, Claude Desktop, Cursor,
or a lab agent — so an assistant can search and cite datasets directly.

```bash
pip install "mdf-cli[mcp]"

# Print the config snippet for your client (it does not edit any file)
mdf mcp install --client claude-code      # or claude-desktop / cursor
mdf mcp install --client claude-code > .mcp.json
```

For Claude Code, the snippet is the contents of `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "mdf": {
      "command": "/path/to/mdf",
      "args": ["mcp", "serve"]
    }
  }
}
```

stdout carries the JSON only, so `mdf mcp install | jq .` works; the target file
path and the client's own one-liner are printed to stderr.

**Tools** (all read-only):

| Tool | What it does |
| --- | --- |
| `search_datasets` | Keyword or browse search with `year` / `organization` / `author` / `keyword` / `domain` filters and `sort`; returns compact records plus the facet values the filters accept |
| `get_dataset_card` | One dataset's full metadata card (prefers the API's compact `format=agent` card) |
| `get_citation` | APA, BibTeX or RIS citation |
| `list_files` | The dataset's file listing (name, size, format) |
| `get_sample` | A small sample of the data |
| `get_versions` | Version history with per-version status and DOI |
| `resolve_doi` | DOI (bare, `doi:`, or `doi.org` URL) → MDF `source_id` |

**Read-only by construction.** Every tool is a GET against the public v2 API
through an anonymous client: the server never logs in, never sees your
credentials, and cannot publish, edit, curate or withdraw anything. Publishing
stays a human action via `mdf publish` (or the `/mdf-publish` skill above).

Service selection follows the CLI: `--service` / `--api-url` on `mdf mcp serve`,
otherwise `MDF_API_URL`, `MDF_SERVICE`, then your `mdf config` default.
`mdf mcp install` adds an `env` block only when you pass `--service` or
`--api-url`; otherwise the server follows the CLI default (so it moves with the
release default instead of staying pinned to today's service).

Tools return compact JSON. Failures come back as
`{"success": false, "error": {"kind", "message", "hint"}}`, the same envelope and vocabulary the CLI
prints, so an assistant can act on them instead of surfacing a traceback.

## CLI commands

### Auth

```bash
mdf login                     # Authenticate via Globus (opens browser); prints who you are
mdf login --service staging   # Authenticate against staging
mdf login --token T --save    # Validate an access token and keep it for later commands
mdf logout                    # Clear cached tokens (and saved tokens)
mdf whoami                    # Who you are, which service, and how you are authenticated
mdf doctor                    # Check service, connectivity, login and the local mdf.yaml
```

A token saved with `--save` is used only for the service (API URL) it was
validated against, and only when there is no cached Globus login. A cached
Globus login is sent only to the known MDF services and to localhost; for any
other `--api-url`, pass `--token` or set `MDF_TRUST_API_URL=1`.

Commands never start a login prompt unless a person can answer it: with
`--json`, or when stdin/stdout is not a terminal, a missing login is an `auth`
error (exit code 4, hint `mdf login`) instead. Login prompts go to stderr.

### Publishing datasets

```bash
# Direct mode (no manifest needed)
mdf publish ./data/ --title "My Dataset" --author "Jane" --submit
mdf publish ./data/ --title "My Dataset" --author "Jane" --dry-run  # Preview payload (default)

# Manifest mode (mdf.yaml in current directory)
mdf init                                             # Create mdf.yaml (asks on a terminal)
mdf init --title "Title" --author "Doe, Jane"        # Create mdf.yaml (non-interactive)
mdf check                                            # Run submit-time checks without submitting
mdf publish --submit                                 # Submit to MDF

# Update an existing dataset (publish a new version)
mdf publish --update --submit                        # ID from mdf.yaml, else your last publish
mdf publish --update my_dataset_id --submit          # Explicit ID (or DOI)
mdf publish --update --title "New" --submit           # Metadata-only update with an override
mdf publish --update my_dataset_id ./new_data/ --submit  # New files as well
```

The first successful `mdf publish --submit` writes the dataset's `source_id`
into `mdf.yaml`; that is how `--update` finds it later (a plain `--submit` on a
published manifest is refused rather than creating a duplicate dataset).
An update starts from the dataset's current metadata — description, keywords,
license, funding, related works — and applies `mdf.yaml`, then
`--title`/`--author`/`--description`, on top (a key set to an empty value in
`mdf.yaml` clears it). Only data paths given on the command line are uploaded;
without them it is a metadata-only update that keeps the previous files.

On the staging service (the public beta), submissions get DataCite **test** DOIs
(prefix `10.23677`) that do not resolve on doi.org; `mdf publish --submit` and
`mdf import --submit` say so on stderr.

### Manifest management

```bash
mdf init                                      # Create mdf.yaml
mdf manifest discover *.csv *.json            # Extract metadata from files into mdf.yaml
mdf manifest inspect                          # Show a readable summary of the current manifest
```

### Datasets

```bash
mdf cite my_dataset_v1                       # Citation (-f apa|bibtex|ris|datacite)
mdf cite my_dataset_v1 -f bibtex -o ref.bib  # Write it to a file (plain text when piped)
mdf versions my_dataset_v1                   # Version history table
mdf dataset open my_dataset_v1               # Open the dataset page in a browser
mdf dataset diff my_dataset_v1 --from 1.0 --to 2.0  # Metadata diff between versions
mdf dataset edit my_dataset_v1 --title "New Title"  # Edit metadata on a submission
mdf dataset withdraw my_dataset_v1           # Withdraw a pending_curation submission
mdf dataset resubmit my_dataset_v1           # Resubmit a rejected submission
```

### Discoverability

```bash
mdf list                            # Your datasets: latest versions, no deleted/withdrawn
mdf list --all                      # Everything, including older versions
mdf list --limit 50                 # More results

mdf show my_dataset_v1              # Formatted dataset card
mdf show my_dataset_v1 --cite       # Include citation
mdf show my_dataset_v1 --files      # Include the file list (--sample: a data sample)
mdf show my_dataset_v1 --json       # Raw JSON output

mdf clone my_dataset_v1 --plan          # What a download would do (reads the card only)
mdf clone my_dataset_v1 --plan --files  # Also list every file (may need a login)
mdf clone my_dataset_v1                 # Download

mdf status                          # Status of last published dataset
mdf status my_dataset_v1            # Status of specific dataset
mdf status my_dataset_v1 --watch    # Poll until terminal state

mdf search "perovskite"             # Keyword search
mdf search "perovskite" --json --brief  # IDs, titles, authors, DOIs
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

### Configuration

```bash
mdf config show                          # Show all settings
mdf config set defaults.service staging  # Set default service
mdf config set user.email me@example.com # Set user email (validated)
mdf config get defaults.service          # Get a value
mdf config path                          # Show config file location
```

### Scripting

Every command that reads or writes data takes `--json`: stdout is then exactly
one JSON document, failures are `{"success": false, "error": {"kind", "code",
"message", "hint"}}`, and the exit code says what happened: `0` ok, `2` usage,
`3` not found, `4` login/permission, `5` network or service.

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
from mdf import MDFAgent

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
```

The SDK may start an interactive Globus login when it has no credentials (it
prompts on stderr). To fail instead, pass `interactive=False` to
`BackendClient.authenticated(...)`, or wrap calls in
`mdf.auth.globus.interactive_login(False)`; a missing login then raises
`mdf.core.exceptions.AuthRequired`.

### BackendClient (low-level API)

```python
from mdf import BackendClient

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
git clone https://github.com/materials-data-facility/mdf-cli
cd mdf_client
pip install -e .

# With metadata extractors (PDF, CSV, Excel)
pip install -e ".[extractors]"
```

## Legacy

The original `mdf_forge` and `mdf_connect_client` code is preserved in `legacy/` for reference.

## Support

This work was performed under financial assistance award 70NANB14H012 from U.S. Department of Commerce, National Institute of Standards and Technology as part of the [Center for Hierarchical Material Design (CHiMaD)](http://chimad.northwestern.edu). This work was performed under the following financial assistance award 70NANB19H005 from U.S. Department of Commerce, National Institute of Standards and Technology as part of the Center for Hierarchical Materials Design (CHiMaD). This work was also supported by the National Science Foundation as part of the [Midwest Big Data Hub](http://midwestbigdatahub.org) under NSF Award Number: 1636950 "BD Spokes: SPOKE: MIDWEST: Collaborative: Integrative Materials Design (IMaD): Leverage, Innovate, and Disseminate".
