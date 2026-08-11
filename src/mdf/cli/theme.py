"""Shared visual language for the ``mdf`` CLI.

Every command renders through this module: one Rich theme, one console pair
(stdout for data, stderr for chrome), and a small vocabulary of glyphs, panels,
tables, spinners, progress bars and syntax blocks. ``mdf`` and its sibling CLIs
are meant to read as one product family, so colors and glyphs carry MEANING —
state, identity, severity — never decoration.

Rules that outrank aesthetics:

* **Machine output is sacred.** Nothing here is emitted under ``--json``/raw
  output, Rich drops color automatically for pipes/non-TTY, and ``NO_COLOR`` is
  honored. Color is never forced.
* **stderr for chrome, stdout for data.** Spinners, progress bars and live
  transfer UI render on :data:`err_console` so stdout stays parseable.
* **Glyph vocabulary:** ``✓`` ok, ``✗`` failed, ``●`` in-progress/attention,
  ``○`` pending/skipped/empty. Emoji are limited to one per panel or headline.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Tuple

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TransferSpeedColumn,
)
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.theme import Theme

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

#: mdf's brand accent. (The sibling foundry CLI uses blue; everything else in
#: the shared contract is identical.)
ACCENT = "magenta"

MDF_THEME = Theme(
    {
        # identity
        "id": "cyan",
        "accent": ACCENT,
        "accent.soft": f"dim {ACCENT}",
        # Rich resolves a markup tag as one whole style name, so every
        # modifier+role combination the CLI needs gets its own key here.
        "accent.bold": f"bold {ACCENT}",
        "brand": f"bold {ACCENT}",
        "ok.bold": "bold green",
        "warn.bold": "bold yellow",
        # severity
        "ok": "green",
        "warn": "yellow",
        "err": "red bold",
        # text roles
        "secondary": "dim",
        "hint": "dim",
        "value": "bright_white",
        "label": "dim",
        # component roles
        "panel.title": f"bold {ACCENT}",
        "panel.subtitle": "dim",
        "table.header": f"bold {ACCENT}",
        "badge.ok": "green",
        "badge.err": "red bold",
        "badge.active": "yellow",
        "badge.idle": "dim",
    }
)

# Glyphs — the whole status vocabulary.
OK = "✓"
FAIL = "✗"
ACTIVE = "●"
IDLE = "○"

# Emoji palette, used at most once per panel/headline and never inside tables
# or machine output.
SCIENCE = "🔬"
PACKAGE = "📦"
SPARKLE = "✨"
SEARCH = "🔍"

_STATE_GLYPH = {
    "ok": (OK, "badge.ok"),
    "err": (FAIL, "badge.err"),
    "active": (ACTIVE, "badge.active"),
    "idle": (IDLE, "badge.idle"),
}

#: Publication / curation / transfer states → glyph class.
STATE_KINDS: Dict[str, str] = {
    "published": "ok",
    "approved": "ok",
    "succeeded": "ok",
    "complete": "ok",
    "ok": "ok",
    "submitted": "active",
    "pending_curation": "active",
    "pending": "active",
    "processing": "active",
    "active": "active",
    "publish_failed": "err",
    "rejected": "err",
    "failed": "err",
    "error": "err",
    "withdrawn": "idle",
    "deleted": "idle",
    "closed": "idle",
    "skipped": "idle",
}


# ---------------------------------------------------------------------------
# Consoles
# ---------------------------------------------------------------------------

_NO_COLOR = bool(os.environ.get("NO_COLOR"))

#: Data channel. Command results render here.
console = Console(theme=MDF_THEME, no_color=_NO_COLOR)

#: Chrome channel. Spinners, progress and live transfer UI render here so a
#: piped stdout stays a clean data stream.
err_console = Console(theme=MDF_THEME, no_color=_NO_COLOR, stderr=True)


def interactive() -> bool:
    """True when it is worth drawing live chrome (spinners, progress bars)."""
    return bool(sys.stderr.isatty()) and not _NO_COLOR


# ---------------------------------------------------------------------------
# Badges and inline marks
# ---------------------------------------------------------------------------


def mark(state: str, text: str = "", *, style: Optional[str] = None) -> str:
    """``"✓ text"`` markup for one of the four glyph states.

    ``state`` is ``ok`` | ``err`` | ``active`` | ``idle``. ``text`` is assumed to
    be caller-escaped when it contains user or server data.
    """
    glyph, glyph_style = _STATE_GLYPH.get(state, _STATE_GLYPH["idle"])
    body_style = style if style is not None else ("err" if state == "err" else None)
    if not text:
        return f"[{glyph_style}]{glyph}[/{glyph_style}]"
    body = f"[{body_style}]{text}[/{body_style}]" if body_style else text
    return f"[{glyph_style}]{glyph}[/{glyph_style}] {body}"


def state_of(status: str) -> str:
    """Glyph class for a submission/transfer status word."""
    return STATE_KINDS.get(str(status or "").strip().lower(), "unknown")


def state_badge(status: str) -> str:
    """``● submitted`` / ``✓ approved`` / ``✗ rejected`` style badge.

    An unrecognized status keeps its word, rendered dim, with no glyph — the CLI
    should never invent a meaning for a state it does not know.
    """
    word = str(status or "")
    kind = state_of(word)
    if kind == "unknown":
        return f"[secondary]{escape(word)}[/secondary]"
    glyph, glyph_style = _STATE_GLYPH[kind]
    return f"[{glyph_style}]{glyph} {escape(word)}[/{glyph_style}]"


def ident(value: Any) -> str:
    """Markup for a dataset/source/stream identifier."""
    return f"[id]{escape(str(value))}[/id]"


def number(value: Any) -> str:
    """Markup for a count, size or other measured value."""
    return f"[value]{escape(str(value))}[/value]"


def human_size(nbytes: float) -> str:
    """Format bytes as a short human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} PB"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def table(
    *,
    show_header: bool = True,
    box_style: Any = box.SIMPLE_HEAD,
    padding: Tuple[int, int] = (0, 1),
    **kwargs: Any,
) -> Table:
    """A result table: simple head rule, accent header, no outer edge."""
    kwargs.setdefault("header_style", "table.header")
    kwargs.setdefault("show_edge", False)
    kwargs.setdefault("pad_edge", False)
    return Table(show_header=show_header, box=box_style, padding=padding, **kwargs)


