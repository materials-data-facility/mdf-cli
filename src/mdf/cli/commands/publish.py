"""`mdf publish [--submit] [--update [ID]]` and `mdf check`."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import typer
from rich.markup import escape

if TYPE_CHECKING:
    from mdf.models.config import ManifestConfig
from mdf.cli import theme
from mdf.cli.app import PANEL_PUBLISH, app
from mdf.cli.commands._shared import (
    _STAGING_DOI_NOTICE,
    _abort_on_exception,
    _fail_usage,
    _parse_submission_mdata,
    _render_preflight,
    _resolve_identifier_with_notice,
    _targets_staging,
    _watch_submission,
)
from mdf.cli.formatting import (
    EXIT_NETWORK,
    EXIT_USAGE,
    api_spinner,
    chrome,
    console,
    emit_json,
    emit_json_result,
    err_console,
    format_status_badge,
    is_success,
    json_fail,
    json_ok,
    require_success,
)
from mdf.cli.options import (
    ApiUrlOpt,
    DevUserOpt,
    JsonOpt,
    ServiceOpt,
    TokenOpt,
    open_client,
)
from mdf.cli.preflight import (
    run_preflight,
)
from mdf.core.agent import MDFAgent
from mdf.core.backend_client import _api_url_for_service
from mdf.core.config import GlobalConfig, resolve_service


def _looks_like_dataset_id(value: str) -> bool:
    """Whether the first positional after ``--update`` names a dataset, not data.

    Anything that exists on disk or carries a URI scheme is data; the rest is
    an ID (or DOI) of the dataset to update.
    """
    return "://" not in value and not Path(value).exists()



@app.command(rich_help_panel=PANEL_PUBLISH)
def publish(
    data: Optional[List[str]] = typer.Argument(
        None, help="Data paths/URIs to publish (with --update, an optional dataset ID first)"
    ),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Dataset title"),
    author: Optional[List[str]] = typer.Option(None, "--author", "-a", help="Author name (repeatable)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Dataset description"),
    dry_run: bool = typer.Option(True, "--dry-run/--submit", help="Preview without submitting"),
    test: bool = typer.Option(False, "--test", help="Submit to test environment"),
    update: bool = typer.Option(
        False,
        "--update",
        "-u",
        help="Publish a new version of an existing dataset: --update [ID] "
        "(default ID: source_id in mdf.yaml, then your last publish)",
    ),
    json_output: JsonOpt = False,
    no_watch: bool = typer.Option(False, "--no-watch", help="Do not watch after a successful submit"),
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Publish a dataset to MDF Connect.

    Three modes:

    1. Direct (data args provided):
        mdf publish ./data/ --title "My Dataset" --author "Jane" --submit

    2. Manifest (mdf.yaml in current directory):
        mdf publish --submit

    3. Manifest + overrides:
        mdf publish --title "New Title" --submit

    The first successful publish writes the new dataset's source_id into
    mdf.yaml. To publish a new version of it later:

        mdf publish --update --submit                  # ID from mdf.yaml / last publish
        mdf publish --update my_dataset_id --submit    # explicit ID (or DOI)
        mdf publish --update my_dataset_id ./new_data/ --submit

    An update starts from the dataset's current metadata (description,
    keywords, license, funding, related works, ...) and applies mdf.yaml, then
    --title/--author/--description, on top; a key set to an empty value in
    mdf.yaml clears it. Only data paths given on the command line are uploaded
    (mdf.yaml's data_sources are ignored for an update); without them it is a
    metadata-only update that keeps the previous files.

    By default, performs a dry run showing the payload.
    Use --submit to actually send to MDF Connect.
    """
    data_args = list(data or [])
    update_id: Optional[str] = None
    if update and data_args and _looks_like_dataset_id(data_args[0]):
        update_id, data_args = data_args[0], data_args[1:]
    _run_publish(
        data=data_args,
        title=title,
        author=author,
        description=description,
        dry_run=dry_run,
        test=test,
        update=update,
        update_id=update_id,
        json_output=json_output,
        preflight_only=False,
        no_watch=no_watch,
        service=service,
        api_url=api_url,
        token=token,
        dev_user=dev_user,
    )



