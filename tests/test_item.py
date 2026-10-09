from pathlib import Path

from herdr_blueprint.item import Item, looks_like_mermaid


def file_item(name: str, **kwargs) -> Item:
    return Item(kind="file", title=name, path=Path(name), **kwargs)


def test_diagram_detection():
    assert Item(kind="mermaid", title="x", source="graph LR").is_diagram
    assert file_item("flow.MMD").is_diagram
    assert not file_item("notes.md").is_diagram
    assert not Item(kind="markdown", title="x", source="# Hi").is_diagram


def test_draw_flag_is_off_by_default():
    assert not file_item("notes.md").draw
    assert file_item("notes.md", draw=True).draw


def test_mermaid_is_recognised_by_its_first_line():
    for source in [
        "graph LR\n  A --> B",
        "flowchart TD\n  A --> B",
        "  sequenceDiagram\n  A->>B: hi",
        "%% a comment\nerDiagram\n  A ||--o{ B : has",
        "stateDiagram-v2\n  [*] --> A",
    ]:
        assert looks_like_mermaid(source), source


def test_markdown_is_not_mermaid():
    for source in ["# Title\n\ntext", "plain words", "", "graphql is not mermaid"]:
        assert not looks_like_mermaid(source), source


def test_read_replaces_bad_bytes(tmp_path):
    doc = tmp_path / "bad.md"
    doc.write_bytes(b"# Title \xff\xfe end")
    text = Item(kind="file", title="bad.md", path=doc).read()
    assert text.startswith("# Title ")
    assert "�" in text


def test_read_cuts_large_documents_with_notice(tmp_path, monkeypatch):
    monkeypatch.setattr("herdr_blueprint.item.MAX_BYTES", 10)
    doc = tmp_path / "big.md"
    doc.write_text("a" * 50, encoding="utf-8")
    text = Item(kind="file", title="big.md", path=doc).read()
    assert text.startswith("a" * 10)
    assert "a" * 11 not in text
    assert "This file is large" in text


def test_read_cuts_large_diagrams_without_notice(tmp_path, monkeypatch):
    monkeypatch.setattr("herdr_blueprint.item.MAX_BYTES", 10)
    diagram = tmp_path / "big.mmd"
    diagram.write_text("b" * 50, encoding="utf-8")
    assert Item(kind="file", title="big.mmd", path=diagram).read() == "b" * 10


def test_read_replaces_control_characters(tmp_path):
    doc = tmp_path / "log.md"
    doc.write_text("red \x1b[31mtext\x07 end\x9b", encoding="utf-8")
    text = Item(kind="file", title="log.md", path=doc).read()
    assert "\x1b" not in text and "\x07" not in text and "\x9b" not in text
    assert text.count("�") == 3


def test_read_keeps_newlines_and_tabs_and_normalizes_crlf(tmp_path):
    doc = tmp_path / "win.md"
    doc.write_bytes(b"a\r\nb\tc\rd\n")
    assert Item(kind="file", title="win.md", path=doc).read() == "a\nb\tc\nd\n"


def test_snippets_are_cleaned():
    assert "\x1b" not in Item(kind="mermaid", title="x", source="graph LR\n  A[\x1b]0;x\x07] --> B").read()
    assert "\x1b" not in Item(kind="markdown", title="x", source="# Hi \x1b[2J").read()


def test_mermaid_front_matter_and_bom_are_recognised():
    assert looks_like_mermaid("---\ntitle: Order flow\n---\nflowchart LR\n  A --> B")
    assert looks_like_mermaid("﻿graph LR\n  A --> B")
    assert looks_like_mermaid("%%{init: {'theme': 'dark'}}%%\nsequenceDiagram\n  A->>B: hi")


def test_newer_diagram_keywords_are_recognised():
    for first in ["C4Dynamic", "C4Deployment", "radar-beta", "treemap-beta", "flowchart-elk TD"]:
        assert looks_like_mermaid(f"{first}\n  x"), first


def test_prose_that_starts_like_a_keyword_is_not_mermaid():
    for source in ["graph-based search is fast", "block-level elements", "pie-chart ideas", "---\nnot front matter"]:
        assert not looks_like_mermaid(source), source


def test_read_drops_a_utf8_bom(tmp_path):
    doc = tmp_path / "bom.md"
    doc.write_bytes("﻿# Title".encode("utf-8"))
    assert Item(kind="file", title="bom.md", path=doc).read() == "# Title"
