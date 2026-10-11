"""Editing with a live preview: the editor runs in a pane below Blueprint, which
redraws from the copy on every save and acts when the editor's exit code appears."""

import json
from pathlib import Path

from herdr_blueprint import edit
from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.document import DiagramView, DocumentView
from herdr_blueprint.item import Item
from herdr_blueprint.sources import inbox
from herdr_blueprint.sources.pane import Source


async def wait_for(pilot, condition, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition not met in time")


class FakePane:
    """Stands in for herdr's editor pane: records the session, lets tests act as the editor."""

    def __init__(self, opens=True):
        self.opens = opens
        self.sessions = []  # (command, copy)
        self.focused = []
        self.alive = True

    def open(self, command, copy):
        if not self.opens:
            return None
        self.sessions.append((command, copy))
        return "w1:pE"

    def focus(self, pane_id):
        self.focused.append(pane_id)

    def exists(self, pane_id):
        return self.alive

    @property
    def copy(self) -> Path:
        return self.sessions[-1][1]

    def save(self, text):
        self.copy.write_text(text, encoding="utf-8")

    def quit(self, code=0):
        edit.exit_file(self.copy).write_text(str(code), encoding="utf-8")


class FlakyPane(FakePane):
    """herdr doesn't answer the first check, while the editor keeps running."""

    def __init__(self):
        super().__init__()
        self.checks = 0

    def exists(self, pane_id):
        self.checks += 1
        return self.checks != 1


class FakePrompter:
    def __init__(self):
        self.sent = []

    def __call__(self, source, text):
        self.sent.append((source, text))
        return True, "Sent to claude"


def make_app(tmp_path, fake, prompter=None, editor=None):
    source = Source("w1:p1", agent="claude", cwd=tmp_path)
    return BlueprintApp(
        settings=Config(theme="rose-pine"), source=source, refresher=lambda pane_id: source,
        prompter=prompter or FakePrompter(), editor=editor or (lambda command: 0),
        find_editor=lambda: ["/usr/bin/nvim"], idle_shell=lambda pane_id: False, kinds=lambda: [],
        pane_editor=fake.open, focus_pane=fake.focus, pane_alive=fake.exists,
    )


FLOW = "graph LR\n  A[Browser] --> B[Tollgate]\n  B --> C[Redis]\n"


def doc(tmp_path, text="# Flow\n\nold line\n") -> Path:
    path = tmp_path / "docs" / "flow.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def start_editing(app, pilot, path, fake):
    await app.open_item(Item(kind="file", title=path.name, path=path))
    await pilot.press("e", "e")
    await wait_for(pilot, lambda: fake.sessions)


async def test_the_editor_opens_in_a_pane_on_the_parts_line(tmp_path):
    fake = FakePane()
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        command, copy = fake.sessions[0]
        assert command == ["/usr/bin/nvim", "+1"] and copy.read_text() == "# Flow\n\nold line\n"
        assert app.editing and "Editing" in app.notice


async def test_each_save_redraws_the_preview(tmp_path):
    fake = FakePane()
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        fake.save("# Flow\n\nnew line\n")
        await wait_for(pilot, lambda: app.preview_text == "# Flow\n\nnew line\n")
        fake.save("# Flow\n\nnewer line\n")
        await wait_for(pilot, lambda: app.preview_text == "# Flow\n\nnewer line\n")


async def test_quitting_after_a_save_sends_the_change(tmp_path):
    fake, prompter = FakePane(), FakePrompter()
    path = doc(tmp_path)
    app = make_app(tmp_path, fake, prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, path, fake)
        fake.save("# Flow\n\nnew line\n")
        fake.quit(0)
        await wait_for(pilot, lambda: prompter.sent)
        assert "+new line" in prompter.sent[0][1]
        assert path.read_text() == "# Flow\n\nold line\n"  # never written by Blueprint
        assert not app.editing and not fake.copy.parent.exists()


async def test_an_error_exit_cancels_and_restores_the_original(tmp_path):
    fake, prompter = FakePane(), FakePrompter()
    app = make_app(tmp_path, fake, prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        fake.save("# Flow\n\nnew line\n")
        await wait_for(pilot, lambda: app.preview_text == "# Flow\n\nnew line\n")
        fake.quit(1)
        await wait_for(pilot, lambda: app.notice == "Edit cancelled")
        assert prompter.sent == [] and app.preview_text is None


async def test_quitting_without_saving_does_nothing(tmp_path):
    fake, prompter = FakePane(), FakePrompter()
    app = make_app(tmp_path, fake, prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        fake.quit(0)
        await wait_for(pilot, lambda: app.notice == "No changes")
        assert prompter.sent == []


async def test_a_pane_closed_by_hand_keeps_saved_work(tmp_path):
    fake, prompter = FakePane(), FakePrompter()
    app = make_app(tmp_path, fake, prompter)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        fake.save("# Flow\n\nnew line\n")
        fake.alive = False  # no exit file will ever come
        await wait_for(pilot, lambda: "kept" in (app.notice or ""), seconds=10)
        assert prompter.sent == [] and not app.editing
        await pilot.press("e", "e")
        await wait_for(pilot, lambda: len(fake.sessions) == 2)
        assert "new line" in fake.copy.read_text()


async def test_sends_during_the_edit_wait_and_are_shown_if_nothing_was_sent(tmp_path):
    fake = FakePane()
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        await start_editing(app, pilot, doc(tmp_path), fake)
        inbox.send({"kind": "text", "source": "# Other", "title": "Other"})
        await pilot.pause(0.5)
        assert app.current.title == "flow.md"  # held
        fake.quit(0)  # nothing saved
        await wait_for(pilot, lambda: app.current and app.current.title == "Other")


async def test_sends_during_an_edit_that_was_sent_are_dropped(tmp_path):
    fake = FakePane()
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        await start_editing(app, pilot, doc(tmp_path), fake)
        inbox.send({"kind": "text", "source": "# Other", "title": "Other"})
        await pilot.pause(0.5)
        fake.save("# Flow\n\nnew line\n")
        fake.quit(0)
        await wait_for(pilot, lambda: not app.editing)
        await pilot.pause(0.3)
        assert app.current.title == "flow.md" and app.preview_text == "# Flow\n\nnew line\n"


async def test_a_save_with_windows_line_endings_previews_like_any_other(tmp_path):
    fake = FakePane()
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        fake.copy.write_text("# Flow\r\n\r\nnew line\r\n", encoding="utf-8", newline="")
        fake.quit(0)
        await wait_for(pilot, lambda: not app.editing)
        await pilot.pause(0.2)
        assert app.preview_text == "# Flow\n\nnew line\n"


async def test_a_second_e_focuses_the_editor_pane(tmp_path):
    fake = FakePane()
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        await pilot.press("e")
        assert fake.focused == ["w1:pE"] and len(fake.sessions) == 1


async def test_without_a_pane_the_editor_borrows_blueprints_pane(tmp_path):
    fake, borrowed = FakePane(opens=False), []
    app = make_app(tmp_path, fake, editor=lambda command: borrowed.append(command) or 0)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="flow.md", path=doc(tmp_path)))
        await pilot.press("e", "e")
        await wait_for(pilot, lambda: borrowed)
        assert not app.editing


async def test_a_click_on_a_diagram_preview_does_not_point(tmp_path):
    fake = FakePane()
    path = tmp_path / "flow.mmd"
    path.write_text(FLOW, encoding="utf-8")
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="flow.mmd", path=path, draw=True))
        await pilot.press("e", "e")
        await wait_for(pilot, lambda: fake.sessions)
        fake.save(FLOW + "  C --> D[Postgres]\n")
        await wait_for(pilot, lambda: app.preview_text is not None)
        await wait_for(pilot, lambda: app.query_one(DiagramView).drawing is not None)
        await pilot.click(DiagramView, offset=(3, 3))
        assert not app.pointing and app.editing


