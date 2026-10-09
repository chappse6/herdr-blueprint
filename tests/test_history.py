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