@app.command(rich_help_panel=PANEL_PUBLISH)
def check(
    data: Optional[List[str]] = typer.Argument(
        None, help="Data paths/URIs (with --update, an optional dataset ID first)"
    ),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Dataset title"),
    author: Optional[List[str]] = typer.Option(None, "--author", "-a", help="Author name (repeatable)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Dataset description"),
    update: bool = typer.Option(False, "--update", "-u", help="Check an update: --update [ID]"),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Run every check `mdf publish --submit` runs, without uploading anything.

    Validates the metadata and data paths, confirms you are logged in, and
    that the service is reachable. Takes the same arguments as `mdf publish`.

    Examples:
        mdf check
        mdf check ./data/ --title "My Dataset" --author "Doe, Jane"
        mdf check --update
    """
    data_args = list(data or [])
    update_id: Optional[str] = None
    if update and data_args and _looks_like_dataset_id(data_args[0]):
        update_id, data_args = data_args[0], data_args[1:]
    _run_publish(
        data=data_args,
        title=title,
        author=author,
        description=description,
        dry_run=True,
        test=False,
        update=update,
        update_id=update_id,
        json_output=json_output,
        preflight_only=True,
        no_watch=True,
        service=service,
        api_url=api_url,
        token=token,
        dev_user=dev_user,
    )



def _flag_manifest(
    *, title: Optional[str], author: Optional[List[str]], description: Optional[str]
) -> "ManifestConfig":
    """A manifest holding only the metadata flags actually given (for merging)."""
    from mdf.models.config import ManifestConfig

    given = {"title": title, "authors": author, "description": description}
    return ManifestConfig(**{key: value for key, value in given.items() if value})



def _update_manifest(
    *,
    update_id: Optional[str],
    data: List[str],
    manifest_path: Optional[Path],
    flags: "ManifestConfig",
    json_output: bool,
    resolved: str,
    api_url: Optional[str],
    token: Optional[str],
    dev_user: Optional[str],
) -> "ManifestConfig":
    """Build the manifest for ``mdf publish --update``.

    Layers, later wins: the latest version's full metadata ← mdf.yaml ← flags
    and data paths. Only data paths given on the command line are uploaded;
    ``data_sources`` in mdf.yaml are ignored for an update, and with no data
    paths the update is metadata-only (the new version keeps the old files).
    """
    from mdf.core.manifest import load_manifest, manifest_from_metadata, merge_manifests
    from mdf.models.config import ManifestConfig

    yaml_layer = ManifestConfig()
    if manifest_path is not None:
        loaded = load_manifest(manifest_path)
        yaml_layer = ManifestConfig(
            **{
                key: getattr(loaded, key)
                for key in loaded.model_fields_set
                if key not in ("data_sources", "update_metadata_only")
            }
        )

    # ID: the argument, else mdf.yaml, else the last publish from this machine.
    target_id = update_id or yaml_layer.source_id or GlobalConfig().last_source_id
    if not target_id:
        _fail_usage(
            json_output,
            "No dataset to update: no ID given, no source_id in mdf.yaml, no previous publish.",
            title="Nothing to update",
            hint="pass the ID: `mdf publish --update <source_id> --submit` (find it with `mdf list`)",
            detail=(
                f"'{escape(data[0])}' exists on disk, so it was read as data, not as a dataset ID."
                if data
                else None
            ),
        )
    if not update_id:
        origin = "mdf.yaml" if yaml_layer.source_id else "your last publish"
        chrome(json_output).print(f"[secondary]Updating {origin}:[/secondary] {theme.ident(target_id)}")

    with open_client(public=False, api_url=api_url, token=token, service=resolved, dev_user=dev_user) as client:
        target_id = _resolve_identifier_with_notice(client, target_id, json_mode=json_output)
        with api_spinner("Loading the current version..."):
            previous = client.status(target_id)
    if not is_success(previous):
        if json_output:
            json_fail(previous)
        require_success(previous, error_prefix=f"Could not load {target_id}")

    # Full metadata of the latest version, not the (partial) public card: the
    # card omits funding and related works. No readable metadata means the
    # update cannot inherit anything, so it must not go ahead.
    submission = previous.get("submission")
    raw = submission.get("dataset_mdata") if isinstance(submission, dict) else None
    metadata = _parse_submission_mdata(submission) if isinstance(submission, dict) else {}
    if not metadata:
        problem = "is missing" if raw in (None, "", {}) else "could not be parsed"
        message = f"The current version's metadata {problem}; refusing to publish an update that would drop it."
        if json_output:
            json_fail(kind="server", message=message, hint="retry; if it persists, report it to MDF")
        console.print()
        console.print(theme.error_panel(escape(message), title=f"Could not load {target_id}"))
        raise typer.Exit(code=EXIT_NETWORK)

    manifest = merge_manifests(merge_manifests(manifest_from_metadata(metadata), yaml_layer), flags)
    manifest.source_id = submission.get("source_id") or target_id
    manifest.data_sources = list(data)
    manifest.update_metadata_only = not data
    return manifest



def _run_publish(
    *,
    data: List[str],
    title: Optional[str],
    author: Optional[List[str]],
    description: Optional[str],
    dry_run: bool,
    test: bool,
    update: bool,
    update_id: Optional[str],
    json_output: bool,
    preflight_only: bool,
    no_watch: bool,
    service: Optional[str],
    api_url: Optional[str],
    token: Optional[str],
    dev_user: Optional[str],
) -> None:
    """``mdf publish`` and ``mdf check`` (with ``preflight_only``)."""
    from mdf.core.manifest import write_source_id
    from mdf.models.config import ManifestConfig

    resolved = resolve_service(service)

    manifest_path = Path.cwd() / "mdf.yaml"
    manifest_mode = manifest_path.exists() and (update or not data)

    if update:
        agent = MDFAgent(
            root=None,
            manifest=_update_manifest(
                update_id=update_id,
                data=data,
                manifest_path=manifest_path if manifest_path.exists() else None,
                flags=_flag_manifest(title=title, author=author, description=description),
                json_output=json_output,
                resolved=resolved,
                api_url=api_url,
                token=token,
                dev_user=dev_user,
            ),
        )

    elif data:
        for flag, value in (("--title", title), ("--author", author)):
            if not value:
                _fail_usage(
                    json_output,
                    f"Direct mode requires {flag}",
                    title="Missing metadata",
                    hint='example: mdf publish ./data/ --title "My Dataset" --author "Jane"',
                )
        cfg = GlobalConfig()
        manifest = ManifestConfig(
            title=title,
            authors=author,
            description=description,
            data_sources=list(data),
            publisher=cfg.publisher,
            organization=cfg.organization,
        )
        agent = MDFAgent(root=None, manifest=manifest)

    elif manifest_mode:
        agent = MDFAgent.from_manifest(".")
        if title:
            agent.manifest.title = title
        if author:
            agent.manifest.authors = author
        if description:
            agent.manifest.description = description
        if not agent.manifest.data_sources:
            agent.manifest.data_sources = ["."]

    else:
        _fail_usage(
            json_output,
            "No data paths provided and no mdf.yaml found",
            title="Nothing to publish",
            detail=(
                "Direct mode:    mdf publish ./data/ --title \"My Dataset\" --author \"Jane\" --submit\n"
                "Manifest mode:  mdf init  →  mdf publish --submit"
            ),
            hint="run `mdf init` to create an mdf.yaml in this directory",
        )

    if not update and agent.manifest.source_id:
        # mdf.yaml was published before: a plain publish would create a second
        # dataset (the server gives it a new id). Make that a choice, not an accident.
        known_id = agent.manifest.source_id
        if not dry_run and not preflight_only:
            _fail_usage(
                json_output,
                f"mdf.yaml is already published as {known_id}",
                title="Already published",
                hint=(
                    "add a version with `mdf publish --update --submit`, or delete "
                    "`source_id` from mdf.yaml to publish it as a new dataset"
                ),
            )
        chrome(json_output).print(
            f"[warn]mdf.yaml is already published as[/warn] {theme.ident(known_id)} "
            "[hint]— use --update to add a version[/hint]"
        )

    preflight = run_preflight(
        agent.manifest,
        root=agent.root or Path.cwd(),
        service=resolved,
        api_url=api_url or _api_url_for_service(resolved),
        token=token,
        dev_user=dev_user,
        submit=preflight_only or not dry_run,
    )

    if preflight_only:
        if json_output:
            if not preflight.get("success"):
                json_fail(
                    kind="validation",
                    message="Preflight found blocking issues",
                    hint="fix the reported issues, then re-run `mdf check`",
                    extra={"preflight": preflight},
                )
            # `mdf check` emits the preflight report itself (it carries `success`).
            json_ok(preflight)
            return
        _render_preflight(preflight)
        if not preflight.get("success"):
            raise typer.Exit(code=EXIT_USAGE)
        return

    if not preflight.get("success"):
        if json_output:
            json_fail(
                kind="validation",
                message="Publish blocked by preflight issues",
                hint="fix the reported issues, then re-run `mdf check`",
                extra={"preflight": preflight},
            )
        _render_preflight(preflight)
        console.print()
        console.print(theme.error_panel(
            "Publish blocked by preflight issues",
            title="Blocked",
            hint="fix the ✗ items above, then re-run `mdf check`",
        ))
        raise typer.Exit(code=EXIT_USAGE)

    try:
        payload = agent.build_submission(test=test, update=update)
    except (RuntimeError, ValueError) as exc:
        _abort_on_exception(
            exc,
            json_output=json_output,
            title="Could not build the submission",
            hint="fix the manifest (`mdf check` shows what is wrong), then re-run `mdf publish`",
            kind="validation",
        )

    if dry_run:
        if json_output:
            emit_json({"success": True, "dry_run": True, "payload": payload, "preflight": preflight})
            return
        _render_preflight(preflight, title="Publish preflight")
        target = api_url or _api_url_for_service(resolved)
        console.print()
        console.print(theme.rule("Dry run — would update" if update else "Dry run — would submit"))
        console.print(f"[label]Target:[/label] {escape(str(target))} [secondary]({escape(resolved)})[/secondary]")
        if update:
            console.print(f"[label]Source ID:[/label] {theme.ident(agent.manifest.source_id)}")
        console.print()
        console.print(theme.json_syntax(payload))
        console.print()
        console.print(theme.panel(
            "[id]mdf publish --update --submit[/id]" if update else "[id]mdf publish --submit[/id]",
            title=f"{theme.PACKAGE} Ready to publish",
        ))
        return

    # Two-tier byte-accurate transfer UI (job + per-file), rendered on stderr.
    transfer = theme.TransferProgress("Uploading", done_description="Upload complete")

    try:
        with transfer:
            result = agent.publish(
                test=test,
                update=update,
                dry_run=False,
                token=token,
                service_instance=resolved,
                api_url=api_url,
                dev_user_id=dev_user,
                progress_callback=transfer.on_progress,
                on_files_resolved=transfer.on_files_resolved,
                on_file_done=transfer.on_file_done,
            )
    except RuntimeError as exc:
        _abort_on_exception(
            exc,
            json_output=json_output,
            title="Upload failed",
            hint="check connectivity with `mdf doctor`, then retry `mdf publish --submit`",
        )

    published = is_success(result)
    source_id_val = result.get("source_id") if published else None
    version_val = result.get("version") if published else None
    saved_to_manifest = False
    if source_id_val:
        # mdf.yaml first: it is what `--update` reads. The machine-wide history
        # is a convenience and must never fail a publish that succeeded.
        if manifest_mode:
            try:
                saved_to_manifest = write_source_id(manifest_path, source_id_val)
            except OSError as exc:
                err_console.print(f"[warn]Could not record source_id in mdf.yaml: {escape(str(exc))}[/warn]")
        try:
            GlobalConfig().record_publish(source_id_val, version_val, resolved)
        except OSError as exc:
            err_console.print(f"[warn]Could not record the publish in ~/.config/mdf: {escape(str(exc))}[/warn]")
    # Stderr in both modes, so the --json payload keeps its shape.
    if source_id_val and _targets_staging(resolved, api_url):
        err_console.print(f"[warn]{_STAGING_DOI_NOTICE}[/warn]", soft_wrap=True)

    if json_output:
        emit_json_result(result)
        return

    if published:
        detail = theme.detail_table(label_width=11)
        detail.add_row("Source ID", theme.ident(source_id_val))
        detail.add_row("Service", escape(str(resolved)))
        detail.add_row("Status", format_status_badge("pending_curation"))
        if version_val:
            detail.add_row("Version", theme.ident(version_val))
        console.print()
        console.print(theme.success_panel(
            detail,
            title="Updated successfully!" if update else "Published successfully!",
            emoji=theme.PACKAGE,
            subtitle="awaiting curation",
        ))
        if saved_to_manifest:
            console.print(
                "[hint]Saved source_id to mdf.yaml — `mdf publish --update --submit` "
                "adds a new version.[/hint]"
            )
        if not source_id_val:
            return
        console.print(f"[hint]Try: mdf status {source_id_val}  ·  mdf show {source_id_val}[/hint]")
        if no_watch:
            console.print(f"[hint]Watch: mdf status --watch {source_id_val}[/hint]")
        else:
            raise typer.Exit(
                code=_watch_submission(
                    source_id_val,
                    interval=5,
                    timeout=1800,
                    service=resolved,
                    api_url=api_url,
                    token=token,
                    dev_user=dev_user,
                    announce=True,
                    stop_on_statuses={"pending_curation"},
                )
            )
    else:
        require_success(result, error_prefix="Update failed" if update else "Publish failed")
