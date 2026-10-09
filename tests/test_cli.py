import io
import json
import subprocess

import pytest

from herdr_blueprint import cli
from herdr_blueprint.sources import inbox


def messages():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(inbox.inbox_dir().glob("*.json"))]


def utf8_stdin(text: str) -> io.TextIOWrapper:
    # A console whose code page is not UTF-8, like Windows cp1252.
    return io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="cp1252")


def completed(stdout: str, code: int = 0):
    return lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr="")


# --- show -------------------------------------------------------------------------

def test_show_sends_absolute_path_without_drawing(tmp_path, capsys):
    doc = tmp_path / "flow.md"
    doc.write_text("# Flow", encoding="utf-8")
    assert cli.main(["show", str(doc)]) == 0
    sent = messages()[0]
    assert (sent["kind"], sent["path"], sent["draw"]) == ("file", str(doc.resolve()), False)
    assert "flow.md" in capsys.readouterr().out


def test_show_draw_flag(tmp_path):
    doc = tmp_path / "flow.mmd"
    doc.write_text("graph LR\n  A --> B", encoding="utf-8")
    assert cli.main(["show", "--draw", str(doc)]) == 0
    assert messages()[0]["draw"] is True


def test_show_rejects_missing_and_unsupported_files(tmp_path):
    assert cli.main(["show", str(tmp_path / "missing.md")]) == 1
    other = tmp_path / "main.py"
    other.write_text("", encoding="utf-8")
    assert cli.main(["show", str(other)]) == 2
    assert messages() == []


# --- send -------------------------------------------------------------------------

def test_send_reads_stdin_as_utf8(monkeypatch):
    monkeypatch.setattr("sys.stdin", utf8_stdin("graph LR\n  A[주문] --> B[결제]\n"))
    assert cli.main(["send", "--title", "Order"]) == 0
    sent = messages()[0]
    assert (sent["kind"], sent["title"], sent["draw"]) == ("text", "Order", False)
    assert "주문" in sent["source"]


def test_send_draw_flag(monkeypatch):
    monkeypatch.setattr("sys.stdin", utf8_stdin("graph LR\n  A --> B\n"))
    assert cli.main(["send", "--draw"]) == 0
    assert messages()[0]["draw"] is True


def test_send_rejects_empty_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", utf8_stdin("  \n"))
    assert cli.main(["send"]) == 2
    assert messages() == []


def test_send_does_not_crash_printing_to_a_narrow_encoding(monkeypatch):
    monkeypatch.setattr("sys.stdin", utf8_stdin("graph LR\n  A --> B\n"))
    monkeypatch.setattr("sys.stdout", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    assert cli.main(["send", "--title", "주문 흐름"]) == 0


def test_the_old_draw_command_is_gone():
    with pytest.raises(SystemExit):
        cli.main(["draw"])


# --- refresh ----------------------------------------------------------------------

def test_refresh_asks_the_viewer_to_redraw(capsys):
    assert cli.main(["refresh"]) == 0
    assert cli.main(["refresh", "--draw"]) == 0
    assert [(m["kind"], m["draw"]) for m in messages()] == [("refresh", False), ("refresh", True)]


# --- open -------------------------------------------------------------------------

def test_open_command_targets_pane_to_the_right_without_focus():
    cmd = cli.open_command("herdr", "w1:p1")
    assert cmd[:4] == ["herdr", "plugin", "pane", "open"]
    assert cmd[cmd.index("--placement") + 1] == "split"
    assert cmd[cmd.index("--direction") + 1] == "right"
    assert cmd[cmd.index("--target-pane") + 1] == "w1:p1"
    assert "--no-focus" in cmd
    assert "--env" not in cmd


def test_open_command_without_pane():
    assert "--target-pane" not in cli.open_command("herdr", None)


def test_focused_pane_id_prefers_the_given_pane():
    def never(cmd, **kwargs):
        raise AssertionError("herdr should not be asked")

    assert cli.focused_pane_id("herdr", "w1:p1", run=never) == "w1:p1"


def test_focused_pane_id_asks_herdr_and_decodes_utf8():
    seen = {}

    def run(cmd, **kwargs):
        seen.update(kwargs, cmd=cmd)
        out = json.dumps({"result": {"pane": {"pane_id": "w2:p3", "terminal_title": "주문"}}})
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    assert cli.focused_pane_id("herdr", None, run=run) == "w2:p3"
    assert seen["cmd"] == ["herdr", "pane", "current"]
    assert (seen["encoding"], seen["errors"]) == ("utf-8", "replace")


def test_focused_pane_id_survives_herdr_problems():
    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    assert cli.focused_pane_id("herdr", None, run=completed("", code=1)) is None
    assert cli.focused_pane_id("herdr", None, run=completed("not json")) is None
    assert cli.focused_pane_id("herdr", None, run=missing) is None


# --- skill ------------------------------------------------------------------------

def test_install_skill_command_uses_home(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    assert cli.main(["install-skill"]) == 0
    assert (home / ".claude/skills/blueprint").exists()
    assert cli.main(["uninstall-skill"]) == 0
    assert not (home / ".claude/skills/blueprint").exists()


def test_send_drops_a_utf8_bom(monkeypatch):
    monkeypatch.setattr("sys.stdin", utf8_stdin("﻿graph LR\n  A --> B\n"))
    assert cli.main(["send", "--draw"]) == 0
    assert messages()[0]["source"].startswith("graph LR")
