import time

from herdr_blueprint.sources import inbox


def test_send_then_receive_round_trip():
    path = inbox.send({"kind": "mermaid", "source": "graph LR\n  A --> B", "title": "Flow"})
    item = inbox.receive(path)
    assert item.kind == "mermaid"
    assert item.title == "Flow"
    assert not path.exists()


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
    path = inbox.send({"kind": "mermaid", "source": "graph LR\n  A --> B"})
    assert inbox.receive(path) is not None
    assert inbox.receive(path) is None


def test_old_messages_are_dropped():
    path = inbox.send({"kind": "mermaid", "source": "graph LR\n  A --> B", "sent_at": 1000})
    assert inbox.receive(path, now=1000 + inbox.MAX_AGE_SECONDS + 1) is None
    assert not path.exists()


def test_recent_messages_are_kept():
    path = inbox.send({"kind": "mermaid", "source": "graph LR\n  A --> B", "sent_at": 1000})
    assert inbox.receive(path, now=1000 + 60) is not None


def test_bad_sent_at_is_treated_as_now():
    path = inbox.send({"kind": "mermaid", "source": "graph LR\n  A --> B", "sent_at": "soon"})
    item = inbox.receive(path)
    assert item is not None
    assert abs(item.at - time.time()) < 5


def test_skipped_messages_are_logged(caplog):
    folder = inbox.inbox_dir()
    folder.mkdir(parents=True)
    bad = folder / "1-bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="herdr_blueprint.inbox"):
        inbox.receive(bad)
    assert "1-bad.json" in caplog.text


async def test_messages_sent_before_start_are_delivered():
    inbox.send({"kind": "mermaid", "source": "graph LR\n  A --> B", "title": "Early"})
    messages = inbox.watch_inbox()
    item = await anext(messages)
    await messages.aclose()
    assert item.title == "Early"
