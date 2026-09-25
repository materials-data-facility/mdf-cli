# Changelog

## 0.3.0 — unreleased

### Breaking
- **One command tree, no aliases (clean break).** Nothing of the pre-0.3 surface was on PyPI, so
  these are removed rather than deprecated:

  | Removed | Use instead |
  |---|---|
  | `mdf setup`, `mdf manifest init`, `mdf config manifest init` | `mdf init [PATH]` |
  | `mdf validate`, `mdf publish --preflight-only` | `mdf check` |
  | `mdf status --auth` | `mdf whoami` |
  | `mdf config doctor` | `mdf doctor` |
  | `mdf dataset cite`, `mdf dataset versions` | `mdf cite`, `mdf versions` |
  | `mdf dataset preview [--sample]` | `mdf show ID --files` / `--sample` |
  | `mdf config manifest discover\|inspect` | `mdf manifest discover\|inspect` |
  | `mdf list --latest-only` | the default now; `mdf list --all` shows everything |
  | `mdf search --type` | (datasets only) |
  | hidden top-level aliases: `init`, `validate`, `whoami`, `watch`, `doctor`, `cite`, `open`, `preview`, `versions`, `diff`, `edit`, `withdraw`, `resubmit`, `pending`, `approve`, `reject`, `delete`, `stats`, `update` | `mdf <command>` / `mdf dataset …` / `mdf admin …` / `mdf publish --update` / `mdf status --watch` |
  | hidden `mdf backend …` and `mdf stream …` groups | — |

- **SDK removals:** `mdf.skill` (`handlers`, `mdf_tools`, `skill.md`; the read-only MCP server is
  the agent interface), `MDFAgent.stream_*`, and the `BackendClient.stream_*` methods (including
  `stream_clone`, which sent the API token to any `*materialsdatafacility.org` host).
- `mdf search --json --brief` returns `{"success": true, "total", "offset", "results": [...]}` with
  `source_id` and `version` per hit, instead of a bare list.
- `mdf list` shows only the latest version of each dataset and hides deleted/withdrawn records
  unless `--all` (or `--status`) is given.
- `mdf doctor` exits non-zero when a check fails (`5` service, `4` login, `2` manifest).
- MCP tool errors now carry `"success": false` beside `error`, the same envelope as the CLI; the
  error vocabulary moved to `mdf.core.errors` (still re-exported by `mdf.cli.formatting`).
- **Exit codes**: every failure now exits non-zero, in both `--json` and human mode:
  `1` unknown, `2` usage/validation, `3` not found, `4` auth/forbidden, `5` network/5xx/429.
  Scripts that only checked for `0`/`1` should switch on these values.
- `--json` failures print one envelope: `{"success": false, "error": {"kind", "code", "message", "hint"}}`.
  Success payloads keep their shapes and now always include `"success": true`.
- `mdf admin approve|reject|delete|rebuild-embeddings` under `--json` require `--yes` (no prompts on stdout).
- Stream commands and the five `mdf_stream_*` agent tools are removed from this release (the backend
  router is unmounted); the handlers return a `feature_unavailable` envelope.
- `dataset_doi` in `mdf.yaml` is no longer sent as `external_doi`. Declare `external_source` for
  mirrored datasets; use `related_works` for a DOI of a related record.
- **No login prompts outside a terminal.** A command that needs a login now exits `4` with the
  `auth` envelope (hint `mdf login`) instead of prompting when `--json` is set or stdin/stdout is
  not a TTY. Login prompts and the Globus URL go to stderr, never stdout. Under `--json`, bad
  flags or values (click usage errors) also print the envelope (exit `2`).
- `mdf show --json` emits the API response flat — `{"success", "card", "permissions"[, "citation"]}`
  — instead of nesting it under a second `card` key.
- `mdf publish --update` sends the dataset identity as the top-level `source_id` field, not
  `extensions.mdf_source_id` (deprecated server-side), and starts from the dataset's full current
  metadata. A plain `mdf publish --submit` on an `mdf.yaml` that already records a `source_id` is
  refused (exit 2) instead of creating a duplicate dataset. An update uploads only data paths
  given on the command line (`data_sources` in `mdf.yaml` are ignored for updates).
- `mdf cite -f` rejects unknown formats (exit 2) instead of falling back to APA.
- `mdf init` never asks for or stores `defaults.service`; `mdf mcp install` adds `MDF_SERVICE`
  to the snippet only with `--service`. Without a terminal, `mdf init` fails fast (exit 2) when
  `--title`/`--author` are missing instead of waiting on a prompt.
- `mdf clone --plan` reads the dataset card only; listing every file needs `--files`. Failed or
  timed-out file listings are errors (`mdf.core.agent.CloneListingError`), not warnings, and a
  directory listing without a Globus Transfer login raises `AuthRequired`.
- SDK: core modules no longer `print()`. `mdf.auth.globus.logout()` returns quietly; clone/upload
  warnings go through `logging` (logger `mdf.core.agent`).

