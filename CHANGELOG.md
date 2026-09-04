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

### Fixed
- Multi-source `mdf clone --transfer` transferred only the first data source.
- `publish` silently submitted local paths when no data token was available; it now fails with a clear message.
- `http(s)://` data sources were dropped by clone; same-name files from different hosts overwrote each other.
- `mdf validate --json` printed Rich chrome on stdout; `whoami --json` always reported success.
- `--service prod` retry storm on connection timeout.
