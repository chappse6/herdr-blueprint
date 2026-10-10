"""Edit mode in the app: e opens a copy, saving sends the change, quitting does nothing.

No real editor, herdr or agent: each is a fake passed to the app.
"""

import threading
from pathlib import Path

from textual.widgets import OptionList

from herdr_blueprint.app import AgentPicker, BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.item import MAX_BYTES, Item
from herdr_blueprint.sources.pane import Source


async def wait_for(pilot, condition, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition not met in time")


class FakeEditor:
    """Stands in for vim: optionally rewrites the copy, then exits with `code`."""

    def __init__(self, change=None, code=0, during=None):
        self.change, self.code, self.during = change, code, during
        self.seen = []  # (command, copy path, text it opened with)

    def __call__(self, command):
        copy = Path(command[-1])
        self.seen.append((command, copy, copy.read_text(encoding="utf-8")))
        if self.during:
            self.during()
        if self.change:
            copy.write_text(self.change(copy.read_text(encoding="utf-8")), encoding="utf-8")
        return self.code


class FakePrompter:
    def __init__(self, *answers):
        self.answers = list(answers) or [(True, "Sent to claude")]
        self.sent = []  # (source, text)

    def __call__(self, source, text):
        self.sent.append((source, text))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def make_app(tmp_path, *, editor, prompter=None, source="agent", refresher=None, **fakes):
    if source == "agent":
        source = Source("w1:p1", agent="claude", cwd=tmp_path)
    return BlueprintApp(
        settings=Config(theme="rose-pine"),
        source=source,
        refresher=refresher or (lambda pane_id: source),
        editor=editor,
        find_editor=lambda: ["fake-editor"],
        prompter=prompter or FakePrompter(),
        starter=fakes.get("starter", lambda src, kind: (True, f"Started {kind}")),
        idle_shell=fakes.get("idle_shell", lambda pane_id: False),
        kinds=fakes.get("kinds", lambda: ["claude"]),
    )


def doc(tmp_path, text="# Flow\n\nold line\n") -> Path:
    path = tmp_path / "docs" / "flow.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def file_item(path: Path) -> Item:
    return Item(kind="file", title=path.name, path=path)


def change_line(text: str) -> str:
    return text.replace("old line", "new line")


# --- when e is offered --------------------------------------------------------------------

async def test_e_is_hidden_on_the_start_screen_and_shown_for_a_document(tmp_path):
    app = make_app(tmp_path, editor=FakeEditor())
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert not app.check_action("edit", ())
        await app.open_item(file_item(doc(tmp_path)))
        assert app.check_action("edit", ())


async def test_a_file_over_the_limit_is_not_editable(tmp_path):
    big = tmp_path / "big.md"
    big.write_bytes(b"x" * (MAX_BYTES + 10))
    app = make_app(tmp_path, editor=FakeEditor())
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(big))
        assert not app.check_action("edit", ())


# --- the editor's exit decides -------------------------------------------------------------

async def test_quitting_without_a_change_sends_nothing(tmp_path):
    path = doc(tmp_path)
    editor, prompter = FakeEditor(), FakePrompter()
    app = make_app(tmp_path, editor=editor, prompter=prompter)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(path))
        await pilot.press("e")
        await wait_for(pilot, lambda: app.notice == "No changes")
        assert prompter.sent == []
        assert editor.seen[0][2] == "# Flow\n\nold line\n"  # it opened the file's text


async def test_an_editor_error_exit_cancels_even_with_a_change(tmp_path):
    prompter = FakePrompter()
    app = make_app(tmp_path, editor=FakeEditor(change=change_line, code=1), prompter=prompter)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(doc(tmp_path)))
        await pilot.press("e")
        await wait_for(pilot, lambda: app.notice == "Edit cancelled")
        assert prompter.sent == []


