from pathlib import Path

from herdr_blueprint.history import History, Item


def file_item(name: str) -> Item:
    return Item(kind="file", title=name, path=Path(name))


def test_push_selects_newest():
    history = History()
    history.push(file_item("a.md"))
    history.push(file_item("b.md"))
    assert history.current.title == "b.md"


def test_same_file_moves_to_end():
    history = History()
    for name in ["a.md", "b.md", "a.md"]:
        history.push(file_item(name))
    assert [i.title for i in history.items] == ["b.md", "a.md"]


def test_push_without_focus_keeps_current():
    history = History()
    history.push(file_item("a.md"))
    history.push(file_item("b.md"), focus=False)
    assert history.current.title == "a.md"


def test_back_and_forward_stop_at_the_ends():
    history = History()
    for name in ["a.md", "b.md"]:
        history.push(file_item(name))
    assert history.back().title == "a.md"
    assert history.back().title == "a.md"
    assert history.forward().title == "b.md"
    assert history.forward().title == "b.md"


def test_limit_drops_oldest():
    history = History(limit=2)
    for name in ["a.md", "b.md", "c.md"]:
        history.push(file_item(name))
    assert [i.title for i in history.items] == ["b.md", "c.md"]


def test_diagram_detection():
    assert Item(kind="mermaid", title="x", source="graph LR").is_diagram
    assert file_item("flow.MMD").is_diagram
    assert not file_item("notes.md").is_diagram


def test_read_replaces_bad_bytes(tmp_path):
    doc = tmp_path / "bad.md"
    doc.write_bytes(b"# Title \xff\xfe end")
    text = Item(kind="file", title="bad.md", path=doc).read()
    assert text.startswith("# Title ")
    assert "�" in text


def test_read_cuts_large_documents_with_notice(tmp_path, monkeypatch):
    monkeypatch.setattr("herdr_blueprint.history.MAX_BYTES", 10)
    doc = tmp_path / "big.md"
    doc.write_text("a" * 50, encoding="utf-8")
    text = Item(kind="file", title="big.md", path=doc).read()
    assert text.startswith("a" * 10)
    assert "a" * 11 not in text
    assert "This file is large" in text


def test_read_cuts_large_diagrams_without_notice(tmp_path, monkeypatch):
    monkeypatch.setattr("herdr_blueprint.history.MAX_BYTES", 10)
    diagram = tmp_path / "big.mmd"
    diagram.write_text("b" * 50, encoding="utf-8")
    assert Item(kind="file", title="big.mmd", path=diagram).read() == "b" * 10
