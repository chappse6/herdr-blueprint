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
    assert len(lines) == 2


def test_install_skips_agents_that_are_not_installed(tmp_path):
    skill = make_skill(tmp_path)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    skills_install.install(skill, home)
    assert not (home / ".agents").exists()


def test_install_twice_is_harmless(tmp_path):
    skill, home = make_skill(tmp_path), make_home(tmp_path)
    skills_install.install(skill, home)
    lines = skills_install.install(skill, home)
    assert all(line.startswith("already linked") for line in lines)


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


def test_skill_dir_points_at_the_repo_skill():
    assert (skills_install.SKILL_DIR / "SKILL.md").is_file()