async def test_saving_a_change_sends_the_diff_and_leaves_the_file_alone(tmp_path):
    path = doc(tmp_path)
    editor, prompter = FakeEditor(change=change_line), FakePrompter()
    app = make_app(tmp_path, editor=editor, prompter=prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(path))
        await pilot.press("e")
        await wait_for(pilot, lambda: app.notice == "Sent your edit to claude")
        (source, text), = prompter.sent
        assert source.pane_id == "w1:p1"
        assert text.startswith("I edited docs/flow.md in Blueprint.")
        assert "```diff" in text and "-old line" in text and "+new line" in text
        assert path.read_text(encoding="utf-8") == "# Flow\n\nold line\n"  # Blueprint never writes it
        command, copy, _ = editor.seen[0]
        assert command[0] == "fake-editor" and copy.name == "flow.md"
        assert not copy.exists() and not copy.parent.exists()  # the copy is gone


async def test_an_edited_snippet_sends_the_new_version(tmp_path):
    prompter = FakePrompter()
    app = make_app(tmp_path, editor=FakeEditor(change=lambda t: t.replace("B", "C")), prompter=prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="Flow", source="graph LR\n  A --> B\n"))
        await pilot.press("e")
        await wait_for(pilot, lambda: prompter.sent)
        text = prompter.sent[0][1]
        assert "the diagram you sent ('Flow')" in text
        assert "```mermaid\ngraph LR\n  A --> C\n```" in text


async def test_a_file_changed_while_editing_is_mentioned(tmp_path):
    path = doc(tmp_path)
    prompter = FakePrompter()
    touch = lambda: path.write_text("# Flow\n\nold line\nagent added\n", encoding="utf-8")
    app = make_app(tmp_path, editor=FakeEditor(change=change_line, during=touch), prompter=prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(path))
        await pilot.press("e")
        await wait_for(pilot, lambda: prompter.sent)
        assert "changed on disk" in prompter.sent[0][1]


# --- an edit that could not be sent is kept --------------------------------------------------

async def test_a_failed_send_keeps_the_edit_for_the_next_e(tmp_path):
    editor = FakeEditor(change=change_line)
    prompter = FakePrompter((False, "claude is waiting for your answer"), (True, "Sent to claude"))
    app = make_app(tmp_path, editor=editor, prompter=prompter)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(doc(tmp_path)))
        await pilot.press("e")
        await wait_for(pilot, lambda: app.notice and "press e" in app.notice)
        assert "waiting for your answer" in app.notice
        editor.change = None  # this time just save and quit
        await pilot.press("e")
        await wait_for(pilot, lambda: app.notice == "Sent your edit to claude")
        assert "new line" in editor.seen[1][2]  # reopened the edit, not the file
        assert len(prompter.sent) == 2 and prompter.sent[0][1] == prompter.sent[1][1]


async def test_e_is_unavailable_while_sending(tmp_path):
    release = threading.Event()

    def slow(source, text):
        release.wait(5)
        return True, "Sent to claude"

    app = make_app(tmp_path, editor=FakeEditor(change=change_line), prompter=slow)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(doc(tmp_path)))
        await pilot.press("e")
        await wait_for(pilot, lambda: not app.check_action("edit", ()))
        release.set()
        await wait_for(pilot, lambda: app.check_action("edit", ()))


async def test_outside_herdr_the_edit_is_kept(tmp_path):
    prompter = FakePrompter()
    app = make_app(tmp_path, editor=FakeEditor(change=change_line), prompter=prompter, source=None)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(doc(tmp_path)))
        await pilot.press("e")
        await wait_for(pilot, lambda: app.notice and "No agent next to Blueprint" in app.notice)
        assert prompter.sent == []


# --- no agent next to Blueprint --------------------------------------------------------------

