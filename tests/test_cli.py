"""Tests for MDF Agent CLI commands."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os

import pytest

from typer.testing import CliRunner

from mdf.cli.main import app

runner = CliRunner()


class TestManifestInitCommand:
    """Tests for 'mdf manifest init' command."""

    def test_manifest_init_creates_yaml(self):
        """mdf manifest init creates mdf.yaml."""
        with TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app,
                ["manifest", "init", tmpdir, "--title", "Test Dataset", "--author", "Jane Doe"],
            )
            assert result.exit_code == 0
            assert "Created mdf.yaml" in result.stdout
            assert (Path(tmpdir) / "mdf.yaml").is_file()
            # No .git directory created
            assert not (Path(tmpdir) / ".git").is_dir()

    def test_manifest_init_with_multiple_authors(self):
        """mdf manifest init with multiple --author options."""
        with TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app,
                [
                    "manifest", "init", tmpdir,
                    "--title", "Test Dataset",
                    "--author", "Jane Doe",
                    "--author", "John Smith",
                ],
            )
            assert result.exit_code == 0

            import yaml
            manifest = yaml.safe_load((Path(tmpdir) / "mdf.yaml").read_text())
            assert len(manifest["authors"]) == 2

    def test_manifest_init_with_all_options(self):
        """mdf manifest init with all options."""
        with TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app,
                [
                    "manifest", "init", tmpdir,
                    "--title", "Test Dataset",
                    "--author", "Jane Doe",
                    "--description", "A test dataset",
                    "--publisher", "Test Publisher",
                    "--year", "2025",
                ],
            )
            assert result.exit_code == 0

            import yaml
            manifest = yaml.safe_load((Path(tmpdir) / "mdf.yaml").read_text())
            assert manifest["title"] == "Test Dataset"
            assert manifest["description"] == "A test dataset"
            assert manifest["publisher"] == "Test Publisher"
            assert manifest["publication_year"] == 2025

    def test_manifest_init_missing_title_fails(self):
        """mdf manifest init without --title fails."""
        with TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app,
                ["manifest", "init", tmpdir, "--author", "Jane Doe"],
            )
            assert result.exit_code != 0

    def test_manifest_init_missing_author_fails(self):
        """mdf manifest init without --author fails."""
        with TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app,
                ["manifest", "init", tmpdir, "--title", "Test"],
            )
            assert result.exit_code != 0

    def test_manifest_init_no_overwrite(self):
        """mdf manifest init refuses to overwrite existing mdf.yaml."""
        with TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "mdf.yaml").write_text("title: Existing\n")
            result = runner.invoke(
                app,
                ["manifest", "init", tmpdir, "--title", "New", "--author", "Jane"],
            )
            assert result.exit_code != 0
            assert "already exists" in result.stdout


class TestValidateCommand:
    """Tests for 'mdf validate' command."""

    def test_validate_valid_manifest(self):
        """mdf validate passes for valid manifest."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data.csv").write_text("a,b,c")

            # Create manifest with data source
            runner.invoke(
                app,
                ["manifest", "init", str(root), "--title", "Test", "--author", "Jane"],
            )

            import yaml
            manifest_path = root / "mdf.yaml"
            manifest = yaml.safe_load(manifest_path.read_text())
            manifest["data_sources"] = ["./data.csv"]
            manifest_path.write_text(yaml.safe_dump(manifest))

            original_cwd = os.getcwd()
            try:
                os.chdir(root)
                result = runner.invoke(app, ["validate"])
                assert result.exit_code == 0
                assert "Validation passed" in result.stdout
            finally:
                os.chdir(original_cwd)


class TestPublishCommand:
    """Tests for 'mdf publish' command."""

    def test_publish_direct_dry_run(self):
        """mdf publish with data args shows dry run payload and next-step hint."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data.csv").write_text("a,b,c")

            result = runner.invoke(
                app,
                ["publish", str(root / "data.csv"), "--title", "Test", "--author", "Jane"],
            )
            assert result.exit_code == 0
            assert "Dry run" in result.stdout
            assert "mdf publish --submit" in result.stdout

    def test_publish_manifest_mode_dry_run(self):
        """mdf publish reads mdf.yaml when no data args provided."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data.csv").write_text("a,b,c")

            runner.invoke(
                app,
                ["manifest", "init", str(root), "--title", "Test", "--author", "Jane"],
            )

            import yaml
            manifest_path = root / "mdf.yaml"
            manifest = yaml.safe_load(manifest_path.read_text())
            manifest["data_sources"] = ["./data.csv"]
            manifest_path.write_text(yaml.safe_dump(manifest))

            original_cwd = os.getcwd()
            try:
                os.chdir(root)
                result = runner.invoke(app, ["publish"])
                assert result.exit_code == 0
                assert "Dry run" in result.stdout
            finally:
                os.chdir(original_cwd)

    def test_publish_no_data_no_manifest_fails(self):
        """mdf publish fails when no data paths and no mdf.yaml."""
        with TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = runner.invoke(app, ["publish"])
                assert result.exit_code != 0
                assert "No data paths" in result.stdout or "no mdf.yaml" in result.stdout
            finally:
                os.chdir(original_cwd)


