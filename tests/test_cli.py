import io
import json

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
