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