def detail_table(label_width: int = 15) -> Table:
    """A two-column ``label: value`` block (used inside panels and detail views)."""
    detail = table(show_header=False, box_style=box.SIMPLE, padding=(0, 2))
    detail.add_column("Field", style="label", width=label_width)
    detail.add_column("Value", overflow="fold")
    return detail


# ---------------------------------------------------------------------------
# Panels and rules
# ---------------------------------------------------------------------------


def panel(
    body: RenderableType,
    *,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    border: str = "accent",
    title_style: str = "panel.title",
    padding: Tuple[int, int] = (0, 1),
) -> Panel:
    """A rounded panel: title in accent (left), subtitle dim (right)."""
    return Panel(
        body,
        title=None if title is None else f"[{title_style}]{title}[/{title_style}]",
        title_align="left",
        subtitle=None if subtitle is None else f"[panel.subtitle]{subtitle}[/panel.subtitle]",
        subtitle_align="right",
        border_style=border,
        box=box.ROUNDED,
        padding=padding,
    )


def success_panel(
    body: RenderableType,
    *,
    title: str,
    subtitle: Optional[str] = None,
    emoji: Optional[str] = None,
) -> Panel:
    """A ``✓``-titled panel for completed work."""
    headline = f"{OK} {title}" if emoji is None else f"{OK} {emoji} {title}"
    return panel(body, title=headline, subtitle=subtitle, border="ok", title_style="ok")