### Changed
- `mdf.cli.main` is now only the assembly point: the app and its login policy live in
  `mdf.cli.app`, commands in `mdf.cli.commands.{auth,status,publish,clone,search,list,show,import_}`
  (the `mdf` entry point is unchanged).
- `mdf.core.agent._resolve_globus_files(data_sources, transfer_token)` keeps its signature but gains
  keyword-only `on_progress=` and `timeout=` (default 300 s), and raises instead of warning when a
  listing fails, times out, or has no Transfer token.

### Security
- `mdf clone` sends the Globus data token **only** to the MDF HTTPS host; non-`https` data sources are
  rejected instead of downloaded. Redirects are followed with credentials stripped across hosts.

### Added
- `mdf search --offset --sort --year --organization --author --keyword --domain`; the query is optional
  (browse mode). Facets are printed in human mode.
- `mdf list` pages server-side (`--limit`, `--status`), reports `next_key`, and never skips records.
- `mdf login --no-browser` and `MDF_NO_BROWSER=1`; the login URL is always printed before any browser opens.
- `mdf dataset resubmit --yes`; `mdf resubmit --yes`.
- SDK: `mdf.auth.globus.login_required_for(scopes)`; `MDFAgent.clone` returns all transfer `task_ids`
  and `partial`; anonymous-first clone for public datasets.
- `mdf publish --update [ID]`: the ID defaults to `source_id` in `mdf.yaml`, then the last publish;
  the new version keeps the previous description, keywords, license, funding and related works
  unless you change them; without data paths it is a metadata-only update. The first successful
  publish writes `source_id` into `mdf.yaml`.
- `mdf publish --submit` against staging notes on stderr that staging mints DataCite test DOIs
  (prefix `10.23677`) that do not resolve on doi.org.
- `mdf import --submit` against staging prints the same test-DOI notice.
- `mdf login` and `mdf whoami` show who you are (name and email) and the service;
  `whoami --json` gains `name` and `email`, and reports rejected credentials (401/403) as
  `"credentials rejected"` with exit 4.
- `mdf login --token T --save` keeps a validated token per API URL (owner-only
  `~/.config/mdf/access_tokens.json`, written atomically, never through a symlink). It is sent
  only to the service it was saved for, and a usable cached Globus login takes precedence;
  `mdf logout` removes it.
- `mdf cite -o FILE`; piped or redirected, `cite` prints the citation text only.
- `mdf check [--update [ID]]`: every check `publish --submit` runs, without uploading.
- `mdf whoami`, `mdf doctor`, `mdf init`, `mdf cite`, `mdf versions` as top-level commands;
  `mdf show --files` / `--sample`; `mdf clone --plan --files` with a live file count and
  `--timeout` for the listing.
- `mdf list`: relative dates, one-line titles, a "Next: mdf status <id>" hint.
- Shared CLI options and client lifecycle (`mdf.cli.options`: `ServiceOpt`, `JsonOpt`, …,
  `open_client()`); `--dev-user` is hidden from help.
- SDK: `interactive=` on `BackendClient.authenticated`, `get_authorizer` and
  `get_authorizer_for_scopes`, the `mdf.auth.globus.interactive_login(allowed)` context manager, and
  `mdf.core.exceptions.AuthRequired`. The SDK default is unchanged (it may prompt).

### Fixed
- `mdf publish --update`: explicit empty values in `mdf.yaml` (`subjects: []`, `description:`)
  clear inherited values; `custom` merges per key with inherited extensions; unknown author keys
  (e.g. identifiers) are kept; missing or unreadable previous metadata stops the update (exit 5)
  instead of silently dropping it; `source_id` write-back replaces an empty `source_id:` line and
  writes atomically; a failing history write no longer fails a successful publish.
- Uploads for an update go to a fresh upload location, never into an existing
  `/mdf_open/{source_id}/` directory (the legacy `extensions.mdf_source_id` no longer steers them).
- `mdf import` of a missing Zenodo record or unregistered DOI exits `3` (not found) without raw
  HTTP-library text.
- Search/list/related tables show `—` for missing values and titles on one line.
- `mdf versions` no longer suggests diffing a single version against itself.
- `mdf show` crashed on every real dataset (author objects); authors now render everywhere
  through one helper.
- Tables show full dataset IDs in the first column (`search`, `list`, `related`; `search` drops its
  constant Type/Status columns to make room), and output that is not a terminal has no box drawing.
- `mdf logout` printed its message twice.
- Multi-source `mdf clone --transfer` transferred only the first data source.
- `publish` silently submitted local paths when no data token was available; it now fails with a clear message.
- `http(s)://` data sources were dropped by clone; same-name files from different hosts overwrote each other.
- `mdf validate --json` printed Rich chrome on stdout; `whoami --json` always reported success.
- `--service prod` retry storm on connection timeout.
