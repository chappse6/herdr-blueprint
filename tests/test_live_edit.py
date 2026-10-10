"""Editing with a live preview: the editor runs in a pane below Blueprint, which
redraws from the copy on every save and acts when the editor's exit code appears."""

import json
from pathlib import Path

from herdr_blueprint import edit
from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
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
        await wait_for(pilot, lambda: "kept" in (app.notice or ""), seconds=8)
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
