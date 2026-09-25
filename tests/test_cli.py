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
                ["init", tmpdir, "--title", "Test Dataset", "--author", "Jane Doe"],
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
                    "init", tmpdir,
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
                    "init", tmpdir,
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
                ["init", tmpdir, "--author", "Jane Doe"],
            )
            assert result.exit_code != 0

    def test_manifest_init_missing_author_fails(self):
        """mdf manifest init without --author fails."""
        with TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app,
                ["init", tmpdir, "--title", "Test"],
            )
            assert result.exit_code != 0

    def test_manifest_init_no_overwrite(self):
        """mdf manifest init refuses to overwrite existing mdf.yaml."""
        with TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "mdf.yaml").write_text("title: Existing\n")
            result = runner.invoke(
                app,
                ["init", tmpdir, "--title", "New", "--author", "Jane"],
            )
            assert result.exit_code != 0
            assert "already exists" in result.stdout


class TestCheckCommand:
    """Tests for 'mdf check' (was 'mdf validate' / 'publish --preflight-only')."""

    def test_check_valid_manifest(self, monkeypatch):
        """mdf check runs the submit-time checks on the manifest in cwd."""
        seen = {}

        def fake_preflight(manifest, **kwargs):
            seen["title"] = manifest.title
            seen["submit"] = kwargs.get("submit")
            return {"success": True, "issues": [], "source_summary": {}}

        monkeypatch.setattr("mdf.cli.commands.publish.run_preflight", fake_preflight)
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data.csv").write_text("a,b,c")
            runner.invoke(app, ["init", str(root), "--title", "Test", "--author", "Jane"])
            monkeypatch.chdir(root)
            result = runner.invoke(app, ["check"])
            assert result.exit_code == 0, result.output
            assert seen == {"title": "Test", "submit": True}


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
                ["init", str(root), "--title", "Test", "--author", "Jane"],
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
            "mdf.core.agent.MDFAgent.plan_clone",
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
            "mdf.core.agent.MDFAgent.clone",
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
            "mdf.core.agent.MDFAgent.plan_clone",
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
            "mdf.core.agent.MDFAgent.clone",
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
    """Tests for CLI help output (the target command tree, plan Lane C)."""

    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for panel in ("Get started", "Find & reuse", "Publish", "Manage", "More"):
            assert panel in result.stdout
        assert "stream" not in result.stdout.lower()
        assert "MDF Agent" not in result.stdout

    def test_removed_surfaces_are_gone(self):
        """C8 clean break: dev groups, aliases and renamed commands no longer exist."""
        for argv in (
            ["backend", "--help"], ["stream", "--help"], ["setup", "--help"],
            ["validate", "--help"], ["watch", "--help"], ["update", "--help"],
            ["preview", "--help"], ["approve", "--help"], ["config", "doctor", "--help"],
            ["config", "manifest", "--help"], ["dataset", "cite", "--help"],
            ["dataset", "versions", "--help"], ["dataset", "preview", "--help"],
        ):
            assert runner.invoke(app, argv).exit_code != 0, argv
        assert runner.invoke(app, ["publish", "--preflight-only"]).exit_code == 2
        assert runner.invoke(app, ["status", "--auth"]).exit_code == 2

    def test_first_run_panel_orders_find_publish_agents(self, monkeypatch):
        with TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            result = runner.invoke(app, [])
            assert result.exit_code == 0
            out = result.stdout
            assert out.index("Find data") < out.index("\nPublish\n") < out.index("\nAgents\n")
            assert out.index("mdf login") < out.index("mdf publish --submit")
            assert "mdf setup" not in out

    def test_manifest_help(self):
        result = runner.invoke(app, ["manifest", "--help"])
        assert result.exit_code == 0
        assert "discover" in result.stdout
        assert "inspect" in result.stdout

    def test_publish_help(self):
        result = runner.invoke(app, ["publish", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.stdout
        assert "--preflight-only" not in result.stdout
        assert "--dev-user" not in result.stdout  # hidden

    def test_top_level_commands_have_help(self):
        for cmd in ("init", "check", "whoami", "doctor", "cite", "versions", "clone"):
            assert runner.invoke(app, [cmd, "--help"]).exit_code == 0, cmd

    def test_dataset_subapp_help(self):
        result = runner.invoke(app, ["dataset", "--help"])
        assert result.exit_code == 0
        for sub in ("edit", "withdraw", "resubmit", "diff", "open"):
            assert sub in result.stdout

    def test_admin_subapp_help(self):
        result = runner.invoke(app, ["admin", "--help"])
        assert result.exit_code == 0
        assert "pending" in result.stdout
        assert "approve" in result.stdout
        assert "reject" in result.stdout

    def test_no_add_command(self):
        assert runner.invoke(app, ["add", "--help"]).exit_code != 0

    def test_no_commit_command(self):
        assert runner.invoke(app, ["commit", "--help"]).exit_code != 0


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

    def test_logout_reports_success(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
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

    # CLI-U10 / C6 -------------------------------------------------------

    @staticmethod
    def _auth_check_ok(monkeypatch, payload):
        class FakeResponse:
            status_code = 200
            headers = {}
            text = ""

            def json(self):
                return payload

        monkeypatch.setattr(
            "httpx.Client.request", lambda self, method, url, **kwargs: FakeResponse()
        )

    def test_login_token_shows_identity_and_is_not_saved_by_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        self._auth_check_ok(
            monkeypatch, {"success": True, "name": "Jane Doe", "email": "jane@example.org"}
        )
        result = runner.invoke(app, ["login", "--service", "staging", "--token", "tok-1"])
        assert result.exit_code == 0, result.stdout
        assert "Jane Doe <jane@example.org>" in result.stdout
        assert "staging" in result.stdout
        from mdf.core.config import saved_token_path

        assert not saved_token_path().exists()

    def test_login_token_save_persists_owner_only(self, monkeypatch, tmp_path):
        import stat

        from mdf.core.backend_client import BackendClient
        from mdf.core.config import saved_token_path

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("MDF_CONNECT_TOKEN", raising=False)
        monkeypatch.delenv("MDF_CLIENT_ID", raising=False)
        monkeypatch.delenv("MDF_DEV_USER_ID", raising=False)
        # No cached Globus login (it would take precedence over the saved token).
        monkeypatch.setattr("mdf.auth.globus.is_logged_in", lambda **kwargs: False)
        self._auth_check_ok(monkeypatch, {"success": True, "name": "Jane Doe"})
        result = runner.invoke(app, ["login", "--token", "tok-2", "--save"])
        assert result.exit_code == 0, result.stdout
        path = saved_token_path()
        assert list(json.loads(path.read_text()).values()) == ["tok-2"]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        # Later commands on the same service pick it up without a Globus login...
        assert BackendClient.authenticated(service_instance="staging")._token == "tok-2"
        # ...but it is never sent to another service.
        from mdf.core.config import load_saved_token

        assert load_saved_token("https://api-dev.materialsdatafacility.org") is None
        assert BackendClient.authenticated(service_instance="local")._token is None
        status = runner.invoke(app, ["whoami", "--json"])
        assert json.loads(status.stdout)["method"] == "saved token (mdf login --token --save)"
        # ...and logout removes it, printing one message.
        monkeypatch.setattr("mdf.auth.globus.logout", lambda: False)
        out = runner.invoke(app, ["logout"])
        assert out.exit_code == 0
        assert out.stdout.count("Logged out") == 1
        assert not path.exists()

    def test_login_save_without_token_is_a_usage_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(app, ["login", "--save"])
        assert result.exit_code == 2

    def test_login_without_a_terminal_does_not_prompt(self, monkeypatch, tmp_path):
        """CliRunner stdin is not a TTY: interactive login must fail fast (exit 4)."""
        from mdf.core.exceptions import AuthRequired

        from mdf.auth import globus as globus_auth

        def fake_get_authorizer_for_scopes(scopes, no_browser=False, interactive=None):
            # The root policy (no TTY) is in force, not a per-command override.
            assert interactive is None
            assert globus_auth._INTERACTIVE_DEFAULT.get() is False
            raise AuthRequired()

        monkeypatch.setattr("mdf.auth.globus.get_authorizer_for_scopes", fake_get_authorizer_for_scopes)
        result = runner.invoke(app, ["login"])
        assert result.exit_code == 4
        assert "needs a terminal" in result.stdout

    def test_whoami_reports_a_rejected_token_as_invalid(self, monkeypatch):
        monkeypatch.setenv("MDF_CONNECT_TOKEN", "expired")
        monkeypatch.setattr("mdf.cli.commands.auth._fetch_identity", lambda **kwargs: {"rejected": "true"})
        result = runner.invoke(app, ["whoami", "--json"])
        assert result.exit_code == 4
        payload = json.loads(result.stdout)
        assert payload["error"]["message"] == "Credentials rejected"
        assert payload["status"] == "credentials rejected"

    def test_untrusted_api_url_gets_no_cached_login(self, monkeypatch, tmp_path):
        """Credential re-review #1 and #5: the CLI never sends a cached login to --api-url X."""
        monkeypatch.setenv("HOME", str(tmp_path))
        for name in ("MDF_CONNECT_TOKEN", "MDF_CLIENT_ID", "MDF_DEV_USER_ID", "MDF_TRUST_API_URL"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr("mdf.auth.globus.is_logged_in", lambda **kwargs: True)

        def no_request(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("no request may reach the untrusted host")

        monkeypatch.setattr("httpx.Client.request", no_request)
        who = runner.invoke(app, ["whoami", "--json", "--api-url", "https://attacker.example"])
        assert who.exit_code == 4
        listing = runner.invoke(app, ["list", "--json", "--api-url", "https://attacker.example"])
        assert listing.exit_code == 4
        assert "MDF_TRUST_API_URL" in json.loads(listing.stdout)["error"]["hint"]
        # On the real service the same cached login is what whoami reports.
        known = json.loads(runner.invoke(app, ["whoami", "--json", "--service", "staging"]).stdout)
        assert known["method"] == "cached Globus login"

    def test_whoami_reports_identity(self, monkeypatch):
        monkeypatch.setenv("MDF_CONNECT_TOKEN", "env-token")
        monkeypatch.setattr(
            "mdf.cli.commands.auth._fetch_identity",
            lambda **kwargs: {"name": "Jane Doe", "email": "jane@example.org"},
        )
        human = runner.invoke(app, ["whoami", "--service", "staging"])
        assert "Jane Doe <jane@example.org>" in human.stdout
        payload = json.loads(runner.invoke(app, ["whoami", "--json"]).stdout)
        assert payload["name"] == "Jane Doe" and payload["email"] == "jane@example.org"


class TestInitAndManifestUX:
    """`mdf init` replaces `setup` + `manifest init` (C8)."""

    def test_init_without_a_terminal_fails_fast_instead_of_prompting(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        result = runner.invoke(app, ["init", str(tmp_path), "--title", "T"])
        assert result.exit_code == 2
        assert "--author" in result.stdout and "not a terminal" in result.stdout
        assert not (tmp_path / "mdf.yaml").exists()

    def test_init_prompts_on_a_terminal_and_never_pins_the_service(self, tmp_path, monkeypatch):
        import yaml

        home = tmp_path / "home"
        dataset = tmp_path / "dataset"
        dataset.mkdir()
        monkeypatch.setenv("HOME", str(home))
        # Pretend a person is at the keyboard (CliRunner's stdin is not a TTY).
        from types import SimpleNamespace

        monkeypatch.setattr(
            "mdf.cli.manifest_cmd.sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))
        )
        result = runner.invoke(
            app,
            ["init", str(dataset)],
            input="My Dataset\nDoe, Jane\n\nA useful dataset\nMy Org\nxrd, dft\nCC-BY-4.0\n",
        )
        assert result.exit_code == 0, result.output
        manifest = yaml.safe_load((dataset / "mdf.yaml").read_text())
        assert manifest["title"] == "My Dataset"
        assert manifest["authors"] == ["Doe, Jane"]
        assert manifest["description"] == "A useful dataset"
        assert manifest["subjects"] == ["xrd", "dft"]
        assert manifest["license"] == "CC-BY-4.0"
        assert "mdf check" in result.stdout
        # CLI-U10: nothing pins the service.
        config = home / ".config" / "mdf" / "config.json"
        assert not config.exists() or "service" not in json.loads(config.read_text()).get("defaults", {})

    def test_manifest_discover_preview_does_not_mutate_manifest(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data.json").write_text('{"temperature": 300, "phase": "fcc"}')

            init_result = runner.invoke(
                app,
                ["init", str(root), "--title", "Test", "--author", "Jane Doe"],
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
                ["init", str(root), "--title", "Test", "--author", "Jane Doe"],
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


class TestListRegressions:
    def test_list_json_applies_filters_and_limit(self, monkeypatch):
        seen = {}

        class FakeClient:
            def submissions(self, organization=None, **kwargs):
                # `mdf list` now filters and pages server-side (F-3): record
                # what it asked for, then answer as a single exhausted page.
                seen.update(kwargs)
                return {
                    "success": True,
                    "total": 4,
                    "next_key": None,
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
            "mdf.core.backend_client.BackendClient.authenticated",
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
                "--limit",
                "1",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        # `total` stays the backend's overall count; `count` is what came back.
        assert payload["total"] == 4
        assert payload["count"] == 1
        assert [record["source_id"] for record in payload["submissions"]] == [
            "keep-first"
        ]
        # --status is now handed to the backend rather than only filtered locally.
        assert seen["status"] == "published"
        assert seen["limit"] == 50  # --search needs full pages to filter over
        assert seen["start_key"] is None


class TestPreflightAndIdentifiers:
    def test_check_renders_preflight_without_submission(self, monkeypatch):
        fake_preflight = {
            "success": True,
            "issues": [],
            "service": "staging",
            "target_url": "https://api.example",
            "source_summary": {"files": 1, "bytes": 3},
            "sources": [{"source": "./data.csv", "kind": "local", "file_count": 1, "total_bytes": 3}],
        }
        monkeypatch.setattr("mdf.cli.commands.publish.run_preflight", lambda *args, **kwargs: fake_preflight)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data.csv").write_text("a,b")
            result = runner.invoke(
                app,
                ["check", str(root / "data.csv"), "--title", "Test", "--author", "Jane"],
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

        monkeypatch.setattr("mdf.core.backend_client.BackendClient.authenticated", lambda **kwargs: FakeClient())
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
        monkeypatch.setattr("mdf.cli.formatting.read_client", lambda **kwargs: FakeClient())
        result = runner.invoke(app, ["show", "10.1234/example"])
        assert result.exit_code == 0
        assert seen["card_id"] == "src-resolved"
