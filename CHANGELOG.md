# Changelog

## 0.3.0 — unreleased

### Breaking
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
  not a TTY. Login prompts and the Globus URL go to stderr, never stdout.
- `mdf show --json` emits the API response flat — `{"success", "card", "permissions"[, "citation"]}`
  — instead of nesting it under a second `card` key.
- `mdf publish --update` sends the dataset identity as the top-level `source_id` field, not
  `extensions.mdf_source_id` (deprecated server-side), and starts from the dataset's full current
  metadata. A plain `mdf publish --submit` on an `mdf.yaml` that already records a `source_id` is
  refused (exit 2) instead of creating a duplicate dataset.
- `mdf dataset cite -f` rejects unknown formats (exit 2) instead of falling back to APA.
- `mdf setup` no longer asks for or stores `defaults.service`; `mdf mcp install` adds
  `MDF_SERVICE` to the snippet only with `--service`.
- SDK: core modules no longer `print()`. `mdf.auth.globus.logout()` returns quietly; clone/upload
  warnings go through `logging` (logger `mdf.core.agent`).

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
- `mdf login` and `mdf status --auth` show who you are (name and email) and the service;
  `status --auth --json` gains `name` and `email`.
- `mdf login --token T --save` keeps a validated token (owner-only file
  `~/.config/mdf/access_token`), used after env credentials and before the cached Globus login;
  `mdf logout` removes it.
- `mdf dataset cite -o FILE`; piped or redirected, `cite` prints the citation text only.
- SDK: `interactive=` on `BackendClient.authenticated`, `get_authorizer` and
  `get_authorizer_for_scopes`, the `mdf.auth.globus.interactive_login(allowed)` context manager, and
  `mdf.core.exceptions.AuthRequired`. The SDK default is unchanged (it may prompt).

### Fixed
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
