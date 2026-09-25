"""`mdf import DOI|zenodo:N`."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.markup import escape

from mdf.cli import theme
from mdf.cli.app import PANEL_PUBLISH, app
from mdf.cli.commands._shared import (
    _STAGING_DOI_NOTICE,
    _abort_on_exception,
    _human_size,
    _targets_staging,
    _watch_submission,
)
from mdf.cli.formatting import (
    EXIT_ERROR,
    EXIT_USAGE,
    api_spinner,
    console,
    emit_json,
    emit_json_result,
    err_console,
    format_authors,
    format_status_badge,
    is_success,
    json_fail,
    require_success,
)
from mdf.cli.options import ApiUrlOpt, DevUserOpt, JsonOpt, ServiceOpt, TokenOpt
from mdf.core.agent import MDFAgent
from mdf.core.config import GlobalConfig, resolve_service


@app.command("import", rich_help_panel=PANEL_PUBLISH)
def import_external(
    identifier: str = typer.Argument(..., help="External identifier (Zenodo record or DOI)"),
    output_dir: Optional[str] = typer.Option(None, "--output", "-o", help="Output directory for downloaded files"),
    dry_run: bool = typer.Option(True, "--dry-run/--submit", help="Preview metadata without downloading"),
    test: bool = typer.Option(False, "--test", help="Submit to test environment"),
    json_output: JsonOpt = False,
    no_watch: bool = typer.Option(False, "--no-watch", help="Do not watch after submit"),
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Import a dataset from an external repository or a DataCite DOI.

    Fetches metadata and data files, then publishes to MDF with
    provenance linking back to the original source.

    Examples:
        mdf import zenodo:12345                    # preview metadata
        mdf import 10.18126/e73h-3w6n              # preview DataCite metadata
        mdf import zenodo:12345 --submit           # download + submit
        mdf import zenodo:12345 -o ./data --submit # custom output dir
    """
    from mdf.importers.registry import resolve_adapter

    adapter = resolve_adapter(identifier)
    if not adapter:
        if json_output:
            json_fail(
                kind="validation",
                message=f"No adapter found for: {identifier}",
                hint="use a DOI, zenodo:ID, or a public Zenodo record URL",
            )
        console.print()
        console.print(theme.error_panel(
            f"No adapter found for: {escape(str(identifier))}",
            title="Unsupported source",
            detail="Supported formats: DOI, doi:DOI, https://doi.org/DOI, zenodo:ID, Zenodo record URL",
            hint="check the identifier, or open an issue to request a new repository adapter",
        ))
        raise typer.Exit(code=EXIT_USAGE)

    # Fetch metadata
    from mdf.importers.base import ExternalRecordNotFoundError

    with api_spinner(f"Fetching metadata from {adapter.name()}…"):
        try:
            metadata = adapter.fetch_metadata(identifier)
        except ExternalRecordNotFoundError as exc:
            _abort_on_exception(
                exc,
                json_output=json_output,
                title="Record not found",
                hint=f"check the identifier; the record must exist and be public on {adapter.name()}",
                kind="not_found",
            )
        except Exception as exc:
            _abort_on_exception(
                exc,
                json_output=json_output,
                title="Failed to fetch metadata",
                hint=f"confirm the record exists and is public on {adapter.name()}",
            )

    # Show metadata panel
    if not json_output:
        table = theme.detail_table(label_width=12)
        table.add_row("Source", theme.ident(adapter.name()))
        table.add_row("Title", escape(str(metadata.get("title", ""))))
        table.add_row("Authors", escape(format_authors(metadata.get("authors"))))
        desc = metadata.get("description", "")
        if len(desc) > 200:
            desc = desc[:200] + "..."
        table.add_row("Description", escape(str(desc)))
        if metadata.get("keywords"):
            table.add_row("Keywords", escape(", ".join(metadata["keywords"])))
        if metadata.get("license"):
            lic = metadata["license"]
            table.add_row("License", escape(lic.get("name", "") if isinstance(lic, dict) else str(lic)))
        if metadata.get("doi"):
            table.add_row("DOI", f"[link=https://doi.org/{metadata['doi']}]{escape(str(metadata['doi']))}[/link]")
        table.add_row("URL", escape(str(metadata.get("url", ""))))
        files = metadata.get("file_urls", [])
        total_size = sum(f.get("size", 0) for f in files)
        table.add_row(
            "Files",
            f"{theme.number(len(files))} files [secondary]({_human_size(total_size)})[/secondary]",
        )

        console.print()
        console.print(theme.panel(
            table,
            title=f"{theme.PACKAGE} {adapter.name()} import",
            subtitle=escape(str(identifier)),
        ))

    if dry_run:
        if json_output:
            emit_json({"success": True, "dry_run": True, "metadata": metadata})
        else:
            if metadata.get("file_urls"):
                console.print()
                file_table = theme.table()
                file_table.add_column("File")
                file_table.add_column("Size", justify="right", style="value")
                for f in metadata["file_urls"][:10]:
                    file_table.add_row(
                        escape(str(f.get("filename", "?"))),
                        _human_size(f["size"]) if f.get("size") else "-",
                    )
                console.print(file_table)
                if len(metadata["file_urls"]) > 10:
                    console.print(f"[secondary]... and {len(metadata['file_urls']) - 10} more[/secondary]")
            console.print()
            console.print(theme.panel(
                f"[id]mdf import {escape(str(identifier))} --submit[/id]",
                title=f"{theme.PACKAGE} Ready to import",
            ))
        return

    # Download + submit
    resolved = resolve_service(service)
    agent = MDFAgent()

    download = theme.TransferProgress(
        f"Downloading from {adapter.name()}",
        done_description="Download complete",
    )

    try:
        with download:
            import_metadata = dict(metadata)
            provenance = import_metadata.get("external_source")
            if isinstance(provenance, dict):
                import_metadata["external_source"] = provenance.get("source") or adapter.name()
                import_metadata.setdefault("doi", provenance.get("doi"))
                import_metadata.setdefault("url", provenance.get("url"))
            result = agent.import_external(
                identifier=identifier,
                output_dir=output_dir,
                metadata=import_metadata,
                progress_callback=download.on_progress,
                on_files_resolved=download.on_files_resolved,
                on_file_done=download.on_file_done,
            )
    except Exception as exc:
        _abort_on_exception(
            exc,
            json_output=json_output,
            title="Import failed",
            hint=f"retry, or download from {adapter.name()} manually and use `mdf publish`",
        )

    if not result.get("success"):
        if json_output:
            json_fail(result)
        console.print()
        console.print(theme.error_panel(
            escape(str(result.get("error", "Unknown error"))),
            title="Import failed",
            hint=f"retry, or download from {adapter.name()} manually and use `mdf publish`",
        ))
        raise typer.Exit(code=EXIT_ERROR)

    # Now publish using the generated manifest (includes upload to MDF storage)
    manifest = result["manifest"]
    manifest.publication_year = metadata.get("publication_year")
    manifest.license = metadata.get("license")
    manifest.related_works = metadata.get("related_works")
    agent_pub = MDFAgent(root=Path(result["output_dir"]), manifest=manifest)

    if not json_output:
        console.print()
        console.print(theme.mark(
            "ok",
            f"Downloaded {result.get('files_downloaded', 0)} files to {escape(str(result['output_dir']))}",
            style="ok",
        ))
        console.print("[secondary]Uploading to MDF and submitting…[/secondary]")

    upload = theme.TransferProgress("Uploading to MDF", done_description="Upload complete")

    try:
        with upload:
            pub_result = agent_pub.publish(
                test=test,
                dry_run=False,
                token=token,
                service_instance=resolved,
                api_url=api_url,
                dev_user_id=dev_user,
                progress_callback=upload.on_progress,
                on_files_resolved=upload.on_files_resolved,
                on_file_done=upload.on_file_done,
            )
    except RuntimeError as exc:
        _abort_on_exception(
            exc,
            json_output=json_output,
            title="Upload failed",
            hint="the download is kept — re-run `mdf publish --submit` from the output directory",
        )

    if is_success(pub_result) and _targets_staging(resolved, api_url):
        # Stderr in both modes, as for `mdf publish --submit` (C10a).
        err_console.print(f"[warn]{_STAGING_DOI_NOTICE}[/warn]", soft_wrap=True)

    if json_output:
        emit_json_result(
            pub_result,
            {"import": result, "publish": pub_result},
            extra={"import": result, "publish": pub_result},
        )
        return

    if pub_result.get("success"):
        source_id_val = pub_result.get("source_id")
        detail = theme.detail_table(label_width=13)
        detail.add_row("Source ID", theme.ident(source_id_val))
        detail.add_row("External DOI", escape(str(metadata.get("doi", "N/A"))))
        detail.add_row("Service", escape(str(resolved)))
        detail.add_row("Status", format_status_badge("pending_curation"))
        console.print()
        console.print(theme.success_panel(
            detail,
            title="Published successfully!",
            emoji=theme.PACKAGE,
            subtitle="awaiting curation",
        ))
        if source_id_val:
            cfg = GlobalConfig()
            cfg.record_publish(source_id_val, pub_result.get("version"), resolved)
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
        require_success(pub_result, error_prefix="Publish failed")
