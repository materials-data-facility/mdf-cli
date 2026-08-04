# MDF Agent Skill

Handler actions registered in `mdf_tools.py`'s `TOOLS` dict (`src/mdf/skill/handlers.py`
has the implementations). Every action returns `{"success": bool, ...}` and never raises
(`agent_safe` catches exceptions into `{"success": False, "error": ...}`).

Backend-hitting actions accept the same auth/targeting kwargs: `api_url`, `token`,
`service_instance`, `dev_user_id` — omitted below for brevity.

Stream actions (`mdf_stream_*`) call backend endpoints that are currently **disabled** on
the live MDF Connect backend (file streaming is off for the initial v2 release — see
`cli/stream.py`'s `_DISABLED_MESSAGE`). They exist in the tool registry but will error
against staging/prod today.

## Publish flow
- `mdf_scan_folder(path)` — extract metadata from every file under `path` via the extractor registry.
- `mdf_create_manifest(path, title, authors, description=None)` — write a new `mdf.yaml` manifest.
- `mdf_suggest_mappings(headers)` — heuristic column-name → MDF schema field suggestions.
- `mdf_validate_and_preview(path)` — load a manifest, run validation, and build the submission payload without sending it.
- `mdf_publish(path, test=False, update=False, submit=False)` — publish from a manifest at `path`; dry-run unless `submit=True`; `update=True` targets an existing dataset.

## Streams (disabled on live backend — see note above)
- `mdf_stream_create(title, lab_id=None, organization=None)` — open a new stream.
- `mdf_stream_append(stream_id, files=None, file_count=None, total_bytes=None)` — append a batch of files to an open stream.
- `mdf_stream_status(stream_id)` — get a stream's current state.
- `mdf_stream_close(stream_id, mint_doi=None, title=None, description=None, authors=None, keywords=None, license=None)` — finalize a stream into a submission.
- `mdf_stream_snapshot(stream_id, title=None, update=False)` — submit the stream's current contents as a snapshot without closing it.

## Search / discovery
- `mdf_check_status(source_id, version=None)` — submission status for a dataset.
- `mdf_list_submissions(organization=None)` — list submissions, optionally filtered by organization.
- `mdf_search_datasets(query, search_type="all", limit=20)` — keyword search (`search_type`: `all` | `datasets` | `streams`).
- `mdf_get_citation(source_id, format="all", version=None)` — citation text (`format`: `bibtex` | `ris` | `apa` | `datacite` | `all`).
- `mdf_get_card(source_id, version=None)` — formatted dataset preview card.
- `mdf_dataset_preview(source_id)` — file listing / data source preview.
- `mdf_dataset_sample(source_id)` — tabular sample rows for a dataset.
- `mdf_dataset_stats(source_id)` — access/download counts for a published dataset.

## Curation
- `mdf_curation_list_pending(limit=50, offset=0, organization=None)` — datasets awaiting curator review.
- `mdf_curation_review(source_id, version=None)` — full curation detail for one submission.
- `mdf_curation_approve(source_id, mint_doi=True, notes=None, metadata_updates=None, version=None)` — approve for publication, optionally minting a DOI and patching metadata.
- `mdf_curation_reject(source_id, reason, suggestions=None, version=None)` — reject back to the submitter with a reason.

## Admin / lifecycle
- `mdf_edit_metadata(source_id, version=None, title=None, description=None, keywords=None)` — patch metadata fields on an existing submission.
- `mdf_withdraw(source_id, reason="", version=None)` — withdraw a `pending_curation` submission.
- `mdf_resubmit(source_id, notes="", version=None)` — resubmit a rejected submission back into the curation queue.
- `mdf_version_diff(source_id, from_version, to_version)` — metadata diff between two versions.
- `mdf_delete_submission(source_id, reason, version=None)` — soft-delete a submission (curator-only).
- `mdf_admin_stats()` — submission counts by status plus view/download totals (curator-only).
- `mdf_health_check()` — backend health check.
