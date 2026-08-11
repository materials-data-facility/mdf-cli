"""MDF manifest subcommands.

Provides `mdf manifest init` and `mdf manifest discover` for creating
and enriching mdf.yaml manifest files without git.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape

from mdf.core.agent import MDFAgent
from mdf.core.config import resolve_service
from mdf.cli import theme
from mdf.cli.preflight import suggest_data_sources
from mdf.cli.theme import console


def _summarize_extracted(extracted: dict) -> None:
    """Print a human-readable summary of discovered metadata."""
    mdf = extracted.get("mdf", extracted) if isinstance(extracted, dict) else {}
    schema = mdf.get("table_schema") if isinstance(mdf, dict) else None
    printed = False
    if isinstance(schema, dict):
        cols = schema.get("columns") or []
        fname = schema.get("file", "table")
        rows = schema.get("row_count")
        detail = f"{len(cols)} column(s)"
        if rows is not None:
            detail += f", {rows} row(s)"
        console.print(f"  [label]{escape(str(fname))}:[/label] {theme.number(detail)}")
        if cols:
            names = ", ".join(str(c.get("name", c)) for c in cols[:8])
            more = "" if len(cols) <= 8 else f" (+{len(cols) - 8} more)"
            console.print(f"    [label]columns:[/label] {escape(names)}{more}")
        printed = True
    if not printed:
        for key in extracted:
            console.print(f"  [secondary]{escape(str(key))}[/secondary]")


app = typer.Typer(
    help="Manage mdf.yaml manifest files",
    no_args_is_help=True,
)


@app.command("init")
def manifest_init(
    path: str = typer.Argument(".", help="Directory to create mdf.yaml in"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Dataset title"),
    author: Optional[List[str]] = typer.Option(None, "--author", "-a", help="Author name (repeatable)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Dataset description"),
    publisher: Optional[str] = typer.Option(None, "--publisher", help="Dataset publisher"),
    publication_year: Optional[int] = typer.Option(None, "--year", "-y", help="Publication year"),
    organization: Optional[str] = typer.Option(None, "--organization", "-o", help="Owning organization"),
    keyword: Optional[List[str]] = typer.Option(None, "--keyword", "-k", help="Keyword (repeatable)"),
):
    """Create an mdf.yaml manifest for a dataset directory.

    The manifest stores metadata (title, authors, etc.) so you don't
    have to pass it every time you publish.

    Examples:
        mdf manifest init --title "My Dataset" --author "Jane Doe"
        mdf manifest init ./my_data --title "Test" --author "Jane"
    """
    resolved_title = title
    resolved_authors = list(author) if author else []
    resolved_keywords = list(keyword) if keyword else []

    if not resolved_title or not resolved_authors:
        if sys.stdin.isatty():
            console.print()
            console.print(theme.rule(f"{theme.SCIENCE} Create MDF manifest"))
            console.print()
            if not resolved_title:
                resolved_title = typer.prompt("Dataset title")
            if not resolved_authors:
                console.print("[hint]Enter author names one per line. Empty line to finish.[/hint]")
                while True:
                    name = typer.prompt("Author", default="", show_default=False)
                    if not name:
                        break
                    resolved_authors.append(name)
            if not description:
                description = typer.prompt("Description (optional)", default="", show_default=False) or None
            if not organization:
                organization = typer.prompt("Organization (optional)", default="", show_default=False) or None
            if not resolved_keywords:
                raw_keywords = typer.prompt(
                    "Keywords (comma-separated, optional)",
                    default="",
                    show_default=False,
                )
                if raw_keywords:
                    resolved_keywords = [part.strip() for part in raw_keywords.split(",") if part.strip()]
        else:
            if not resolved_title:
                console.print(theme.error_panel(
                    "--title is required",
                    title="Missing metadata",
                    hint='example: mdf manifest init --title "My Dataset" --author "Jane Doe"',
                ))
                raise typer.Exit(code=1)
            if not resolved_authors:
                console.print(theme.error_panel(
                    "--author is required",
                    title="Missing metadata",
                    hint='example: mdf manifest init --title "My Dataset" --author "Jane Doe"',
                ))
                raise typer.Exit(code=1)

    if not resolved_title:
        console.print(theme.error_panel("Title is required", title="Missing metadata"))
        raise typer.Exit(code=1)
    if not resolved_authors:
        console.print(theme.error_panel(
            "At least one author is required", title="Missing metadata"
        ))
        raise typer.Exit(code=1)

    resolved_path = Path(path).resolve()
    if (resolved_path / "mdf.yaml").exists():
        console.print(theme.error_panel(
            f"mdf.yaml already exists in {escape(str(path))}",
            title="Manifest exists",
            border="yellow",
            hint="edit it directly, or run `mdf manifest inspect` to review it",
        ))
        raise typer.Exit(code=1)

    agent = MDFAgent.init_manifest(
        path=path,
        title=resolved_title,
        authors=resolved_authors,
        description=description,
        publisher=publisher,
        publication_year=publication_year,
    )
    suggested_sources = suggest_data_sources(resolved_path)
    if suggested_sources and sys.stdin.isatty():
        joined = ", ".join(suggested_sources)
        if typer.confirm(f"Use detected data source candidates ({joined})?", default=True):
            agent.manifest.data_sources = suggested_sources
    if organization:
        agent.manifest.organization = organization
    if resolved_keywords:
        agent.manifest.subjects = resolved_keywords
    if agent.manifest.data_sources or organization or resolved_keywords:
        agent.save_manifest()
    detail = theme.detail_table(label_width=13)
    detail.add_row("Path", escape(str(resolved_path / "mdf.yaml")))
    detail.add_row("Title", escape(str(resolved_title)))
    detail.add_row("Authors", escape(", ".join(resolved_authors)))
    if agent.manifest.data_sources:
        detail.add_row(
            "Data sources",
            escape(", ".join(str(item) for item in agent.manifest.data_sources)),
        )
    if organization:
        detail.add_row("Organization", escape(str(organization)))
    if resolved_keywords:
        detail.add_row("Keywords", escape(", ".join(resolved_keywords)))

    console.print()
    console.print(theme.success_panel(
        detail, title="Created mdf.yaml", subtitle=escape(str(path))
    ))
    console.print("[hint]hint: next run `mdf publish --preflight-only`[/hint]")


@app.command("discover")
def manifest_discover(
    paths: List[str] = typer.Argument(..., help="Files or globs to extract metadata from"),
    preview: bool = typer.Option(False, "--preview", help="Show extracted metadata without writing to mdf.yaml"),
):
    """Extract metadata from data files and save to mdf.yaml.

    Scans the provided files for metadata (column names, schemas, etc.)
    and stores the results in mdf.yaml's auto_metadata section.

    Requires an existing mdf.yaml in the current directory.

    Examples:
        mdf manifest discover *.csv
        mdf manifest discover data/*.json data/*.yaml
    """
    from mdf.core.exceptions import NoManifestError

    try:
        agent = MDFAgent.from_manifest(".")
    except NoManifestError:
        console.print(theme.error_panel(
            "No mdf.yaml found in current directory",
            title="No manifest",
            hint="run `mdf manifest init` first",
        ))
        raise typer.Exit(code=1)

    if preview:
        resolved_files: List[str] = []
        for pattern in paths:
            matches = list(agent.root.glob(pattern)) if agent.root else []
            if not matches and agent.root:
                candidate = agent.root / pattern
                if candidate.exists():
                    matches = [candidate]
            for match in matches:
                if match.is_file():
                    resolved_files.append(str(match.resolve()))
        from mdf.extractors.registry import discover_metadata

        extracted = discover_metadata(resolved_files)
    else:
        extracted = agent.discover(*paths)
    if extracted:
        if preview:
            console.print()
            console.print(theme.rule("Preview only - extracted metadata"))
            console.print()
            console.print(theme.json_syntax(extracted))
        else:
            console.print(theme.mark("ok", "Extracted metadata saved to mdf.yaml", style="ok"))
            _summarize_extracted(extracted)
    else:
        console.print()
        console.print(theme.empty_panel(
            "No metadata extracted from the provided files",
            title="nothing extracted",
            hints=[
                "supported: CSV/Excel tables, JSON, YAML, PDF",
                "check the paths — globs are resolved relative to mdf.yaml",
            ],
        ))


@app.command("inspect")
def manifest_inspect(
    path: str = typer.Argument(".", help="Directory containing mdf.yaml"),
):
    """Show a readable summary of the current manifest."""
    from mdf.cli.preflight import run_preflight

    root = Path(path).resolve()
    manifest_path = root / "mdf.yaml"
    if not manifest_path.exists():
        console.print(theme.error_panel(
            f"No mdf.yaml found in {escape(str(root))}",
            title="No manifest",
            hint="run `mdf setup` or `mdf manifest init` in that directory",
        ))
        raise typer.Exit(code=1)

    agent = MDFAgent.from_manifest(str(root))
    manifest = agent.manifest
    preflight = run_preflight(manifest, root=root, service=resolve_service(None), submit=False)

    title = manifest.title[0] if isinstance(manifest.title, list) else manifest.title or "Untitled"
    authors = []
    for author in manifest.authors or []:
        if isinstance(author, str):
            authors.append(author)
        else:
            authors.append(author.name)

    console.print()
    console.print(theme.panel(
        f"[bold]{escape(str(title))}[/bold]\n"
        f"[secondary]{escape(str(manifest.description or 'No description'))}[/secondary]",
        title="mdf.yaml",
        subtitle=escape(str(root.name)),
    ))

    table = theme.detail_table(label_width=14)
    table.add_row("Location", escape(str(manifest_path)))
    table.add_row("Authors", escape(", ".join(authors)) or "None")
    table.add_row("Publisher", escape(str(manifest.publisher or "None")))
    table.add_row("Organization", escape(str(manifest.organization or "None")))
    table.add_row("Keywords", escape(", ".join(manifest.subjects or [])) or "None")
    table.add_row(
        "Data sources",
        theme.number(len(manifest.data_sources))
        if manifest.data_sources
        else "Auto-scan current directory",
    )
    console.print(table)

    if preflight["sources"]:
        console.print("\n[bold]Resolved sources[/bold]\n")
        source_table = theme.table()
        source_table.add_column("Source", style="id")
        source_table.add_column("Kind", style="secondary")
        source_table.add_column("Files", justify="right", style="value")
        source_table.add_column("Size", justify="right", style="value")
        for source in preflight["sources"]:
            files = source.get("file_count")
            bytes_value = source.get("total_bytes")
            source_table.add_row(
                escape(str(source.get("source", ""))),
                escape(str(source.get("kind", ""))),
                "-" if files is None else str(files),
                "-" if bytes_value is None else theme.human_size(bytes_value),
            )
        console.print(source_table)

    # The manifest itself, syntax-highlighted — the fastest way to spot a typo.
    snippet, hidden = theme.clip_snippet(manifest_path.read_text(encoding="utf-8"))
    console.print()
    console.print(theme.panel(theme.syntax(snippet, "yaml"), title="mdf.yaml source"))
    if hidden:
        console.print(f"[secondary]… {hidden} more line(s) in {manifest_path.name}[/secondary]")

    if preflight["issues"]:
        console.print()
        console.print("[bold]Preflight notes[/bold]")
        notes = theme.checklist_table(detail_column=False)
        for issue in preflight["issues"]:
            theme.add_check(
                notes,
                {"blocking": "err", "warning": "active"}.get(issue["severity"], "idle"),
                escape(str(issue["message"])),
                hint=escape(str(issue["hint"])) if issue.get("hint") else None,
            )
        console.print(notes)
