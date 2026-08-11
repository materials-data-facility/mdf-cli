"""Tests for the shared CLI visual language (mdf.cli.theme + formatting).

These lock the *contract*, not the decoration: status words survive badging,
failures render a code + message + recovery hint, empty states offer a way
forward, and machine output stays byte-plain.
"""

from __future__ import annotations

import json

from rich.console import Console
from typer.testing import CliRunner

from mdf.cli import theme
from mdf.cli.formatting import classify_api_error, handle_api_result
from mdf.cli.main import app
from mdf.core.backend_client import ApiResult

runner = CliRunner()


def render(renderable, width: int = 90) -> str:
    """Render a Rich renderable/markup to plain text (no color, fixed width)."""
    console = Console(width=width, no_color=True, theme=theme.MDF_THEME)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


class TestStatusBadges:
    def test_badge_keeps_status_word(self):
        for status in ("published", "pending_curation", "rejected", "withdrawn"):
            assert status in render(theme.state_badge(status))

    def test_badge_glyph_matches_state_class(self):
        assert theme.OK in render(theme.state_badge("published"))
        assert theme.OK in render(theme.state_badge("approved"))
        assert theme.ACTIVE in render(theme.state_badge("pending_curation"))
        assert theme.FAIL in render(theme.state_badge("rejected"))
        assert theme.FAIL in render(theme.state_badge("publish_failed"))
        assert theme.IDLE in render(theme.state_badge("withdrawn"))

    def test_unknown_status_keeps_word_without_inventing_a_glyph(self):
        out = render(theme.state_badge("brand_new_state"))
        assert "brand_new_state" in out
        for glyph in (theme.OK, theme.FAIL, theme.ACTIVE, theme.IDLE):
            assert glyph not in out

    def test_badge_escapes_markup_in_server_text(self):
        assert "[red]evil[/red]" in render(theme.state_badge("[red]evil[/red]"))


class TestPanels:
    def test_error_panel_has_glyph_code_message_and_hint(self):
        out = render(theme.error_panel(
            "Permission denied", title="Show failed", code="HTTP 403", hint="ask a curator"
        ))
        assert theme.FAIL in out
        assert "Show failed" in out
        assert "HTTP 403" in out
        assert "Permission denied" in out
        assert "hint: ask a curator" in out

    def test_success_panel_is_check_titled(self):
        out = render(theme.success_panel("body", title="Published successfully!"))
        assert f"{theme.OK}" in out
        assert "Published successfully!" in out

    def test_empty_panel_offers_a_way_forward(self):
        out = render(theme.empty_panel("No results found for 'x'", hints=["try --semantic"]))
        assert "No results found for 'x'" in out
        assert "hint: try --semantic" in out
        assert theme.IDLE in out


class TestErrorClassification:
    def test_status_code_drives_message_and_hint(self):
        info = classify_api_error(ApiResult({"detail": "nope"}, status_code=401))
        assert info["message"] == "Authentication required"
        assert "mdf login" in info["hint"]
        assert info["code"] == "HTTP 401"

    def test_connection_failure_gets_a_network_hint(self):
        info = classify_api_error(
            {"success": False, "error": "Connection failed after 4 attempts: refused"}
        )
        assert info["kind"] == "network"
        assert "doctor" in info["hint"]
        # The raw reason is preserved, not replaced by a generic phrase.
        assert "Connection failed" in info["message"]

    def test_unknown_failure_still_suggests_something(self):
        info = classify_api_error({"success": False, "error": "kaboom"})
        assert info["message"] == "kaboom"
        assert info["hint"]

    def test_handle_api_result_prints_hint_for_failures(self, capsys):
        assert handle_api_result(
            ApiResult({"detail": "x"}, status_code=403), error_prefix="Show failed"
        ) is False
        out = capsys.readouterr().out
        assert "Permission denied" in out
        assert "hint:" in out


class TestChecklists:
    def test_check_line_renders_glyph_message_and_hint(self):
        out = render(theme.check_line("err", "Data source missing", hint="fix mdf.yaml"))
        assert theme.FAIL in out
        assert "Data source missing" in out
        assert "hint: fix mdf.yaml" in out

    def test_checklist_table_keeps_details_in_their_own_column(self):
        checks = theme.checklist_table()
        theme.add_check(checks, "ok", "Manifest checks", detail="all good")
        theme.add_check(checks, "idle", "Data staged", detail="no files resolved yet")
        out = render(checks)
        assert theme.OK in out and theme.IDLE in out
        columns = [line.index("all good") for line in out.splitlines() if "all good" in line]
        details = [line.index("no files") for line in out.splitlines() if "no files" in line]
        assert columns and details and columns[0] == details[0]