def error_panel(
    message: str,
    *,
    title: str = "Error",
    code: Optional[str] = None,
    detail: Optional[str] = None,
    hint: Optional[str] = None,
    border: str = "red",
) -> Panel:
    """A ``✗``-titled panel: CODE in the title, message in the body, ``hint:`` last.

    ``message``/``detail``/``hint`` must already be escaped when they carry
    server-controlled text.
    """
    lines = [message]
    if detail:
        lines.append(f"[secondary]{detail}[/secondary]")
    if hint:
        lines.append(f"[hint]hint: {hint}[/hint]")
    headline = f"{FAIL} {title}" if not code else f"{FAIL} {title} · {code}"
    return panel(
        "\n".join(lines),
        title=headline,
        border=border,
        title_style="err" if border == "red" else "warn",
    )


def empty_panel(
    message: str,
    *,
    title: str = "no results",
    hints: Sequence[str] = (),
) -> Panel:
    """The best-looking output in the CLI: a calm dim panel plus a way forward."""
    lines = [f"[secondary]{message}[/secondary]"]
    for hint in hints:
        lines.append(f"[hint]hint: {hint}[/hint]")
    return panel(
        "\n".join(lines),
        title=f"{IDLE} {title}",
        border="dim",
        title_style="secondary",
    )


def rule(label: Optional[str] = None, *, style: str = "accent.soft") -> Rule:
    """A section rule for walkthrough-style multi-section output."""
    if label is None:
        return Rule(style=style)
    return Rule(f"[accent]{label}[/accent]", style=style, align="left")


# ---------------------------------------------------------------------------
# Syntax highlighting
# ---------------------------------------------------------------------------


def syntax(code: str, lexer: str = "json", *, word_wrap: bool = False) -> Syntax:
    """Highlighted structured text (JSON previews, mdf.yaml snippets, BibTeX).

    ``background_color="default"`` keeps the block transparent so it sits inside
    panels and light terminals without painting a dark slab.
    """
    return Syntax(
        code,
        lexer,
        theme="ansi_dark",
        line_numbers=False,
        word_wrap=word_wrap,
        background_color="default",
    )


def json_syntax(payload: Any) -> Syntax:
    """Pretty, highlighted JSON for human eyes (never for ``--json`` output)."""
    import json as _json

    text = payload if isinstance(payload, str) else _json.dumps(payload, indent=2)
    # Wrap rather than crop: a truncated payload preview hides exactly the long
    # values a human is checking.
    return syntax(text, "json", word_wrap=True)


def clip_snippet(text: str, max_lines: int = 28) -> Tuple[str, int]:
    """Trim a snippet to ``max_lines``; returns ``(snippet, hidden_line_count)``."""
    lines = text.rstrip("\n").split("\n")
    if len(lines) <= max_lines:
        return "\n".join(lines), 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


# ---------------------------------------------------------------------------
# Checklists (preflight, doctor)
# ---------------------------------------------------------------------------


def checklist_table(*, detail_column: bool = True) -> Table:
    """A glyph checklist whose labels, details and wrapped text stay aligned.

    Two shapes: ``glyph | item | detail`` for derived checks (so the details form
    a column), and ``glyph | item`` for free-form findings, where wrapped lines
    and the ``hint:`` line hang under the text instead of the glyph.
    """
    checks = Table(
        show_header=False, box=None, padding=(0, 1), show_edge=False, pad_edge=False
    )
    checks.add_column("state", width=1, no_wrap=True)
    checks.add_column("item", overflow="fold")
    if detail_column:
        checks.add_column("detail", style="secondary", overflow="fold")
    return checks


def add_check(
    checks: Table,
    state: str,
    label: str,
    *,
    detail: Optional[str] = None,
    hint: Optional[str] = None,
) -> None:
    """Append one checklist row. ``label``/``detail``/``hint`` must be escaped."""
    hint_markup = f"[hint]hint: {hint}[/hint]" if hint else None
    if len(checks.columns) >= 3:
        cell = detail or ""
        if hint_markup:
            cell = f"{cell}\n{hint_markup}" if cell else hint_markup
        checks.add_row(mark(state), label, cell)
        return
    body = label if detail is None else f"{label}  [secondary]{detail}[/secondary]"
    if hint_markup:
        body = f"{body}\n{hint_markup}"
    checks.add_row(mark(state), body)


