"""Link the Blueprint agent skill into Claude Code and Codex skill folders."""

from __future__ import annotations

import shutil
from pathlib import Path

# The skill lives in the repo next to src/ (uv installs the project in editable mode).
SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "blueprint"

# Marks a copied skill as ours, for systems where symlinks are not allowed.
MARKER = ".herdr-blueprint"


def skill_folders(home: Path) -> list[Path]:
    """Skill folders of the agents that are installed."""
    folders = []
    if (home / ".claude").is_dir():
        folders.append(home / ".claude" / "skills")
    if (home / ".codex").is_dir() or (home / ".agents").is_dir():
        folders.append(home / ".agents" / "skills")
    return folders


def _is_ours(dest: Path, skill: Path) -> bool:
    if dest.is_symlink():
        return dest.resolve() == skill.resolve()
    return (dest / MARKER).is_file()


def install(skill: Path, home: Path) -> list[str]:
    lines = []
    for folder in skill_folders(home):
        dest = folder / skill.name
        folder.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() and _is_ours(dest, skill):
            lines.append(f"already linked: {dest}")
            continue
        if dest.exists() or dest.is_symlink():
            if not _is_ours(dest, skill):
                lines.append(f"skipped, path already exists: {dest}")
                continue
            shutil.rmtree(dest)  # refresh our old copy
        try:
            dest.symlink_to(skill, target_is_directory=True)
            lines.append(f"linked: {dest}")
        except OSError:
            shutil.copytree(skill, dest)
            (dest / MARKER).write_text("Installed by herdr-blueprint\n", encoding="utf-8")
            lines.append(f"copied: {dest}")
    return lines


def uninstall(skill: Path, home: Path) -> list[str]:
    lines = []
    for folder in skill_folders(home):
        dest = folder / skill.name
        if not (dest.exists() or dest.is_symlink()) or not _is_ours(dest, skill):
            continue
        if dest.is_symlink():
            dest.unlink()
        else:
            shutil.rmtree(dest)
        lines.append(f"removed: {dest}")
    return lines
