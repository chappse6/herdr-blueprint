import json
import os
import subprocess
from pathlib import Path

from herdr_blueprint.sources import pane


def completed(stdout: str, code: int = 0):
    return lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr="")


def pane_json(**fields) -> str:
    return json.dumps({"id": "cli:pane:get", "result": {"pane": {"pane_id": "w1:p1", **fields}}})


def error_json(code: str) -> str:
    return json.dumps({"error": {"code": code, "message": "..."}})


# --- the pane next to Blueprint -------------------------------------------------------

def test_source_reads_agent_task_and_folder():
    seen = {}

    def run(cmd, **kwargs):
        seen.update(kwargs, cmd=cmd)
        return completed(pane_json(
            agent="claude", cwd="/work/app", foreground_cwd="/work/app/web",
            tokens={"task": "Fix login"}, terminal_title_stripped="something else",
        ))(cmd)

    source = pane.source_of("herdr", "w1:p1", run=run)
    assert seen["cmd"] == ["herdr", "pane", "get", "w1:p1"]
    assert seen["encoding"] == "utf-8"
    assert source == pane.Source("w1:p1", agent="claude", task="Fix login", cwd=Path("/work/app/web"))


def test_source_falls_back_to_the_agent_terminal_title():
    run = completed(pane_json(agent="codex", cwd="/w", terminal_title_stripped="Review diff"))
    assert pane.source_of("herdr", "w1:p1", run=run).task == "Review diff"


def test_a_shell_pane_has_no_agent_and_no_task():
    run = completed(pane_json(cwd="/w", terminal_title_stripped="zsh"))
    source = pane.source_of("herdr", "w1:p1", run=run)
    assert (source.agent, source.task, source.cwd) == (None, None, Path("/w"))


def test_source_cleans_control_characters_in_the_task():
    run = completed(pane_json(agent="claude", cwd="/w", tokens={"task": "a\x1b[31mb"}))
    assert "\x1b" not in pane.source_of("herdr", "w1:p1", run=run).task


def test_no_source_without_a_pane_or_when_herdr_fails():
    def boom(cmd, **kwargs):
        raise OSError("no herdr")

    assert pane.source_of("herdr", None) is None
    assert pane.source_of("herdr", "w1:p1", run=boom) is None
    assert pane.source_of("herdr", "w1:p1", run=completed(error_json("pane_not_found"), 1)) is None
    assert pane.source_of("herdr", "w1:p1", run=completed("not json")) is None


def test_pane_exists():
    assert pane.pane_exists("herdr", "w1:p1", run=completed(pane_json()))
    assert not pane.pane_exists("herdr", "w1:p1", run=completed(error_json("pane_not_found"), 1))


# --- asking the agent to draw ----------------------------------------------------------

def test_ask_submits_the_blueprint_prompt_to_the_agent_pane():
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        return completed("{}")(cmd)

    result = pane.ask_to_draw("herdr", pane.Source("w1:p1", agent="claude"), run=run)
    assert seen["cmd"][:4] == ["herdr", "agent", "prompt", "w1:p1"]
    prompt = seen["cmd"][4]
    assert "blueprint skill" in prompt and "--draw" in prompt
    # A bare diagram doesn't say what is going on: ask for a summary with it.
    assert "Markdown" in prompt and "summary" in prompt
    assert result == (True, "Asked claude to draw")


def test_ask_explains_why_it_failed():
    source = pane.Source("w1:p1", agent="claude")

    def boom(cmd, **kwargs):
        raise OSError("no herdr")

    assert pane.ask_to_draw("herdr", source, run=completed(error_json("agent_blocked"), 1)) == (
        False, "claude is waiting for your answer"
    )
    assert pane.ask_to_draw("herdr", source, run=completed(error_json("agent_not_found"), 1)) == (
        False, "claude is not in that pane anymore"
    )
    assert pane.ask_to_draw("herdr", source, run=completed("garbage", 1)) == (False, "Could not reach claude")
    assert pane.ask_to_draw("herdr", source, run=boom) == (False, "Could not reach claude")


# --- files to open ---------------------------------------------------------------------

