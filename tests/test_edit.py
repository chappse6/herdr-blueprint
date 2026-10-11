from pathlib import Path

import pytest

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


def test_blueprint_editor_comes_first():
    # For people whose $EDITOR is set up for something else (say, starting in insert mode).
    which = which_only("nvim", "hx")
    environ = {"BLUEPRINT_EDITOR": "nvim", "VISUAL": "hx", "EDITOR": "hx"}
    assert edit.editor_command(environ, which=which, system="posix") == ["/usr/bin/nvim"]


def test_an_edit_of_a_design_tied_to_code_asks_for_the_code_change():
    design = "graph LR\n  A[POST /token] --> B[verifyPkce]\n  %% code: B src/auth/pkce.ts:10\n"
    assert edit.tied_to_code(design) and not edit.tied_to_code("graph LR\n  A --> B\n")
    sent = edit.text_prompt("Token", design, diagram=True, tied_to_code=True)
    assert "`%% code:`" in sent and "run the tests" in sent
    changed = edit.file_prompt("docs/token.mmd", "-a\n+b\n", diverged=False, tied_to_code=True)
    assert "`%% code:`" in changed and "run the tests" in changed
    assert "run the tests" not in edit.file_prompt("docs/token.mmd", "-a\n+b\n", diverged=False)


def test_no_editor_at_all():
    assert edit.editor_command({}, which=which_only(), system="posix") is None


def test_a_configured_editor_that_does_not_exist_is_an_error():
    # Passing it on would fail inside the suspended terminal.
    with pytest.raises(edit.EditorError, match="Editor not found: nvimm"):
        edit.editor_command({"EDITOR": "nvimm"}, which=which_only("vim"), system="posix")


def test_unbalanced_quotes_in_the_editor_setting_are_an_error():
    with pytest.raises(edit.EditorError, match=r"Cannot read \$EDITOR"):
        edit.editor_command({"EDITOR": '"vim'}, which=which_only("vim"), system="posix")


