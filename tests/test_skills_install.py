from pathlib import Path

from herdr_blueprint import skills_install


def make_skill(tmp_path: Path) -> Path:
    skill = tmp_path / "repo" / "skills" / "blueprint"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: blueprint\n---\n", encoding="utf-8")
    return skill


def make_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir()
    return home


def test_install_links_into_both_agents(tmp_path):
    skill, home = make_skill(tmp_path), make_home(tmp_path)
    lines = skills_install.install(skill, home)
    for dest in [home / ".claude/skills/blueprint", home / ".agents/skills/blueprint"]:
        assert dest.is_symlink()
        assert dest.resolve() == skill.resolve()
    assert len([line for line in lines if not line.startswith("command")]) == 2


def test_install_skips_agents_that_are_not_installed(tmp_path):
    skill = make_skill(tmp_path)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    skills_install.install(skill, home)
    assert not (home / ".agents").exists()


def test_install_twice_is_harmless(tmp_path):
    skill, home = make_skill(tmp_path), make_home(tmp_path)
    skills_install.install(skill, home)
    lines = [line for line in skills_install.install(skill, home) if not line.startswith("command")]
    assert lines and all(line.startswith("already linked") for line in lines)


def test_install_leaves_a_foreign_folder_alone(tmp_path):
    skill, home = make_skill(tmp_path), make_home(tmp_path)
    foreign = home / ".claude/skills/blueprint"
    foreign.mkdir(parents=True)
    lines = skills_install.install(skill, home)
    assert any(line.startswith("skipped") for line in lines)
    assert not foreign.is_symlink()


def test_install_copies_when_symlinks_fail(tmp_path, monkeypatch):
    skill, home = make_skill(tmp_path), make_home(tmp_path)

    def refuse(self, target, target_is_directory=False):
        raise OSError("symlinks not allowed")

    monkeypatch.setattr(Path, "symlink_to", refuse)
    skills_install.install(skill, home)
    dest = home / ".claude/skills/blueprint"
    assert (dest / "SKILL.md").is_file()
    assert (dest / skills_install.MARKER).is_file()


def test_uninstall_removes_links_and_copies_only(tmp_path, monkeypatch):
    skill, home = make_skill(tmp_path), make_home(tmp_path)
    skills_install.install(skill, home)
    foreign = home / ".agents/skills/other"
    foreign.mkdir(parents=True)
    skills_install.uninstall(skill, home)
    assert not (home / ".claude/skills/blueprint").exists()
    assert not (home / ".agents/skills/blueprint").exists()
    assert foreign.exists()


def test_skill_dir_points_at_the_repo_skill(monkeypatch):
    monkeypatch.delenv("HERDR_PLUGIN_ROOT", raising=False)
    assert (skills_install.skill_dir() / "SKILL.md").is_file()


def plugin_folder(base):
    skill = base / "skills" / "blueprint"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: blueprint\n---\n", encoding="utf-8")
    return skill


def test_skill_dir_prefers_the_plugin_folder_herdr_names(tmp_path, monkeypatch):
    # An installed copy of the package lives in .venv, far from skills/.
    skill = plugin_folder(tmp_path / "plugin")
    monkeypatch.setenv("HERDR_PLUGIN_ROOT", str(tmp_path / "plugin"))
    assert skills_install.skill_dir() == skill


def test_skill_dir_falls_back_to_the_current_folder(tmp_path, monkeypatch):
    skill = plugin_folder(tmp_path / "plugin")
    monkeypatch.delenv("HERDR_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(skills_install, "__file__", str(tmp_path / "venv/lib/site-packages/herdr_blueprint/skills_install.py"))
    monkeypatch.chdir(tmp_path / "plugin")
    assert skills_install.skill_dir() == skill


def test_install_puts_a_short_command_on_the_path(tmp_path):
    import os

    skill, home = make_skill(tmp_path), make_home(tmp_path)
    lines = skills_install.install(skill, home)
    command = home / ".local" / "bin" / "herdr-blueprint"
    if os.name == "nt":
        assert not command.exists()  # no shell script on Windows; the skill's long form works there
        return
    text = command.read_text(encoding="utf-8")
    assert f'root="{skill.resolve().parent.parent}"' in text and "python -m herdr_blueprint" in text
    assert os.access(command, os.X_OK) and any(str(command) in line for line in lines)
    skills_install.uninstall(skill, home)
    assert not command.exists()


def test_install_leaves_someone_elses_command_alone(tmp_path):
    skill, home = make_skill(tmp_path), make_home(tmp_path)
    command = home / ".local" / "bin" / "herdr-blueprint"
    command.parent.mkdir(parents=True)
    command.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    skills_install.install(skill, home)
    skills_install.uninstall(skill, home)
    assert command.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"


def test_install_can_copy_where_a_link_would_break(tmp_path):
    # herdr builds in a folder it then moves: a link from there would dangle.
    skill, home = make_skill(tmp_path), make_home(tmp_path)
    skills_install.install(skill, home, copy=True)
    dest = home / ".claude" / "skills" / "blueprint"
    assert dest.is_dir() and not dest.is_symlink() and (dest / skills_install.MARKER).is_file()
    (skill / "SKILL.md").write_text("---\nname: blueprint\n---\nnew\n", encoding="utf-8")
    skills_install.install(skill, home, copy=True)  # the next install or update refreshes it
    assert "new" in (dest / "SKILL.md").read_text(encoding="utf-8")


def test_the_short_command_finds_the_plugin_when_its_folder_moved(tmp_path):
    import os

    if os.name == "nt":
        return
    skill, home = make_skill(tmp_path), make_home(tmp_path)
    skills_install.install(skill, home, copy=True)
    text = (home / ".local" / "bin" / "herdr-blueprint").read_text(encoding="utf-8")
    assert "herdr-plugin.toml" in text and "plugin list --plugin seeun.blueprint" in text