def check_line(
    state: str,
    message: str,
    *,
    detail: Optional[str] = None,
    hint: Optional[str] = None,
    indent: int = 2,
) -> str:
    """One line of a pre-launch checklist, with an optional dim ``hint:`` under it."""
    pad = " " * indent
    text = f"{pad}{mark(state, message)}"
    if detail:
        text += f"  [secondary]{detail}[/secondary]"
    if hint:
        text += f"\n{pad}    [hint]hint: {hint}[/hint]"
    return text


def checklist(lines: Iterable[str]) -> str:
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Spinners
# ---------------------------------------------------------------------------


@contextmanager
def spinner(message: str = "Working…"):
    """Transient ``dots`` spinner on stderr. A no-op when there's no TTY."""
    if interactive():
        with err_console.status(f"[secondary]{message}[/secondary]", spinner="dots"):
            yield
    else:
        yield


# ---------------------------------------------------------------------------
# Transfer progress (uploads, downloads, archive extraction)
# ---------------------------------------------------------------------------


def _default_file_label(rel_path: str) -> str:
    return Path(rel_path).name


class TransferProgress:
    """Two-tier byte-accurate progress for uploads/downloads.

    Top row is the job (spinner, bar, ``m/n`` files, percent); below it one row
    per in-flight file with real size/total and speed, which collapses to a
    ``✓ name`` tick once the file lands.

    Renders on stderr and only when stderr is a TTY; otherwise every callback
    property is ``None`` so callers hand the core no-op callbacks exactly as
    they did before.
    """

    def __init__(
        self,
        description: str,
        *,
        done_description: Optional[str] = None,
        stage_labels: Optional[Dict[str, str]] = None,
        file_label: Callable[[str], str] = _default_file_label,
        enabled: Optional[bool] = None,
    ) -> None:
        self.enabled = interactive() if enabled is None else bool(enabled)
        self._description = description
        self._done_description = done_description or f"{description} complete"
        self._stage_labels = stage_labels or {}
        self._file_label = file_label

        self._overall: Optional[Progress] = None
        self._files: Optional[Progress] = None
        self._live: Optional[Live] = None
        self._overall_task: Optional[int] = None
        self._file_tasks: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._known_total = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "TransferProgress":
        if not self.enabled or self._live is not None:
            return self
        self._overall = Progress(
            SpinnerColumn(style="accent"),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=40, complete_style="accent", finished_style="ok"),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            console=err_console,
        )
        self._files = Progress(
            SpinnerColumn("dots2", style="accent.soft"),
            TextColumn("[secondary]{task.description}[/secondary]"),
            BarColumn(bar_width=None, complete_style="accent", finished_style="ok"),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            console=err_console,
        )
        self._live = Live(
            Group(self._overall, self._files),
            console=err_console,
            refresh_per_second=15,
        )
        self._live.start()
        return self

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def __enter__(self) -> "TransferProgress":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    # -- callbacks ---------------------------------------------------------

    @property
    def on_files_resolved(self) -> Optional[Callable[[int], None]]:
        return self._on_files_resolved if self.enabled else None

    @property
    def on_progress(self) -> Optional[Callable[[str, int, int], None]]:
        return self._on_progress if self.enabled else None

    @property
    def on_file_done(self) -> Optional[Callable[[str, int], None]]:
        return self._on_file_done if self.enabled else None

    @property
    def on_stage(self) -> Optional[Callable[[str, Dict[str, Any]], None]]:
        return self._on_stage if self.enabled else None

    def _on_files_resolved(self, count: int) -> None:
        if self._overall is None:
            return
        self._known_total = count
        total = max(count, 1)
        if self._overall_task is None:
            self._overall_task = self._overall.add_task(
                f"[accent]{self._description}[/accent]", total=total
            )
        else:
            self._overall.update(self._overall_task, total=total, completed=0)

    def _on_progress(self, rel_path: str, bytes_done: int, total_bytes: int) -> None:
        if self._files is None:
            return
        label = self._file_label(rel_path)
        with self._lock:
            if rel_path not in self._file_tasks:
                self._file_tasks[rel_path] = self._files.add_task(
                    label, total=max(total_bytes, 1)
                )
            self._files.update(self._file_tasks[rel_path], completed=bytes_done)

    def _on_file_done(self, rel_path: str, _size: int = 0) -> None:
        if self._files is None:
            return
        label = self._file_label(rel_path)
        with self._lock:
            task_id = self._file_tasks.pop(rel_path, None)
            if task_id is not None:
                self._files.update(task_id, description=f"[ok]{OK} {label}[/ok]")
        if self._overall is not None and self._overall_task is not None:
            self._overall.advance(self._overall_task, 1)
            task = self._overall.tasks[self._overall_task]
            if task.total is not None and task.completed >= task.total:
                self._overall.update(
                    self._overall_task,
                    description=f"[ok]{self._done_description}[/ok]",
                )
        if task_id is not None:
            self._retire_file_row(task_id)

    def _on_stage(self, stage: str, info: Optional[Dict[str, Any]] = None) -> None:
        if self._overall is None:
            return
        info = info or {}
        label = self._stage_labels.get(stage, stage.replace("_", " "))
        total = max(int(info.get("files_count") or self._known_total or 1), 1)
        if self._overall_task is None:
            self._overall_task = self._overall.add_task(label, total=total)
        else:
            self._overall.update(
                self._overall_task, description=label, total=total, completed=0
            )

    def _retire_file_row(self, task_id: int) -> None:
        """Let the ``✓`` tick linger a few frames, then drop the row."""

        def _remove() -> None:
            time.sleep(0.4)
            files = self._files
            if files is None:
                return
            try:
                files.remove_task(task_id)
            except Exception:
                pass

        threading.Thread(target=_remove, daemon=True).start()


