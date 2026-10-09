from pathlib import Path

from herdr_blueprint.sources.follow import is_viewable

ROOT = Path("/work/repo")


def test_markdown_and_mermaid_are_viewable():
    for name in ["README.md", "docs/a.markdown", "flow.mmd", "x/y.mermaid", "UP.MD"]:
        assert is_viewable(ROOT / name, ROOT), name


def test_other_files_are_not():
    assert not is_viewable(ROOT / "main.py", ROOT)


def test_hidden_and_build_folders_are_skipped():
    for name in [".git/x.md", "node_modules/pkg/README.md", "dist/a.md", ".venv/lib/x.md"]:
        assert not is_viewable(ROOT / name, ROOT), name


def test_files_outside_root_are_skipped():
    assert not is_viewable(Path("/elsewhere/a.md"), ROOT)