def test_an_editor_path_with_home_or_a_plain_file_works(tmp_path, monkeypatch):
    program = tmp_path / "bin" / "myvim"
    program.parent.mkdir()
    program.write_text("#!/bin/sh\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # what ~ means on Windows
    nowhere = which_only()
    assert edit.editor_command({"EDITOR": "~/bin/myvim -n"}, which=nowhere, system="posix") == [str(program), "-n"]
    # A plain path, read the way this system reads it (Windows keeps its backslashes).
    assert edit.editor_command({"EDITOR": str(program)}, which=nowhere) == [str(program)]


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


def test_windows_device_names_are_not_used_as_is():
    for title in ("CON", "nul", "COM1", "lpt9.md"):
        name = edit.copy_name(title, diagram=False)
        assert name.split(".")[0].upper() not in {"CON", "NUL", "COM1", "LPT9"}, name


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


def test_an_editor_adding_a_final_newline_or_crlf_is_no_change():
    # vim and nano add the final newline on save; sent snippets have none.
    assert edit.same_text("graph LR\n  A --> B", "graph LR\n  A --> B\n")
    assert edit.same_text("a\r\nb\r\n", "a\nb\n")
    assert not edit.same_text("a\nb", "a\nc")
    assert edit.unified_diff("a\nb", "a\nb\n", "x.md") == ""


def test_diff_lines_are_the_file_lines():
    # str.splitlines would also split on form feeds and U+2028.
    diff = edit.unified_diff("one\x0ctwo\nthree\n", "one\x0ctwo\nTHREE\n", "x.md")
    assert " one\x0ctwo" in diff


def test_display_path_is_relative_inside_the_agent_folder(tmp_path):
    inside = tmp_path / "docs" / "flow.md"
    assert edit.display_path(inside, tmp_path) == "docs/flow.md"
    elsewhere = Path("/elsewhere/flow.md")
    assert edit.display_path(elsewhere, tmp_path) == str(elsewhere)  # as the system writes it
    assert edit.display_path(inside, None) == str(inside)


def test_file_prompt_fences_the_diff_and_asks_to_apply_and_show():
    prompt = edit.file_prompt("docs/flow.md", "--- a\n+++ b\n+new line\n", diverged=False)
    assert prompt.startswith("I edited `docs/flow.md` in Blueprint.")
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
    attachment = Path("/tmp/b p/flow.diff")
    prompt = edit.file_prompt("docs/flow.md", "x" * 10, diverged=False, attachment=attachment)
    assert f"`{attachment}`" in prompt and "```diff" not in prompt
    assert edit.needs_attachment("x" * (edit.INLINE_LIMIT + 1))
    assert not edit.needs_attachment("x" * edit.INLINE_LIMIT)


def test_content_with_backticks_cannot_break_the_fence():
    prompt = edit.text_prompt("Plan", "```\nx\n```\n", diagram=False)
    # The fence is longer than any backtick run inside, so the content stays inside it.
    assert "````markdown\n```\nx\n```\n````" in prompt


def test_control_characters_are_removed_from_prompts():
    # argv cannot hold NUL, and escapes have no business in a pasted prompt.
    assert edit.prompt_safe("a\x00b\x1b[31mc\td\ne") == "ab[31mc\td\ne"


# --- agents ---------------------------------------------------------------------------------

def test_agent_kinds_on_path_claude_first():
    assert edit.kinds_on_path(which_only("codex", "claude", "gemini")) == ["claude", "codex", "gemini"]
    assert edit.kinds_on_path(which_only()) == []


def test_agent_names_are_valid_and_per_pane():
    assert edit.agent_name("claude", "w1:p1") == "bp-claude-w1-p1"
    assert edit.agent_name("claude", "w12:pAB") == "bp-claude-w12-pab"
    name = edit.agent_name("mastracode", "w999:p" + "Z" * 40)
    assert len(name) <= 32 and name[0].isalpha()


# --- the editor pane's session -----------------------------------------------------------

def test_the_session_runs_the_editor_and_leaves_its_exit_code(tmp_path):
    import json
    import subprocess

    copy = tmp_path / "flow.md"
    copy.write_text("x", encoding="utf-8")
    seen = []

    def run(command):
        seen.append(command)
        return subprocess.CompletedProcess(command, 3)

    env = {edit.SESSION_COPY: str(copy), edit.SESSION_COMMAND: json.dumps(["nvim", "+4"])}
    assert edit.run_session(env, run=run) == 0
    assert seen == [["nvim", "+4", str(copy)]]
    assert edit.exit_file(copy).read_text() == "3"


def test_an_editor_that_cannot_start_leaves_127(tmp_path):
    import json

    def run(command):
        raise FileNotFoundError(command[0])

    copy = tmp_path / "flow.md"
    copy.write_text("x", encoding="utf-8")
    env = {edit.SESSION_COPY: str(copy), edit.SESSION_COMMAND: json.dumps(["nope"])}
    edit.run_session(env, run=run)
    assert edit.exit_file(copy).read_text() == "127"


def test_only_edits_of_a_diagram_tied_to_code_are_work_orders():
    doc = "# Token\n\nIntro.\n\n```mermaid\ngraph LR\n  A --> B\n  %% code: A src/a.py:1\n```\n"
    assert not edit.touches_code(doc, doc.replace("# Token", "# Tokens"), diagram=False)
    assert edit.touches_code(doc, doc.replace("A --> B", "A --> C"), diagram=False)
    assert edit.touches_code(doc, doc.replace("```mermaid\ngraph LR\n  A --> B\n  %% code: A src/a.py:1\n```\n", ""), diagram=False)
    plain = "```\n%% code: A x.py:1\n```\n"  # not a Mermaid block
    assert not edit.touches_code(plain, plain + "more\n", diagram=False)
    mmd = "graph LR\n  A --> B\n  %% code: A a.py:1\n"
    assert edit.touches_code(mmd, mmd.replace("--> B", "--> C"), diagram=True)
    assert not edit.touches_code("graph LR\n  A --> B\n", "graph LR\n  A --> C\n", diagram=True)


def test_the_session_survives_a_missing_setup_or_a_removed_folder(tmp_path):
    import json
    import subprocess

    assert edit.run_session({}) == 2  # started by hand: says so, no traceback
    gone = tmp_path / "closed" / "flow.md"  # Blueprint quit and its folder went away
    env = {edit.SESSION_COPY: str(gone), edit.SESSION_COMMAND: json.dumps(["nvim"])}
    assert edit.run_session(env, run=lambda command: subprocess.CompletedProcess(command, 0)) == 0


def test_the_session_writes_an_exit_code_even_when_running_the_editor_breaks(tmp_path):
    import json

    copy = tmp_path / "flow.md"
    copy.write_text("x", encoding="utf-8")

    def broken(command):
        raise RuntimeError("boom")

    env = {edit.SESSION_COPY: str(copy), edit.SESSION_COMMAND: json.dumps(["nvim"])}
    with pytest.raises(RuntimeError):
        edit.run_session(env, run=broken)
    assert edit.exit_file(copy).read_text() == "1"