def simple_progress(*, transient: bool = True) -> Progress:
    """Single-row byte progress (stream uploads): bar, percent, size, speed."""
    return Progress(
        TextColumn("[secondary]{task.description}[/secondary]"),
        BarColumn(complete_style="accent", finished_style="ok"),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=err_console,
        transient=transient,
    )


# ---------------------------------------------------------------------------
# Typer help styling — the first thing a new user sees.
# ---------------------------------------------------------------------------


def style_typer_help() -> None:
    """Push the theme into Typer's rich help renderer (options, panels, usage)."""
    try:
        import typer.rich_utils as rich_utils
    except Exception:  # pragma: no cover - typer always ships rich_utils
        return

    rich_utils.STYLE_OPTION = "bold cyan"
    rich_utils.STYLE_SWITCH = "bold green"
    rich_utils.STYLE_METAVAR = "dim"
    rich_utils.STYLE_USAGE = ACCENT
    rich_utils.STYLE_USAGE_COMMAND = "bold"
    rich_utils.STYLE_OPTION_DEFAULT = "dim"
    rich_utils.STYLE_OPTIONS_PANEL_BORDER = "dim"
    rich_utils.STYLE_COMMANDS_PANEL_BORDER = "dim"
    rich_utils.STYLE_ERRORS_PANEL_BORDER = "red"
    rich_utils.STYLE_COMMANDS_TABLE_FIRST_COLUMN = f"bold {ACCENT}"
    rich_utils.STYLE_ERRORS_SUGGESTION = "dim"


__all__ = [
    "ACCENT",
    "ACTIVE",
    "FAIL",
    "IDLE",
    "MDF_THEME",
    "OK",
    "PACKAGE",
    "SCIENCE",
    "SEARCH",
    "SPARKLE",
    "TransferProgress",
    "add_check",
    "check_line",
    "checklist",
    "checklist_table",
    "clip_snippet",
    "console",
    "detail_table",
    "empty_panel",
    "err_console",
    "error_panel",
    "human_size",
    "ident",
    "interactive",
    "json_syntax",
    "mark",
    "number",
    "panel",
    "rule",
    "simple_progress",
    "spinner",
    "state_badge",
    "state_of",
    "style_typer_help",
    "success_panel",
    "syntax",
    "table",
]
