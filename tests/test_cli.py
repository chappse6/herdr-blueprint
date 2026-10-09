import io
import json
import subprocess

from herdr_blueprint import cli
from herdr_blueprint.sources import inbox


def messages():
    return [json.loads(p.read_text()) for p in sorted(inbox.inbox_dir().glob("*.json"))]


def test_show_sends_absolute_path(tmp_path, capsys):
    doc = tmp_path / "flow.md"
    doc.write_text("# Flow", encoding="utf-8")
    assert cli.main(["show", str(doc)]) == 0
    assert messages()[0]["path"] == str(doc.resolve())
    assert "flow.md" in capsys.readouterr().out


def test_show_rejects_missing_and_unsupported_files(tmp_path):
    assert cli.main(["show", str(tmp_path / "missing.md")]) == 1
    other = tmp_path / "main.py"
    other.write_text("", encoding="utf-8")
    assert cli.main(["show", str(other)]) == 2
    assert messages() == []


def test_draw_reads_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("graph LR\n  A --> B\n"))
    assert cli.main(["draw", "--title", "Flow"]) == 0
    sent = messages()[0]
    assert sent["kind"] == "mermaid"
    assert sent["title"] == "Flow"
    assert sent["source"].startswith("graph LR")


def test_draw_rejects_empty_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("  \n"))
    assert cli.main(["draw"]) == 2


def test_detect_root_reads_herdr_context(tmp_path, monkeypatch):
    context = {"workspace": {"id": "w1"}, "pane": {"cwd": "/nope", "foreground_cwd": str(tmp_path)}}
    monkeypatch.setenv("HERDR_PLUGIN_CONTEXT_JSON", json.dumps(context))
    assert cli.detect_root() == tmp_path


def test_detect_root_falls_back_to_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("HERDR_PLUGIN_CONTEXT_JSON", raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli.detect_root() == tmp_path


def completed(stdout: str, code: int = 0):
    return lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr="")


def test_open_command_targets_pane_and_passes_root(tmp_path):
    cmd = cli.open_command("herdr", "w1:p1", tmp_path)
    assert cmd[:4] == ["herdr", "plugin", "pane", "open"]
    assert cmd[cmd.index("--placement") + 1] == "split"
    assert cmd[cmd.index("--direction") + 1] == "right"
    assert cmd[cmd.index("--target-pane") + 1] == "w1:p1"
    assert cmd[cmd.index("--env") + 1] == f"HERDR_BLUEPRINT_ROOT={tmp_path}"
    assert "--no-focus" in cmd


def test_open_command_without_pane_or_root():
    cmd = cli.open_command("herdr", None, None)
    assert "--target-pane" not in cmd
    assert "--env" not in cmd


def test_focused_pane_reads_foreground_cwd(tmp_path):
    out = json.dumps({"result": {"pane": {"pane_id": "w1:p1", "cwd": "/nope", "foreground_cwd": str(tmp_path)}}})
    assert cli.focused_pane("herdr", None, run=completed(out)) == ("w1:p1", tmp_path)


def test_focused_pane_survives_herdr_errors():
    assert cli.focused_pane("herdr", "w1:p1", run=completed("", code=1)) == ("w1:p1", None)
    assert cli.focused_pane("herdr", "w1:p1", run=completed("not json")) == ("w1:p1", None)


def test_focused_pane_survives_missing_herdr():
    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    assert cli.focused_pane("herdr", None, run=missing) == (None, None)


def test_detect_root_prefers_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_BLUEPRINT_ROOT", str(tmp_path))
    monkeypatch.setenv("HERDR_PLUGIN_CONTEXT_JSON", json.dumps({"cwd": "/"}))
    assert cli.detect_root() == tmp_path
