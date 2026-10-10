from pathlib import Path

from herdr_blueprint import edit


def which_only(*found):
    table = {name: f"/usr/bin/{name}" for name in found}
    return lambda name: table.get(name)


# --- the editor ----------------------------------------------------------------------

def test_editor_prefers_visual_then_editor_then_path():
    which = which_only("vim", "nano", "nvim", "hx")
    assert edit.editor_command({"VISUAL": "nvim -u x", "EDITOR": "hx"}, which=which, system="posix") == [
        "/usr/bin/nvim", "-u", "x",
    ]
    assert edit.editor_command({"EDITOR": "hx"}, which=which, system="posix") == ["/usr/bin/hx"]
    assert edit.editor_command({}, which=which, system="posix") == ["/usr/bin/vim"]
    assert edit.editor_command({}, which=which_only("nano"), system="posix") == ["/usr/bin/nano"]


def test_no_editor_at_all():
    assert edit.editor_command({}, which=which_only(), system="posix") is None


def test_notepad_is_the_windows_last_resort():
    assert edit.editor_command({}, which=which_only("notepad"), system="nt") == ["/usr/bin/notepad"]
    assert edit.editor_command({}, which=which_only("notepad"), system="posix") is None


def test_quoted_windows_paths_and_bare_names_resolve():
    exe = r"C:\Program Files\Notepad++\notepad++.exe"
    which = lambda name: name if name == exe else None
    assert edit.editor_command({"EDITOR": f'"{exe}" -multiInst'}, which=which, system="nt") == [exe, "-multiInst"]
    # EDITOR=code finds code.cmd on PATH, and gets --wait.
    which = lambda name: r"C:\VS Code\bin\code.cmd" if name == "code" else None
    assert edit.editor_command({"EDITOR": "code"}, which=which, system="nt") == [r"C:\VS Code\bin\code.cmd", "--wait"]


def test_gui_editors_wait_unless_told_already():
    which = which_only("code", "subl")
    assert edit.editor_command({"EDITOR": "code"}, which=which, system="posix") == ["/usr/bin/code", "--wait"]
    assert edit.editor_command({"EDITOR": "subl -w"}, which=which, system="posix") == ["/usr/bin/subl", "-w"]


# --- the copy's name -------------------------------------------------------------------

def test_copy_keeps_a_clean_name_and_the_right_suffix():
    assert edit.copy_name("login-flow.md", diagram=False) == "login-flow.md"
    assert edit.copy_name("flow.mmd", diagram=True) == "flow.mmd"
    assert edit.copy_name("Login flow", diagram=True) == "Login-flow.mmd"
    assert edit.copy_name("notes.markdown", diagram=False) == "notes.md"


def test_hostile_titles_still_make_a_valid_name():
    for title in ("a" * 400, "bad\x00name", 'what: is* this? <x> |y| "z"', "../../etc/passwd", "", "한글 제목"):
        name = edit.copy_name(title, diagram=False)
        assert name.endswith(".md")
        assert len(name) <= 64
        assert all(c.isascii() and (c.isalnum() or c in "._-") for c in name), name
        assert not name.startswith(".")


# --- diff and prompt ---------------------------------------------------------------------

def test_diff_is_empty_without_a_change():
    assert edit.unified_diff("a\nb\n", "a\nb\n", "x.md") == ""
    diff = edit.unified_diff("a\nb\n", "a\nc\n", "docs/x.md")
    assert "-b" in diff and "+c" in diff and "docs/x.md" in diff


def test_display_path_is_relative_inside_the_agent_folder(tmp_path):
    inside = tmp_path / "docs" / "flow.md"
    assert edit.display_path(inside, tmp_path) == "docs/flow.md"
    assert edit.display_path(Path("/elsewhere/flow.md"), tmp_path) == "/elsewhere/flow.md"
    assert edit.display_path(inside, None) == str(inside)


def test_file_prompt_fences_the_diff_and_asks_to_apply_and_show():
    prompt = edit.file_prompt("docs/flow.md", "--- a\n+++ b\n+new line\n", diverged=False)
    assert prompt.startswith("I edited docs/flow.md in Blueprint.")
    assert "anything that depends on it" in prompt and "show the result in Blueprint" in prompt
    assert "```diff\n--- a\n+++ b\n+new line\n```" in prompt
    assert "changed" not in prompt.split("```")[0].lower().replace("i edited", "")


def test_file_prompt_says_when_the_file_changed_meanwhile():
    prompt = edit.file_prompt("docs/flow.md", "+x\n", diverged=True)
    assert "changed on disk" in prompt and "read the file" in prompt


def test_text_prompt_sends_the_new_version_not_a_diff():
    prompt = edit.text_prompt("Login flow", "graph LR\n  A --> B\n", diagram=True)
    assert "the diagram you sent ('Login flow')" in prompt
    assert "```mermaid\ngraph LR\n  A --> B\n```" in prompt
    assert "send the new version to Blueprint" in prompt
    note = edit.text_prompt("Plan", "# Plan\n", diagram=False)
    assert "the note you sent ('Plan')" in note and "```markdown" in note


def test_long_bodies_are_named_by_path():
    prompt = edit.file_prompt("docs/flow.md", "x" * 10, diverged=False, attachment=Path("/tmp/bp/flow.diff"))
    assert "/tmp/bp/flow.diff" in prompt and "```diff" not in prompt
    assert edit.needs_attachment("x" * (edit.INLINE_LIMIT + 1))
    assert not edit.needs_attachment("x" * edit.INLINE_LIMIT)


def test_content_with_backticks_cannot_break_the_fence():
    prompt = edit.text_prompt("Plan", "```\nx\n```\n", diagram=False)
    # The fence is longer than any backtick run inside, so the content stays inside it.
    assert "````markdown\n```\nx\n```\n````" in prompt


# --- agents ---------------------------------------------------------------------------------

def test_agent_kinds_on_path_claude_first():
    assert edit.kinds_on_path(which_only("codex", "claude", "gemini")) == ["claude", "codex", "gemini"]
    assert edit.kinds_on_path(which_only()) == []


def test_agent_names_are_valid_and_per_pane():
    assert edit.agent_name("claude", "w1:p1") == "bp-claude-w1-p1"
    assert edit.agent_name("claude", "w12:pAB") == "bp-claude-w12-pab"
    name = edit.agent_name("mastracode", "w999:p" + "Z" * 40)
    assert len(name) <= 32 and name[0].isalpha()
