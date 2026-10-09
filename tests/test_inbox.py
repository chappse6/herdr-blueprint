import time

from herdr_blueprint.item import Item
from herdr_blueprint.sources import inbox
from herdr_blueprint.sources.inbox import Refresh


def test_text_messages_become_mermaid_or_markdown():
    diagram = inbox.receive(inbox.send({"kind": "text", "source": "graph LR\n  A --> B", "title": "Flow"}))
    doc = inbox.receive(inbox.send({"kind": "text", "source": "# Notes\n\nhello", "title": "Notes"}))
    assert (diagram.kind, diagram.title) == ("mermaid", "Flow")
    assert (doc.kind, doc.title) == ("markdown", "Notes")


def test_draw_flag_travels_with_the_message():
    drawn = inbox.receive(inbox.send({"kind": "file", "path": "/tmp/x.md", "draw": True}))
    plain = inbox.receive(inbox.send({"kind": "file", "path": "/tmp/x.md"}))
    assert drawn.draw is True
    assert plain.draw is False


def test_only_a_real_true_turns_drawing_on():
    item = inbox.receive(inbox.send({"kind": "text", "source": "graph LR\n  A --> B", "draw": "yes"}))
    assert item.draw is False


def test_refresh_messages():
    assert inbox.receive(inbox.send({"kind": "refresh"})) == Refresh(draw=False)
    assert inbox.receive(inbox.send({"kind": "refresh", "draw": True})) == Refresh(draw=True)


def test_send_leaves_no_temp_files():
    path = inbox.send({"kind": "file", "path": "/tmp/x.md"})
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_broken_message_is_skipped_and_deleted():
    folder = inbox.inbox_dir()
    folder.mkdir(parents=True)
    bad = folder / "1-bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert inbox.receive(bad) is None
    assert not bad.exists()


def test_unknown_kind_is_skipped():
    path = inbox.send({"kind": "image", "path": "/tmp/x.png"})
    assert inbox.receive(path) is None


def test_workspace_id_is_made_safe_for_folders():
    assert inbox.inbox_dir("w1:p2").name == "w1_p2"


def test_a_message_is_received_only_once():
    path = inbox.send({"kind": "text", "source": "graph LR\n  A --> B"})
    assert inbox.receive(path) is not None
    assert inbox.receive(path) is None


def test_old_messages_are_dropped():
    path = inbox.send({"kind": "text", "source": "graph LR\n  A --> B", "sent_at": 1000})
    assert inbox.receive(path, now=1000 + inbox.MAX_AGE_SECONDS + 1) is None
    assert not path.exists()


def test_recent_messages_are_kept():
    path = inbox.send({"kind": "text", "source": "graph LR\n  A --> B", "sent_at": 1000})
    assert inbox.receive(path, now=1000 + 60) is not None


def test_bad_sent_at_is_treated_as_now():
    path = inbox.send({"kind": "text", "source": "graph LR\n  A --> B", "sent_at": "soon"})
    item = inbox.receive(path)
    assert isinstance(item, Item)
    assert abs(item.at - time.time()) < 5


def test_skipped_messages_are_logged(caplog):
    folder = inbox.inbox_dir()
    folder.mkdir(parents=True)
    bad = folder / "1-bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="herdr_blueprint.inbox"):
        inbox.receive(bad)
    assert "1-bad.json" in caplog.text


async def test_on_start_only_the_newest_waiting_item_is_shown():
    inbox.send({"kind": "text", "source": "# First", "title": "First"})
    inbox.send({"kind": "refresh"})
    inbox.send({"kind": "text", "source": "# Second", "title": "Second"})
    messages = inbox.watch_inbox()
    item = await anext(messages)
    await messages.aclose()
    assert item.title == "Second"
    assert list(inbox.inbox_dir().glob("*.json")) == []