async def test_a_shell_neighbor_offers_to_start_an_agent(tmp_path):
    neighbor = {"source": Source("w1:p1", cwd=tmp_path)}
    started = []

    def starter(source, kind):
        started.append(kind)
        neighbor["source"] = Source("w1:p1", agent=kind, cwd=tmp_path)
        return True, f"Started {kind}"

    prompter = FakePrompter()
    app = make_app(
        tmp_path, editor=FakeEditor(change=change_line), prompter=prompter,
        source=neighbor["source"], refresher=lambda pane_id: neighbor["source"],
        idle_shell=lambda pane_id: True, kinds=lambda: ["claude", "codex"], starter=starter,
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(doc(tmp_path)))
        await pilot.press("e")
        await wait_for(pilot, lambda: isinstance(app.screen, AgentPicker))
        options = app.screen.query_one(OptionList)
        labels = [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]
        assert labels == ["Start claude", "Start codex", "Don't send"]
        await pilot.press("enter")
        await wait_for(pilot, lambda: app.notice == "Sent your edit to claude")
        assert started == ["claude"] and prompter.sent[0][0].agent == "claude"


async def test_declining_the_start_keeps_the_edit(tmp_path):
    shell = Source("w1:p1", cwd=tmp_path)
    started, prompter = [], FakePrompter()
    app = make_app(
        tmp_path, editor=FakeEditor(change=change_line), prompter=prompter, source=shell,
        idle_shell=lambda pane_id: True, starter=lambda s, k: started.append(k) or (True, ""),
    )
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(doc(tmp_path)))
        await pilot.press("e")
        await wait_for(pilot, lambda: isinstance(app.screen, AgentPicker))
        await pilot.press("escape")
        await wait_for(pilot, lambda: app.notice and "press e" in app.notice)
        assert started == [] and prompter.sent == []
        assert app.check_action("edit", ())


async def test_a_busy_neighbor_is_not_offered_an_agent(tmp_path):
    busy = Source("w1:p1", cwd=tmp_path)  # say, nvim runs there
    app = make_app(tmp_path, editor=FakeEditor(change=change_line), source=busy, idle_shell=lambda pane_id: False)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(doc(tmp_path)))
        await pilot.press("e")
        await wait_for(pilot, lambda: app.notice and "No agent next to Blueprint" in app.notice)
        assert not isinstance(app.screen, AgentPicker)


async def test_a_failed_start_keeps_the_edit(tmp_path):
    shell = Source("w1:p1", cwd=tmp_path)
    app = make_app(
        tmp_path, editor=FakeEditor(change=change_line), source=shell, idle_shell=lambda pane_id: True,
        starter=lambda s, k: (False, "claude started but is waiting for input in that pane"),
    )
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(doc(tmp_path)))
        await pilot.press("e")
        await wait_for(pilot, lambda: isinstance(app.screen, AgentPicker))
        await pilot.press("enter")
        await wait_for(pilot, lambda: app.notice and "waiting for input" in app.notice)
        assert "press e" in app.notice


# --- odd inputs --------------------------------------------------------------------------------

async def test_a_long_diff_goes_to_a_file_that_is_removed_on_close(tmp_path):
    path = doc(tmp_path, "# Flow\n\n" + "old line\n" * 900)
    prompter = FakePrompter()
    app = make_app(tmp_path, editor=FakeEditor(change=change_line), prompter=prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(file_item(path))
        await pilot.press("e")
        await wait_for(pilot, lambda: prompter.sent)
        text = prompter.sent[0][1]
        assert "```diff" not in text
        attachment = Path(text.split("The diff is in ", 1)[1].split(".diff", 1)[0] + ".diff")
        assert attachment.exists() and "+new line" in attachment.read_text(encoding="utf-8")
    assert not attachment.exists()


async def test_a_hostile_title_does_not_crash(tmp_path):
    editor, prompter = FakeEditor(change=lambda t: t + "more\n"), FakePrompter()
    app = make_app(tmp_path, editor=editor, prompter=prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="markdown", title="a" * 400 + '\x00:*?"<>|', source="# Note\n"))
        await pilot.press("e")
        await wait_for(pilot, lambda: prompter.sent)
        assert app.is_running
        name = editor.seen[0][1].name
        assert len(name) <= 64 and name.endswith(".md")
