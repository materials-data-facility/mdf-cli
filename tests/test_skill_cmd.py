"""Tests for the 'mdf skill' commands and the packaged skill bundle."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from typer.testing import CliRunner

from mdf.cli.main import app
from mdf.cli import skill_cmd
from mdf.cli.skill_cmd import BUNDLED_SKILLS, _bundle_path

runner = CliRunner()


def _parse_frontmatter(text: str) -> dict:
    """Parse the YAML frontmatter block out of a SKILL.md."""
    import yaml

    assert text.startswith("---\n"), "SKILL.md must open with a frontmatter fence"
    _, block, _rest = text.split("---\n", 2)
    return yaml.safe_load(block)


class TestSkillBundle:
    """The packaged mdf-publish bundle itself."""

    def test_bundle_exists(self):
        """The mdf-publish bundle ships with the package."""
        assert _bundle_path("mdf-publish").is_dir()

    def test_skill_md_frontmatter(self):
        """SKILL.md has valid frontmatter with name and description."""
        skill_md = _bundle_path("mdf-publish") / "SKILL.md"
        meta = _parse_frontmatter(skill_md.read_text())
        assert meta["name"] == "mdf-publish"
        assert meta["description"]
        assert "/mdf-publish" in meta["description"]

    def test_reference_files_present(self):
        """Both reference documents the SKILL.md points at exist."""
        refs = _bundle_path("mdf-publish") / "references"
        assert (refs / "metadata-checklist.md").is_file()
        assert (refs / "evidence-recipes.md").is_file()

    def test_skill_md_documents_both_human_gates(self):
        """The two approval gates are present and named."""
        body = (_bundle_path("mdf-publish") / "SKILL.md").read_text()
        assert "HUMAN GATE #1" in body
        assert "HUMAN GATE #2" in body
        assert "--submit" in body


class TestSkillList:
    """Tests for 'mdf skill list'."""

    def test_list_shows_bundled_skills(self):
        result = runner.invoke(app, ["skill", "list"])
        assert result.exit_code == 0
        for name in BUNDLED_SKILLS:
            assert name in result.stdout


class TestSkillPath:
    """Tests for 'mdf skill path'."""

    def test_path_prints_bundle_location(self):
        result = runner.invoke(app, ["skill", "path"])
        assert result.exit_code == 0
        assert Path(result.stdout.strip()).is_dir()

    def test_path_rejects_unknown_skill(self):
        result = runner.invoke(app, ["skill", "path", "not-a-skill"])
        assert result.exit_code == 1


class TestSkillInstall:
    """Tests for 'mdf skill install'."""

    def test_default_agent_targets(self, monkeypatch, tmp_path):
        """Each runtime resolves to its conventional global skills directory."""
        monkeypatch.setattr(skill_cmd.Path, "home", classmethod(lambda cls: tmp_path))
        assert skill_cmd._default_destination("claude", "mdf-publish", False) == (
            tmp_path / ".claude/skills/mdf-publish"
        )
        assert skill_cmd._default_destination("codex", "mdf-publish", False) == (
            tmp_path / ".codex/skills/mdf-publish"
        )
        assert skill_cmd._default_destination("gemini", "mdf-publish", False) == (
            tmp_path / ".gemini/skills/mdf-publish"
        )

    def test_project_target_is_claude_only(self):
        result = runner.invoke(app, ["skill", "install", "--agent", "codex", "--project"])
        assert result.exit_code == 2
        assert "only for --agent claude" in result.stderr

    def test_target_rejects_multiple_agents(self, tmp_path):
        result = runner.invoke(
            app,
            [
                "skill",
                "install",
                "--agent",
                "claude",
                "--agent",
                "codex",
                "--target",
                str(tmp_path / "mdf-publish"),
            ],
        )
        assert result.exit_code == 2
        assert "only be used with one agent" in result.stderr

    def test_all_installs_to_each_agent_target(self, monkeypatch, tmp_path):
        """--agent all copies the complete bundle into all three global locations."""
        monkeypatch.setattr(skill_cmd.Path, "home", classmethod(lambda cls: tmp_path))
        result = runner.invoke(app, ["skill", "install", "--agent", "all"])
        assert result.exit_code == 0
        for agent in ("claude", "codex", "gemini"):
            installed = tmp_path / f".{agent}" / "skills" / "mdf-publish"
            assert (installed / "SKILL.md").is_file()
            assert f"for {agent}" in result.stdout
            assert "/mdf-publish" in result.stdout

    def test_force_applies_per_target(self, monkeypatch, tmp_path):
        """One refused target does not prevent other targets from installing."""
        monkeypatch.setattr(skill_cmd.Path, "home", classmethod(lambda cls: tmp_path))
        codex = tmp_path / ".codex" / "skills" / "mdf-publish"
        codex.mkdir(parents=True)
        (codex / "SKILL.md").write_text("old")

        result = runner.invoke(
            app, ["skill", "install", "--agent", "codex", "--agent", "gemini"]
        )
        assert result.exit_code == 1
        assert (codex / "SKILL.md").read_text() == "old"
        assert (tmp_path / ".gemini/skills/mdf-publish/SKILL.md").is_file()

        result = runner.invoke(
            app,
            ["skill", "install", "--agent", "codex", "--agent", "gemini", "--force"],
        )
        assert result.exit_code == 0
        assert (codex / "SKILL.md").read_text() != "old"

    def test_install_to_target(self):
        """Files land in an explicit --target directory."""
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "skills" / "mdf-publish"
            result = runner.invoke(app, ["skill", "install", "--target", str(target)])
            assert result.exit_code == 0
            assert (target / "SKILL.md").is_file()
            assert (target / "references" / "metadata-checklist.md").is_file()
            assert (target / "references" / "evidence-recipes.md").is_file()

    def test_installed_skill_md_parses(self):
        """The installed SKILL.md keeps valid frontmatter."""
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "mdf-publish"
            result = runner.invoke(app, ["skill", "install", "--target", str(target)])
            assert result.exit_code == 0
            meta = _parse_frontmatter((target / "SKILL.md").read_text())
            assert meta["name"] == "mdf-publish"
            assert meta["description"]

    def test_install_refuses_overwrite(self):
        """A second install without --force is refused."""
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "mdf-publish"
            assert runner.invoke(app, ["skill", "install", "--target", str(target)]).exit_code == 0
            result = runner.invoke(app, ["skill", "install", "--target", str(target)])
            assert result.exit_code == 1
            assert "Already installed" in result.stdout

    def test_install_force_overwrites(self):
        """--force replaces an existing installation."""
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "mdf-publish"
            assert runner.invoke(app, ["skill", "install", "--target", str(target)]).exit_code == 0
            (target / "SKILL.md").write_text("clobbered")
            result = runner.invoke(app, ["skill", "install", "--target", str(target), "--force"])
            assert result.exit_code == 0
            assert (target / "SKILL.md").read_text() != "clobbered"

    def test_install_rejects_unknown_skill(self):
        with TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app, ["skill", "install", "not-a-skill", "--target", str(tmpdir)]
            )
            assert result.exit_code == 1
            assert "Unknown skill" in result.stdout