class TestCloneCommand:
    """Tests for 'mdf clone' command."""

    def test_clone_help(self):
        """mdf clone --help shows download options."""
        result = runner.invoke(app, ["clone", "--help"])
        assert result.exit_code == 0
        assert "--transfer" in result.stdout
        assert "--derive" in result.stdout

    def test_clone_renders_plan_and_success_summary(self, monkeypatch):
        monkeypatch.setattr(
            "mdf.cli.main.MDFAgent.plan_clone",
            lambda self, **kwargs: {
                "success": True,
                "requested_identifier": "src-1",
                "resolved_source_id": "src-1",
                "version": "1.0",
                "title": "Test Dataset",
                "output_path": "/tmp/out",
                "requested_method": "auto",
                "selected_method": "zip",
                "archive_available": True,
                "archive_size": 2048,
                "file_count": None,
            },
        )
        monkeypatch.setattr(
            "mdf.cli.main.MDFAgent.clone",
            lambda self, **kwargs: {
                "success": True,
                "method": "zip",
                "files_count": 3,
                "path": "/tmp/out",
                "source_id": "src-1",
                "resolved_source_id": "src-1",
                "title": "Test Dataset",
            },
        )

        result = runner.invoke(app, ["clone", "src-1"])
        assert result.exit_code == 0
        assert "Clone plan" in result.stdout
        assert "Archive download" in result.stdout
        assert "Clone complete!" in result.stdout

    def test_clone_partial_failure_lists_failed_files(self, monkeypatch):
        monkeypatch.setattr(
            "mdf.cli.main.MDFAgent.plan_clone",
            lambda self, **kwargs: {
                "success": True,
                "requested_identifier": "src-1",
                "resolved_source_id": "src-1",
                "version": "1.0",
                "title": "Test Dataset",
                "output_path": "/tmp/out",
                "requested_method": "auto",
                "selected_method": "https",
                "archive_available": False,
                "archive_size": None,
                "file_count": 2,
            },
        )
        monkeypatch.setattr(
            "mdf.cli.main.MDFAgent.clone",
            lambda self, **kwargs: {
                "success": False,
                "partial": True,
                "method": "https",
                "files_count": 1,
                "failed_count": 1,
                "path": "/tmp/out",
                "errors": ["missing.csv: 404 Not Found"],
                "source_id": "src-1",
                "resolved_source_id": "src-1",
            },
        )

        result = runner.invoke(app, ["clone", "src-1"])
        assert result.exit_code == 1
        assert "Clone partially completed" in result.stdout
        assert "missing.csv" in result.stdout