def touch(path: Path, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def no_git(cmd, **kwargs):
    raise OSError("git not installed")


def test_finds_docs_and_diagrams_newest_first(tmp_path):
    old = touch(tmp_path / "README.md", 1_000)
    new = touch(tmp_path / "docs" / "flow.mmd", 3_000)
    mid = touch(tmp_path / "notes.markdown", 2_000)
    touch(tmp_path / "main.py", 4_000)
    assert pane.find_documents(tmp_path, run=no_git) == [new, mid, old]


def test_walk_skips_hidden_and_dependency_folders_and_deep_files(tmp_path):
    keep = touch(tmp_path / "a" / "b" / "c" / "keep.md", 1_000)
    touch(tmp_path / ".git" / "x.md", 1_000)
    touch(tmp_path / "node_modules" / "pkg" / "README.md", 1_000)
    touch(tmp_path / ".venv" / "y.md", 1_000)
    touch(tmp_path / "a" / "b" / "c" / "d" / "e" / "deep.md", 1_000)
    assert pane.find_documents(tmp_path, run=no_git) == [keep]


def test_uses_git_to_respect_gitignore(tmp_path):
    tracked = touch(tmp_path / "README.md", 1_000)
    touch(tmp_path / "ignored.md", 2_000)
    seen = {}

    def git(cmd, **kwargs):
        seen["cmd"] = cmd
        # git lists README.md and a file that was deleted since.
        return subprocess.CompletedProcess(cmd, 0, stdout="README.md\0gone.md\0", stderr="")

    assert pane.find_documents(tmp_path, run=git) == [tracked]
    assert seen["cmd"][:3] == ["git", "-C", str(tmp_path)]
    assert "--exclude-standard" in seen["cmd"]


def test_falls_back_to_walking_outside_git(tmp_path):
    doc = touch(tmp_path / "README.md", 1_000)
    not_a_repo = lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal")
    assert pane.find_documents(tmp_path, run=not_a_repo) == [doc]


def test_keeps_only_the_newest_files(tmp_path):
    for i in range(5):
        touch(tmp_path / f"{i}.md", 1_000 + i)
    found = pane.find_documents(tmp_path, limit=2, run=no_git)
    assert [p.name for p in found] == ["4.md", "3.md"]


def test_short_folder_keeps_the_last_two_parts():
    home = Path("/Users/me")

    def native(text: str) -> str:
        # Folders show with the OS separator: ~\…\herdr\agent-title on Windows.
        return text.replace("/", os.sep)

    assert pane.short_folder(Path("/Users/me/work/herdr/agent-title"), home) == native("~/…/herdr/agent-title")
    assert pane.short_folder(Path("/Users/me/app"), home) == native("~/app")
    assert pane.short_folder(Path("/srv/a/b/c"), home) == native("/…/b/c")
    assert pane.short_folder(Path("/srv"), home) == native("/srv")


def test_a_hung_herdr_or_git_does_not_hang_or_crash(tmp_path):
    seen = []

    def hung(cmd, **kwargs):
        seen.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout") or 0)

    assert pane.source_of("herdr", "w1:p1", run=hung) is None
    assert pane.ask_to_draw("herdr", pane.Source("w1:p1", agent="claude"), run=hung) == (
        False, "Could not reach claude",
    )
    doc = touch(tmp_path / "README.md", 1_000)
    assert pane.find_documents(tmp_path, run=hung) == [doc]
    assert all(seen)  # every call had a timeout


def test_an_agent_title_that_is_just_the_product_name_is_not_a_task():
    # A fresh Claude Code titles its terminal "Claude Code"; that says nothing.
    for title in ("Claude Code", "claude", "Codex", "OpenAI Codex"):
        run = completed(pane_json(agent="claude", cwd="/w", terminal_title_stripped=title))
        assert pane.source_of("herdr", "w1:p1", run=run).task is None, title


# --- sending any prompt, and starting an agent -------------------------------------------

def test_send_prompt_submits_the_text():
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        return completed("{}")(cmd)

    assert pane.send_prompt("herdr", pane.Source("w1:p1", agent="claude"), "hello", run=run) == (True, "Sent to claude")
    assert seen["cmd"] == ["herdr", "agent", "prompt", "w1:p1", "hello"]


def process_info(names, shell_pid=10, group=10):
    procs = [{"name": n, "argv0": n, "pid": 10 + i} for i, n in enumerate(names)]
    return json.dumps({"result": {"process_info": {
        "foreground_processes": procs, "shell_pid": shell_pid, "foreground_process_group_id": group,
    }}})


def test_an_idle_shell_is_a_place_to_start_an_agent():
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        return completed(process_info(["zsh"]))(cmd)

    assert pane.shell_is_idle("herdr", "w1:p1", run=run)
    assert seen["cmd"] == ["herdr", "pane", "process-info", "--pane", "w1:p1"]
    assert pane.shell_is_idle("herdr", "w1:p1", run=completed(process_info(["-zsh"])))
    assert pane.shell_is_idle("herdr", "w1:p1", run=completed(process_info(["pwsh.exe"])))


def test_a_busy_pane_is_not():
    assert not pane.shell_is_idle("herdr", "w1:p1", run=completed(process_info(["nvim"])))
    assert not pane.shell_is_idle("herdr", "w1:p1", run=completed(process_info(["zsh", "sleep"])))
    # The shell is not in the foreground: something it started is.
    assert not pane.shell_is_idle("herdr", "w1:p1", run=completed(process_info(["zsh"], shell_pid=10, group=99)))
    assert not pane.shell_is_idle("herdr", "w1:p1", run=completed(error_json("pane_not_found"), 1))


def test_start_agent_uses_a_valid_unique_name_and_waits():
    seen = {}

    def run(cmd, **kwargs):
        seen.update(kwargs, cmd=cmd)
        return completed("{}")(cmd)

    ok, _ = pane.start_agent("herdr", pane.Source("w1:p1"), "claude", run=run)
    assert ok
    assert seen["cmd"][:3] == ["herdr", "agent", "start"]
    assert seen["cmd"][3] == "bp-claude-w1-p1"
    assert seen["cmd"][seen["cmd"].index("--kind") + 1] == "claude"
    assert seen["cmd"][seen["cmd"].index("--pane") + 1] == "w1:p1"
    assert seen["timeout"] > 30  # longer than herdr's own wait


def test_start_agent_explains_failures():
    source = pane.Source("w1:p1")
    assert pane.start_agent("herdr", source, "claude", run=completed(error_json("agent_not_ready"), 1)) == (
        False, "claude started but is waiting for input in that pane",
    )
    assert pane.start_agent("herdr", source, "claude", run=completed(error_json("pane_busy"), 1)) == (
        False, "Could not start claude: pane busy",
    )