async def test_a_click_on_a_document_preview_does_not_point(tmp_path):
    fake = FakePane()
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        fake.save("# Flow\n\nnew line\n\nmore\n")
        await wait_for(pilot, lambda: app.preview_text is not None)
        await pilot.pause(0.2)
        blocks = [b for b in app.query_one(DocumentView).children if hasattr(b, "source_range")]
        await pilot.click(blocks[1])
        assert not app.pointing and app.editing


async def test_the_preview_of_a_sent_edit_cannot_be_pointed_at(tmp_path):
    fake = FakePane()
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        fake.save("# Flow\n\nIntro added\n\nnew line\n")
        fake.quit(0)
        await wait_for(pilot, lambda: app.notice == "Sent your edit to claude")
        # The file doesn't hold this text yet: wait for the agent's next send.
        assert app.preview_text is not None and not app.check_action("edit", ())
        await pilot.press("e")
        blocks = [b for b in app.query_one(DocumentView).children if hasattr(b, "source_range")]
        await pilot.click(blocks[1])
        assert not app.pointing
        await pilot.press("r")  # back to the file itself
        await wait_for(pilot, lambda: app.preview_text is None)
        assert app.check_action("edit", ())


async def test_after_a_failed_send_e_on_the_preview_edits_it_again(tmp_path):
    fake = FakePane()
    app = make_app(tmp_path, fake, prompter=lambda source, text: (False, "Could not reach claude"))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        fake.save("# Flow\n\nnew line\n")
        fake.quit(0)
        await wait_for(pilot, lambda: (app.notice or "").startswith("Could not reach claude"))
        await pilot.press("e")
        await wait_for(pilot, lambda: len(fake.sessions) == 2)
        assert not app.pointing and fake.copy.read_text() == "# Flow\n\nnew line\n"


async def test_one_unanswered_pane_check_does_not_end_the_edit(tmp_path):
    fake = FlakyPane()
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        await wait_for(pilot, lambda: fake.checks >= 2, seconds=8)
        await pilot.pause(0.2)
        assert app.editing and fake.copy.parent.exists()


async def test_without_file_events_the_edit_still_previews_and_finishes(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("OS file watch limit reached")

    monkeypatch.setattr("watchfiles.awatch", broken)
    fake, prompter = FakePane(), FakePrompter()
    app = make_app(tmp_path, fake, prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await start_editing(app, pilot, doc(tmp_path), fake)
        fake.save("# Flow\n\nnew line\n")
        await wait_for(pilot, lambda: app.preview_text == "# Flow\n\nnew line\n", seconds=8)
        fake.quit(0)
        await wait_for(pilot, lambda: prompter.sent, seconds=8)
        assert "+new line" in prompter.sent[0][1]


async def test_a_held_send_that_replaces_sent_text_reports_the_work_dropped(tmp_path):
    fake = FakePane()
    app = make_app(tmp_path, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        await app.open_item(Item(kind="markdown", title="Plan", source="# Plan\n\nold\n"))
        await pilot.press("e", "e")
        await wait_for(pilot, lambda: fake.sessions)
        fake.save("# Plan\n\nnew\n")
        await wait_for(pilot, lambda: app.preview_text is not None)
        inbox.send({"kind": "text", "source": "# Other", "title": "Other"})
        await pilot.pause(0.5)
        fake.alive = False  # closed by hand: the saved work would be kept, but Plan is gone
        await wait_for(pilot, lambda: not app.editing and app.current.title == "Other", seconds=10)
        await pilot.pause(0.1)
        assert app._kept == {}
        assert app.notice == "Your unsent work on 'Plan' was dropped"