class TestSyntaxAndProgress:
    def test_json_preview_is_highlighted_not_cropped(self):
        payload = {"description": "x" * 200}
        out = render(theme.json_syntax(payload))
        assert "description" in out
        # word_wrap keeps the whole value visible across lines
        assert out.count("x") > 150

    def test_transfer_progress_is_inert_without_a_tty(self):
        transfer = theme.TransferProgress("Uploading", enabled=False)
        with transfer:
            assert transfer.on_progress is None
            assert transfer.on_files_resolved is None
            assert transfer.on_file_done is None
            assert transfer.on_stage is None


class TestMachineOutputStaysPlain:
    def test_search_json_output_has_no_styling(self, monkeypatch):
        class FakeClient:
            def close(self):
                return None

            def search(self, query, search_type="all", limit=20):
                return ApiResult(
                    {"query": query, "total": 1, "results": [{"type": "dataset", "title": "T"}]},
                    status_code=200,
                )

        monkeypatch.setattr("mdf.cli.main.read_client", lambda **kwargs: FakeClient())
        result = runner.invoke(app, ["search", "perovskite", "--json"])
        assert result.exit_code == 0
        assert "\x1b[" not in result.stdout
        assert json.loads(result.stdout)["total"] == 1

    def test_search_empty_state_is_a_panel_with_hints(self, monkeypatch):
        class FakeClient:
            def close(self):
                return None

            def search(self, query, search_type="all", limit=20):
                return ApiResult({"query": query, "total": 0, "results": []}, status_code=200)

        monkeypatch.setattr("mdf.cli.main.read_client", lambda **kwargs: FakeClient())
        result = runner.invoke(app, ["search", "unobtainium"])
        assert result.exit_code == 0
        assert "No results found for 'unobtainium'" in result.stdout
        assert "hint:" in result.stdout

    def test_search_network_failure_is_not_reported_as_empty(self, monkeypatch):
        class FakeClient:
            def close(self):
                return None

            def search(self, query, search_type="all", limit=20):
                return {"success": False, "error": "Connection failed after 4 attempts: refused"}

        monkeypatch.setattr("mdf.cli.main.read_client", lambda **kwargs: FakeClient())
        result = runner.invoke(app, ["search", "perovskite"])
        assert "Search failed" in result.stdout
        assert "No results found" not in result.stdout


class TestPublishFlowRendering:
    """The submit path renders a status-badged success panel (and no tracebacks)."""

    def test_publish_submit_renders_success_panel(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "data.csv").write_text("a,b\n1,2\n")

        monkeypatch.setattr(
            "mdf.cli.main.run_preflight",
            lambda *args, **kwargs: {
                "success": True,
                "issues": [],
                "service": "staging",
                "target_url": "https://api.example",
                "source_summary": {"count": 1, "files": 1, "bytes": 8},
                "sources": [
                    {"source": "./data.csv", "kind": "local", "file_count": 1, "total_bytes": 8}
                ],
            },
        )

        def fake_publish(self, **kwargs):
            # The CLI hands real callbacks only when stderr is a TTY; both shapes
            # must work.
            for name in ("on_files_resolved", "progress_callback", "on_file_done"):
                assert name in kwargs
            resolved = kwargs.get("on_files_resolved")
            if resolved:
                resolved(1)
            return {"success": True, "source_id": "src-1", "version": "1.0"}

        monkeypatch.setattr("mdf.cli.main.MDFAgent.publish", fake_publish)

        result = runner.invoke(
            app,
            [
                "publish",
                str(tmp_path / "data.csv"),
                "--title",
                "Test",
                "--author",
                "Jane",
                "--submit",
                "--no-watch",
            ],
        )
        assert result.exit_code == 0
        assert "Published successfully!" in result.stdout
        assert "pending_curation" in result.stdout
        assert "src-1" in result.stdout

    def test_publish_upload_failure_renders_error_panel(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "data.csv").write_text("a,b\n1,2\n")

        monkeypatch.setattr(
            "mdf.cli.main.run_preflight",
            lambda *args, **kwargs: {
                "success": True,
                "issues": [],
                "service": "staging",
                "target_url": "https://api.example",
                "source_summary": {"count": 1, "files": 1, "bytes": 8},
                "sources": [],
            },
        )

        def boom(self, **kwargs):
            raise RuntimeError("upload aborted at 40%")

        monkeypatch.setattr("mdf.cli.main.MDFAgent.publish", boom)

        result = runner.invoke(
            app,
            [
                "publish",
                str(tmp_path / "data.csv"),
                "--title",
                "Test",
                "--author",
                "Jane",
                "--submit",
            ],
        )
        assert result.exit_code == 1
        assert "Upload failed" in result.stdout
        assert "upload aborted at 40%" in result.stdout
        assert "hint:" in result.stdout
