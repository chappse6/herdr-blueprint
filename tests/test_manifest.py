"""The plugin must keep working after herdr moves its folder.

herdr builds a plugin in a temporary checkout, then moves it into place. Any
absolute path the build writes into .venv (console-script shebangs, editable
.pth files) then points at a folder that no longer exists.
"""

import re
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")  # Python 3.11+

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = tomllib.loads((ROOT / "herdr-plugin.toml").read_text(encoding="utf-8"))
SKILL = (ROOT / "skills" / "blueprint" / "SKILL.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_build_installs_a_copy_not_a_link_to_the_checkout():
    (build,) = MANIFEST["build"]
    assert build["command"][:2] == ["uv", "sync"]
    assert "--no-editable" in build["command"]


def test_commands_run_the_module_not_a_console_script():
    commands = [pane["command"] for pane in MANIFEST["panes"]]
    commands += [action["command"] for action in MANIFEST["actions"]]
    for command in commands:
        assert command[:4] == ["uv", "run", "--no-sync", "python"], command
        assert command[4:6] == ["-m", "herdr_blueprint"], command


def test_agents_run_the_module_too():
    for text in (SKILL, README):
        assert not re.search(r"--no-sync herdr-blueprint", text)
        assert "--no-sync python -m herdr_blueprint" in text


def test_finding_the_plugin_needs_only_uv():
    # jq is missing on many machines (most Windows ones); uv is required anyway.
    for text in (SKILL, README):
        assert "jq " not in text
        assert "herdr plugin list --plugin seeun.blueprint --json | uv run --no-project" in text