class TestCLIHelp:
    """Tests for CLI help output."""

    def test_main_help(self):
        """mdf --help shows organized panels with 11 visible items."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Publish & Download" in result.stdout
        assert "Explore" in result.stdout
        assert "Auth & Setup" in result.stdout
        assert "More" in result.stdout
        assert "│ stream" not in result.stdout.lower()

    def test_stream_commands_are_hidden_from_help(self):
        backend_help = runner.invoke(app, ["backend", "--help"])
        stream_help = runner.invoke(app, ["stream", "--help"])

        assert backend_help.exit_code == 0
        assert "stream-create" not in backend_help.stdout
        assert "stream-append" not in backend_help.stdout
        assert "stream-status" not in backend_help.stdout
        assert "stream-close" not in backend_help.stdout
        assert "stream-snapshot" not in backend_help.stdout
        assert stream_help.exit_code == 0
        assert " upload " not in stream_help.stdout
        assert " create " not in stream_help.stdout

    def test_first_run_panel_mentions_setup(self, monkeypatch):
        with TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            result = runner.invoke(app, [])
            assert result.exit_code == 0
            assert "mdf setup" in result.stdout

    def test_manifest_help(self):
        """mdf manifest --help shows subcommands."""
        result = runner.invoke(app, ["manifest", "--help"])
        assert result.exit_code == 0
        assert "init" in result.stdout
        assert "discover" in result.stdout

    def test_publish_help(self):
        """mdf publish --help shows options."""
        result = runner.invoke(app, ["publish", "--help"])
        assert result.exit_code == 0
        assert "--test" in result.stdout or "--dry-run" in result.stdout

    def test_validate_help(self):
        """mdf validate --help shows usage (hidden but functional)."""
        result = runner.invoke(app, ["validate", "--help"])
        assert result.exit_code == 0

    def test_clone_help(self):
        """mdf clone --help shows options."""
        result = runner.invoke(app, ["clone", "--help"])
        assert result.exit_code == 0

    def test_init_command_exists(self):
        """mdf init is a hidden alias that still works."""
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0

    def test_dataset_subapp_help(self):
        """mdf dataset --help shows dataset utility commands."""
        result = runner.invoke(app, ["dataset", "--help"])
        assert result.exit_code == 0
        assert "cite" in result.stdout
        assert "open" in result.stdout
        assert "preview" in result.stdout
        assert "versions" in result.stdout

    def test_admin_subapp_help(self):
        """mdf admin --help shows curation commands."""
        result = runner.invoke(app, ["admin", "--help"])
        assert result.exit_code == 0
        assert "pending" in result.stdout
        assert "approve" in result.stdout
        assert "reject" in result.stdout

    def test_config_doctor_help(self):
        """mdf config doctor --help works."""
        result = runner.invoke(app, ["config", "doctor", "--help"])
        assert result.exit_code == 0

    def test_config_manifest_help(self):
        """mdf config manifest --help works."""
        result = runner.invoke(app, ["config", "manifest", "--help"])
        assert result.exit_code == 0
        assert "init" in result.stdout

    def test_hidden_aliases_work(self):
        """Old top-level commands still work as hidden aliases."""
        for cmd in ["cite", "open", "preview", "versions", "watch", "doctor"]:
            result = runner.invoke(app, [cmd, "--help"])
            assert result.exit_code == 0, f"Hidden alias '{cmd}' should still work"

    def test_no_add_command(self):
        """mdf add is no longer a command."""
        result = runner.invoke(app, ["add", "--help"])
        assert result.exit_code != 0

    def test_no_commit_command(self):
        """mdf commit is no longer a command."""
        result = runner.invoke(app, ["commit", "--help"])
        assert result.exit_code != 0


class TestAuthCommands:
    """Tests for login/logout/whoami commands."""

    def test_login_invokes_auth(self, monkeypatch):
        called = {"method": None, "url": None, "headers": None}

        class FakeResponse:
            status_code = 200
            headers = {}
            text = ""

            def json(self):
                return {"success": True, "user_id": "user-1"}

        def fake_request(self, method, url, **kwargs):
            called["method"] = method
            called["url"] = url
            called["headers"] = kwargs.get("headers")
            return FakeResponse()

        monkeypatch.setattr("httpx.Client.request", fake_request)

        result = runner.invoke(app, ["login", "--service", "dev", "--token", "abc123"])
        assert result.exit_code == 0
        assert "Authentication ready" in result.stdout
        assert called["method"] == "GET"
        assert called["url"] == "https://api-dev.materialsdatafacility.org/auth/check"
        assert called["headers"] == {"Authorization": "Bearer abc123"}

    def test_login_rejects_invalid_token(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("MDF_SERVICE", raising=False)

        class FakeResponse:
            status_code = 401
            headers = {}
            text = ""

            def json(self):
                return {"success": False, "detail": "invalid token"}

        monkeypatch.setattr(
            "httpx.Client.request",
            lambda self, method, url, **kwargs: FakeResponse(),
        )

        result = runner.invoke(app, ["login", "--token", "bad-token"])
        assert result.exit_code == 1
        assert "Token validation failed for staging" in result.stdout
        assert "invalid token" in result.stdout
        assert "Authentication ready" not in result.stdout

    def test_logout_reports_success(self, monkeypatch):
        monkeypatch.setattr("mdf.auth.globus.logout", lambda: True)
        result = runner.invoke(app, ["logout"])
        assert result.exit_code == 0
        assert "Logged out" in result.stdout

    def test_whoami_uses_env_token_status(self, monkeypatch):
        """mdf whoami still works as hidden alias."""
        monkeypatch.setattr("mdf.auth.globus.is_logged_in", lambda service_instance="prod": False)
        monkeypatch.setenv("MDF_CONNECT_TOKEN", "env-token")
        result = runner.invoke(app, ["whoami", "--service", "prod"])
        assert result.exit_code == 0
        assert "authenticated" in result.stdout
        assert "MDF_CONNECT_TOKEN is set in environment" in result.stdout

    def test_status_auth_flag(self, monkeypatch):
        """mdf status --auth shows identity info (absorbs whoami)."""
        monkeypatch.setattr("mdf.auth.globus.is_logged_in", lambda service_instance="prod": False)
        monkeypatch.setenv("MDF_CONNECT_TOKEN", "env-token")
        result = runner.invoke(app, ["status", "--auth", "--service", "prod"])
        assert result.exit_code == 0
        assert "authenticated" in result.stdout
        assert "MDF_CONNECT_TOKEN is set in environment" in result.stdout


class TestSetupAndManifestUX:
    def test_setup_writes_config_without_creating_manifest(self, monkeypatch):
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            dataset = Path(tmpdir) / "dataset"
            dataset.mkdir()
            monkeypatch.setenv("HOME", str(home))

            result = runner.invoke(
                app,
                ["setup", str(dataset)],
                input="staging\nMy Org\nMy Publisher\n\nn\n",
            )

            assert result.exit_code == 0
            config_path = home / ".config" / "mdf" / "config.json"
            assert config_path.exists()
            config = json.loads(config_path.read_text())
            assert config["defaults"]["service"] == "staging"
            assert config["user"]["organization"] == "My Org"
            assert config["user"]["publisher"] == "My Publisher"
            assert not (dataset / "mdf.yaml").exists()

    def test_setup_creates_publishable_manifest_and_prints_real_next_step(self, monkeypatch):
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            dataset = Path(tmpdir) / "dataset"
            dataset.mkdir()
            monkeypatch.setenv("HOME", str(home))

            result = runner.invoke(
                app,
                ["setup", str(dataset)],
                input=(
                    "staging\n"
                    "My Org\n"
                    "My Publisher\n"
                    "me@example.org\n"
                    "y\n"
                    "My Dataset\n"
                    "Jane Doe\n"
                    "A useful dataset\n"
                ),
            )

            assert result.exit_code == 0
            import yaml

            manifest = yaml.safe_load((dataset / "mdf.yaml").read_text())
            assert manifest["title"] == "My Dataset"
            assert manifest["authors"] == ["Jane Doe"]
            assert manifest["description"] == "A useful dataset"
            assert "mdf manifest inspect" in result.stdout
            assert "mdf config manifest inspect" not in result.stdout

    def test_manifest_discover_preview_does_not_mutate_manifest(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data.json").write_text('{"temperature": 300, "phase": "fcc"}')

            init_result = runner.invoke(
                app,
                ["manifest", "init", str(root), "--title", "Test", "--author", "Jane Doe"],
            )
            assert init_result.exit_code == 0
            manifest_path = root / "mdf.yaml"
            before = manifest_path.read_text()

            original_cwd = os.getcwd()
            try:
                os.chdir(root)
                result = runner.invoke(app, ["manifest", "discover", "--preview", "data.json"])
            finally:
                os.chdir(original_cwd)

            assert result.exit_code == 0
            assert "Preview only" in result.stdout
            assert manifest_path.read_text() == before

    def test_manifest_inspect_shows_resolved_sources(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data.csv").write_text("a,b,c\n1,2,3\n")

            runner.invoke(
                app,
                ["manifest", "init", str(root), "--title", "Test", "--author", "Jane Doe"],
            )

            import yaml

            manifest_path = root / "mdf.yaml"
            manifest = yaml.safe_load(manifest_path.read_text())
            manifest["data_sources"] = ["./data.csv"]
            manifest_path.write_text(yaml.safe_dump(manifest))

            result = runner.invoke(app, ["manifest", "inspect", str(root)])
            assert result.exit_code == 0
            assert "Resolved sources" in result.stdout
            assert "./data.csv" in result.stdout


class TestListAndStreamRegressions:
    def test_list_json_applies_filters_and_limit(self, monkeypatch):
        class FakeClient:
            def submissions(self):
                return {
                    "success": True,
                    "total": 4,
                    "submissions": [
                        {
                            "source_id": "keep-first",
                            "status": "published",
                            "dataset_mdata": json.dumps(
                                {"title": "Target material", "latest": True}
                            ),
                        },
                        {
                            "source_id": "keep-second",
                            "status": "published",
                            "dataset_mdata": {
                                "title": "Another target material",
                                "latest": True,
                            },
                        },
                        {
                            "source_id": "wrong-status",
                            "status": "rejected",
                            "dataset_mdata": {
                                "title": "Target material",
                                "latest": True,
                            },
                        },
                        {
                            "source_id": "old-version",
                            "status": "published",
                            "dataset_mdata": {
                                "title": "Target material",
                                "latest": False,
                            },
                        },
                    ],
                }

            def close(self):
                return None

        monkeypatch.setattr(
            "mdf.cli.main.BackendClient.authenticated",
            lambda **kwargs: FakeClient(),
        )

        result = runner.invoke(
            app,
            [
                "list",
                "--json",
                "--status",
                "published",
                "--search",
                "target",
                "--latest-only",
                "--limit",
                "1",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["total"] == 1
        assert [record["source_id"] for record in payload["submissions"]] == [
            "keep-first"
        ]

    def test_stream_disabled_warning_exits_nonzero(self):
        result = runner.invoke(
            app,
            ["stream", "upload", "--stream-id", "stream-1", "missing.csv"],
        )
        assert result.exit_code == 1
        assert "file streaming is disabled in the current MDF release" in result.stdout

    def test_backend_stream_command_is_disabled(self):
        result = runner.invoke(
            app,
            ["backend", "stream-create", "--title", "Lab run"],
        )
        assert result.exit_code == 1
        assert "file streaming is disabled in the current MDF release" in result.stdout

    def test_stream_upload_exits_nonzero_when_file_fails(self, monkeypatch, tmp_path):
        from mdf.cli import stream as stream_cli

        data_file = tmp_path / "data.csv"
        data_file.write_text("a,b\n1,2\n")

        class FakeClient:
            def stream_upload(self, stream_id, filename, content):
                return {"success": False, "error": "upload rejected"}

            def close(self):
                return None

        monkeypatch.setattr(stream_cli, "STREAMING_ENABLED", True)
        monkeypatch.setattr(
            "mdf.cli.stream.BackendClient.authenticated",
            lambda **kwargs: FakeClient(),
        )

        result = runner.invoke(
            app,
            ["stream", "upload", "--stream-id", "stream-1", str(data_file)],
        )

        assert result.exit_code == 1
        assert "upload rejected" in result.stdout


class TestPreflightAndIdentifiers:
    def test_publish_preflight_only_renders_without_submission(self, monkeypatch):
        fake_preflight = {
            "success": True,
            "issues": [],
            "service": "staging",
            "target_url": "https://api.example",
            "source_summary": {"files": 1, "bytes": 3},
            "sources": [{"source": "./data.csv", "kind": "local", "file_count": 1, "total_bytes": 3}],
        }
        monkeypatch.setattr("mdf.cli.main.run_preflight", lambda *args, **kwargs: fake_preflight)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data.csv").write_text("a,b")
            result = runner.invoke(
                app,
                ["publish", str(root / "data.csv"), "--title", "Test", "--author", "Jane", "--preflight-only"],
            )

        assert result.exit_code == 0
        assert "Preflight" in result.stdout
        assert "data.csv" in result.stdout

    def test_status_renders_rejection_feedback(self, monkeypatch):
        class FakeClient:
            def close(self):
                return None

            def status(self, source_id, version=None):
                return {
                    "submission": {
                        "source_id": source_id,
                        "version": "1.0",
                        "status": "rejected",
                        "dataset_mdata": {"title": "Rejected dataset"},
                        "rejection_reason": "Missing methods",
                        "suggestions": "Add the sample preparation details",
                    }
                }

        monkeypatch.setattr("mdf.cli.main.BackendClient.authenticated", lambda **kwargs: FakeClient())
        result = runner.invoke(app, ["status", "src-1"])
        assert result.exit_code == 0
        assert "Missing methods" in result.stdout
        assert "Add the sample preparation details" in result.stdout

    def test_show_resolves_doi_before_loading_card(self, monkeypatch):
        seen = {"card_id": None}

        class FakeClient:
            def close(self):
                return None

            def search(self, query, search_type="datasets", limit=10):
                return {
                    "results": [
                        {
                            "source_id": "src-resolved",
                            "doi": "10.1234/example",
                            "title": "Resolved dataset",
                        }
                    ]
                }

            def get_card(self, source_id, version=None):
                seen["card_id"] = source_id
                return {
                    "success": True,
                    "card": {
                        "source_id": source_id,
                        "version": "1.0",
                        "title": "Resolved dataset",
                        "description": "A dataset",
                    },
                }

        # `show` is a public read and now builds its client via read_client()
        # (no forced interactive login). Patch that seam.
        monkeypatch.setattr("mdf.cli.main.read_client", lambda **kwargs: FakeClient())
        result = runner.invoke(app, ["show", "10.1234/example"])
        assert result.exit_code == 0
        assert seen["card_id"] == "src-resolved"
