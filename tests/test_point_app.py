"""Point mode: e points at a part, hjkl moves, Enter asks the agent about it."""

import threading
from pathlib import Path

from textual.widgets import Input

from herdr_blueprint import point
from herdr_blueprint.app import BlueprintApp, NoteLine
from herdr_blueprint.config import Config
from herdr_blueprint.document import DiagramFence, DiagramView, DocumentView, PlainView
from herdr_blueprint.item import Item
from herdr_blueprint.sources import inbox
from herdr_blueprint.sources.pane import Source

FLOW = "graph LR\n  A[Browser] --> B[Tollgate]\n  B --> C[Redis]\n"
WIDE = "graph LR\n" + "".join(f"  N{i}[Node number {i}] --> N{i + 1}[Node number {i + 1}]\n" for i in range(5))
FENCED = "# Flow\n\nIntro.\n\n```mermaid\ngraph TD\n  A[Browser] --> B[Tollgate]\n  B --> C[Redis]\n```\n\nAfter.\n"
DOC = "# Login flow\n\nThe app never sees the password.\n\n## Steps\n\n- authorize\n- token\n"


async def wait_for(pilot, condition, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition not met in time")


class FakePrompter:
    def __init__(self, *answers):
        self.answers = list(answers) or [(True, "Sent to claude")]
        self.sent = []

    def __call__(self, source, text):
        self.sent.append((source, text))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class FakeEditor:
    def __init__(self):
        self.seen = []

    def __call__(self, command):
        self.seen.append(command)
        return 0  # quits without saving


def make_app(tmp_path, prompter=None, editor=None, find_editor=None):
    source = Source("w1:p1", agent="claude", cwd=tmp_path)
    return BlueprintApp(
        ask_on_open=False, settings=Config(theme="rose-pine"), source=source, refresher=lambda pane_id: source,
        prompter=prompter or FakePrompter(), editor=editor or FakeEditor(),
        find_editor=find_editor or (lambda: ["/usr/bin/nvim"]),
        idle_shell=lambda pane_id: False, kinds=lambda: [],
    )


def flow_file(tmp_path, text=FLOW, name="flow.mmd") -> Path:
    path = tmp_path / "docs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def open_drawn(app, pilot, path):
    await app.open_item(Item(kind="file", title=path.name, path=path, draw=True))
    view = app.query_one(DiagramView)
    await wait_for(pilot, lambda: view.drawing is not None)
    return view


async def open_fenced(app, pilot, tmp_path, text=FENCED):
    path = tmp_path / "flow.md"
    path.write_text(text, encoding="utf-8")
    await app.open_item(Item(kind="file", title="flow.md", path=path, draw=True))
    fence = app.query_one(DiagramFence)
    await wait_for(pilot, lambda: fence.drawing is not None)
    await pilot.pause()  # let the layout catch up with the drawing
    return fence


# --- entering and moving --------------------------------------------------------------

async def test_e_points_at_the_first_node_of_a_drawn_diagram(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert not app.check_action("edit", ())  # start screen: nothing to point at
        view = await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e")
        assert app.pointing
        assert [p.label for p in app.parts] == ["Browser", "Tollgate", "Redis"]
        assert app.selected == 0 and view.shown == app.parts[0]
        # Only point keys are offered now.
        assert app.check_action("point_ask", ()) and not app.check_action("ask", ())
        assert not app.check_action("pick_theme", ()) and not app.check_action("open_file", ())


async def test_hjkl_move_the_selection_and_the_highlight_follows(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        view = await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "l")
        assert app.selected == 1 and view.shown.label == "Tollgate"
        await pilot.press("l", "h")
        assert app.selected == 1
        await pilot.press("escape")
        assert not app.pointing and view.shown is None


async def test_no_pointing_in_plain_view(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("line\n" * 3000, encoding="utf-8")
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="big.md", path=big))
        assert app.query(PlainView)
        assert not app.check_action("edit", ())


async def test_parts_follow_the_compact_layout(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(70, 30)) as pilot:
        await pilot.pause()
        view = await open_drawn(app, pilot, flow_file(tmp_path, WIDE))
        assert view.drawing.padding != (4, 2, 4)
        await pilot.press("e")
        assert len(app.parts) == 6
        rows = view.drawing.text.plain.split("\n")
        for part in app.parts:
            box = part.boxes[0]
            assert point._char_at(rows, box.x, box.y) != " "


async def test_moving_to_a_part_off_screen_scrolls_it_into_view(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(60, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path, WIDE))
        body = app.query_one("#body")
        await pilot.press("e")
        for _ in range(5):
            await pilot.press("l")
        await pilot.pause()
        assert app.selected == 5
        box, view = app.parts[5].boxes[0], app.query_one(DiagramView)
        left = view.virtual_region.x + box.x
        assert body.scroll_x <= left and left + box.width <= body.scroll_x + body.scrollable_content_region.width


async def test_a_click_after_scrolling_selects_the_node_under_it(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(60, 30)) as pilot:
        await pilot.pause()
        view = await open_drawn(app, pilot, flow_file(tmp_path, WIDE))
        body = app.query_one("#body")
        for _ in range(12):
            await pilot.press("l")  # scroll sideways while reading
        await pilot.pause()
        assert body.scroll_x > 0
        parts = point.diagram_parts(WIDE, view.drawing.padding, view.drawing.text)
        target = next(i for i, p in enumerate(parts) if body.scroll_x <= p.boxes[0].x)
        box, region = parts[target].boxes[0], view.content_region  # on screen, after the scroll
        await pilot.click(offset=(region.x + box.x + 1, region.y + box.y + 1))
        assert app.pointing and app.selected == target


async def test_a_diagram_without_parts_is_pointed_at_whole(tmp_path):
    pie = flow_file(tmp_path, 'pie title Pets\n  "Dogs": 3\n  "Cats": 2\n', "pets.mmd")
    prompter = FakePrompter()
    app = make_app(tmp_path, prompter=prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, pie)
        await pilot.press("e")
        assert [p.kind for p in app.parts] == ["diagram"]
        await pilot.press("enter")
        await pilot.press(*"make it a bar chart")
        await pilot.press("enter")
        await wait_for(pilot, lambda: prompter.sent)
        assert "the whole diagram" in prompter.sent[0][1]


async def test_a_mouse_click_selects_the_node_under_it(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        view = await open_drawn(app, pilot, flow_file(tmp_path))
        box = view.drawing and point.diagram_parts(FLOW, view.drawing.padding, view.drawing.text)[2].boxes[0]
        await pilot.click(DiagramView, offset=(box.x + 2, box.y + 1 + 2))  # +1: the view's top padding
        assert app.pointing and app.selected == 2


async def test_enter_on_a_box_tied_to_code_asks_for_a_code_change(tmp_path):
    linked = FLOW + "  %% code: B src/tollgate.py:12\n"
    prompter = FakePrompter()
    app = make_app(tmp_path, prompter=prompter)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path, linked))
        await pilot.press("e", "l", "enter", *"cache it", "enter")
        await wait_for(pilot, lambda: prompter.sent)
        text = prompter.sent[0][1]
        assert "It stands for `src/tollgate.py:12`." in text and "Make this change in the code" in text


async def test_a_branch_can_be_pointed_at_and_asked_about(tmp_path):
    branchy = "graph TD\n  A[Start] --> B{Cached}\n  B -->|hit| C[Return]\n  B -->|miss| D[Fetch]\n"
    prompter = FakePrompter()
    app = make_app(tmp_path, prompter=prompter)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = await open_drawn(app, pilot, flow_file(tmp_path, branchy))
        await pilot.press("e")
        index = next(i for i, p in enumerate(app.parts) if p.label == "miss")
        app._select(index)
        assert view.shown.kind == "branch"
        await pilot.press("enter", *"log it", "enter")
        await wait_for(pilot, lambda: prompter.sent)
        assert 'the branch "miss" from "Cached" to "Fetch"' in prompter.sent[0][1]


async def test_g_and_shift_g_jump_to_the_first_and_last_part(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path, FLOW.replace("graph LR", "graph TD")))
        await pilot.press("e", "G")
        assert app.pointing and app.parts[app.selected].label == "Redis"
        await pilot.press("g", "g")
        assert app.parts[app.selected].label == "Browser"
        await open_fenced(app, pilot, tmp_path)
        await pilot.press("e", "G")
        assert app.parts[app.selected].label == "After."
        await pilot.press("g")
        assert app.parts[app.selected].label == "Flow"


async def test_ctrl_d_and_ctrl_u_move_the_selection_half_a_screen(tmp_path):
    long = "# Notes\n\n" + "".join(f"Paragraph {i}.\n\n" for i in range(40))
    path = tmp_path / "long.md"
    path.write_text(long, encoding="utf-8")
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="long.md", path=path))
        await pilot.press("e", "ctrl+d")
        body = app.query_one("#body")
        moved = app.selected
        assert 2 <= moved <= 8  # about half of a ~20-row screen, two rows a paragraph
        await pilot.press("ctrl+d")
        assert app.selected > moved and app.pointing
        await pilot.press("ctrl+u", "ctrl+u")
        assert app.selected == 0 and body.scroll_y == 0


# --- diagrams inside a document ------------------------------------------------------

async def test_j_walks_into_a_drawn_diagram_box_by_box_and_out_again(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        fence = await open_fenced(app, pilot, tmp_path)
        await pilot.press("e")
        assert [p.label for p in app.parts] == ["Flow", "Intro.", "Browser", "Tollgate", "Redis", "After."]
        await pilot.press("j", "j")
        assert app.parts[app.selected].label == "Browser" and fence.shown == app.parts[app.selected]
        assert not fence.has_class("pointed")  # the box is highlighted, not the whole block
        await pilot.press("j")
        assert app.parts[app.selected].label == "Tollgate"
        await pilot.press("j", "j")  # Redis, then out below the diagram
        assert app.parts[app.selected].label == "After." and fence.shown is None
        await pilot.press("k")
        assert app.parts[app.selected].label == "Redis"  # back in from below: the bottom box


async def test_enter_on_a_box_in_a_document_names_the_box_and_its_line(tmp_path):
    prompter = FakePrompter()
    app = make_app(tmp_path, prompter=prompter)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await open_fenced(app, pilot, tmp_path)
        await pilot.press("e", "j", "j", "j", "enter", *"cache it", "enter")
        await wait_for(pilot, lambda: prompter.sent)
        text = prompter.sent[0][1]
        assert 'the box "Tollgate" in the diagram under "Flow"' in text
        assert "`flow.md`, line 7:" in text and "cache it" in text


async def test_a_click_on_a_box_in_a_document_selects_it(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        fence = await open_fenced(app, pilot, tmp_path)
        box = point.diagram_parts(fence.code, fence.drawing.padding, fence.drawing.text)[2].boxes[0]
        region = fence.query_one("#code-content").content_region
        await pilot.click(offset=(region.x + box.x + 1, region.y + box.y + 1))
        assert app.pointing and app.parts[app.selected].label == "Redis"


async def test_moving_past_the_edge_of_a_wide_diagram_scrolls_it(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(60, 30)) as pilot:
        await pilot.pause()
        fence = await open_fenced(app, pilot, tmp_path, "# Wide\n\n```mermaid\n" + WIDE + "```\n")
        await pilot.press("e", "j")
        for _ in range(5):
            await pilot.press("l")
        await pilot.pause()
        part = app.parts[app.selected]
        assert part.label == "Node number 5" and fence.scroll_x > 0
        left = fence.query_one("#code-content").content_region.x + part.boxes[0].x
        assert fence.region.x <= left and left + part.boxes[0].width <= fence.region.right


async def test_a_redraw_keeps_the_box_selected(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        fence = await open_fenced(app, pilot, tmp_path)
        await pilot.press("e", "j", "j", "j")
        before = fence.drawing
        app.theme = "bp-nord"
        await wait_for(pilot, lambda: fence.drawing is not before)
        await pilot.pause()
        assert app.parts[app.selected].label == "Tollgate" and fence.shown.label == "Tollgate"
        # ...and it is still drawn highlighted.
        assert any("reverse" in repr(span.style) for span in fence._highlighted_code.spans)


async def test_e_on_a_box_in_a_document_opens_the_editor_on_its_line(tmp_path):
    editor = FakeEditor()
    app = make_app(tmp_path, editor=editor)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await open_fenced(app, pilot, tmp_path)
        await pilot.press("e", "j", "j", "j", "j", "e")  # Redis
        await wait_for(pilot, lambda: editor.seen)
        assert "+8" in editor.seen[0]


# --- asking ----------------------------------------------------------------------------------

async def test_enter_asks_with_the_part_its_line_and_the_file(tmp_path):
    prompter = FakePrompter()
    app = make_app(tmp_path, prompter=prompter)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "l", "enter")
        assert isinstance(app.focused, Input) and app.query(NoteLine)
        await pilot.press(*"cache it for 30 seconds")
        await pilot.press("enter")
        await wait_for(pilot, lambda: prompter.sent)
        text = prompter.sent[0][1]
        assert 'the box "Tollgate"' in text and "`docs/flow.mmd`" in text
        assert "line 2" in text and "cache it for 30 seconds" in text
        assert not app.pointing and not app.query(NoteLine)
        await wait_for(pilot, lambda: app.notice == 'Asked claude about the box "Tollgate"')
        assert app._kept == {}  # delivered: the next Enter starts empty


async def test_escape_steps_back_from_the_note_then_leaves(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "enter")
        await pilot.press(*"half typed")
        await pilot.press("escape")
        assert app.pointing and not app.query(NoteLine)
        await pilot.press("enter")
        assert app.query_one(NoteLine).value == "half typed"  # the words were kept
        await pilot.press("escape", "escape")
        assert not app.pointing


async def test_a_send_while_typing_a_note_waits_until_the_note_closes(tmp_path):
    # The agent redraws after each piece of work; it must not swap the screen mid-sentence.
    app = make_app(tmp_path)
    path = flow_file(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        await open_drawn(app, pilot, path)
        await pilot.press("e", "l", "enter")
        await pilot.press(*"rename it")
        inbox.send({"kind": "text", "source": "# Other", "title": "Other"})
        await pilot.pause(0.6)
        assert app.current.title == "flow.mmd" and app.query_one(NoteLine).value == "rename it"
        await pilot.press("escape")  # the note is kept; now the send shows
        await wait_for(pilot, lambda: app.current and app.current.title == "Other")
        assert not app.pointing and not app.query(NoteLine)
        await open_drawn(app, pilot, path)
        await pilot.press("e", "l", "enter")
        assert app.query_one(NoteLine).value == "rename it"


async def test_a_kept_note_moves_to_the_next_version_of_the_same_note(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        await app.open_item(Item(kind="mermaid", title="Token flow", source=FLOW, draw=True))
        view = app.query_one(DiagramView)
        await wait_for(pilot, lambda: view.drawing is not None)
        await pilot.press("e", "l", "enter", *"cache it", "escape", "escape")
        inbox.send({"kind": "mermaid", "source": FLOW + "  C --> D[Disk]\n", "title": "Token flow", "draw": True})
        await wait_for(pilot, lambda: app.current.source != FLOW)
        assert "dropped" not in (app.notice or "")
        view = app.query_one(DiagramView)
        await wait_for(pilot, lambda: view.drawing is not None)
        await pilot.press("e", "l", "enter")
        assert app.query_one(NoteLine).value == "cache it"


async def test_a_note_open_on_a_diagram_block_keeps_its_target_when_the_drawing_lands(tmp_path):
    prompter = FakePrompter()
    app = make_app(tmp_path, prompter=prompter)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        fence = await open_fenced(app, pilot, tmp_path)
        drawing, fence.drawing = fence.drawing, None  # as if termaid were still drawing
        await pilot.press("e", "j", "j")
        assert app.parts[app.selected].kind == "diagram"
        await pilot.press("enter", *"redo it")
        fence.drawing = drawing
        fence.post_message(DiagramFence.Drawn(fence))
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: prompter.sent)
        assert "the diagram block" in prompter.sent[0][1]


async def test_document_blocks_walk_with_j_and_k_and_ask_with_lines(tmp_path):
    doc = tmp_path / "notes.md"
    doc.write_text(DOC, encoding="utf-8")
    prompter = FakePrompter()
    app = make_app(tmp_path, prompter=prompter)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="notes.md", path=doc))
        await pilot.press("e")
        assert [p.kind for p in app.parts] == ["heading", "paragraph", "heading", "list"]
        await pilot.press("j", "j", "j")
        assert app.selected == 3
        blocks = [b for b in app.query_one(DocumentView).children if hasattr(b, "source_range")]
        assert blocks[3].has_class("pointed") and not blocks[0].has_class("pointed")
        await pilot.press("k", "enter")
        await pilot.press(*"call it Flow")
        await pilot.press("enter")
        await wait_for(pilot, lambda: prompter.sent)
        assert 'the heading "Steps"' in prompter.sent[0][1] and "line 5" in prompter.sent[0][1]


async def test_pointing_is_locked_while_asking(tmp_path):
    release = threading.Event()

    def slow(source, text):
        release.wait(5)
        return True, "Sent to claude"

    app = make_app(tmp_path, prompter=slow)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "enter")
        await pilot.press(*"x")
        await pilot.press("enter")
        await wait_for(pilot, lambda: not app.check_action("edit", ()))
        await pilot.press("e")
        assert not app.pointing
        release.set()
        await wait_for(pilot, lambda: app.check_action("edit", ()))


async def test_a_failed_note_on_sent_text_that_was_replaced_is_dropped(tmp_path):
    release = threading.Event()

    def slow_fail(source, text):
        release.wait(5)
        return False, "Could not reach claude"

    app = make_app(tmp_path, prompter=slow_fail)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        await app.open_item(Item(kind="mermaid", title="Login", source=FLOW, draw=True))
        await wait_for(pilot, lambda: app.query_one(DiagramView).drawing is not None)
        await pilot.press("e", "l", "enter", *"rename it", "enter")
        await wait_for(pilot, lambda: app._sending)
        inbox.send({"kind": "text", "source": "# Other", "title": "Other"})
        await wait_for(pilot, lambda: app.current.title == "Other")
        release.set()
        await wait_for(pilot, lambda: not app._sending)
        assert app.notice == "Could not reach claude. Your note on 'Login' was not sent and was dropped"
        assert app._kept == {}


async def test_a_failed_note_on_a_file_no_longer_shown_says_where_to_find_it(tmp_path):
    release = threading.Event()

    def slow_fail(source, text):
        release.wait(5)
        return False, "Could not reach claude"

    other = tmp_path / "other.md"
    other.write_text("# Other\n", encoding="utf-8")
    app = make_app(tmp_path, prompter=slow_fail)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "l", "enter", *"rename it", "enter")
        await wait_for(pilot, lambda: app._sending)
        await app.open_item(Item(kind="file", title="other.md", path=other))
        release.set()
        await wait_for(pilot, lambda: not app._sending)
        assert app.notice == "Could not reach claude. Your note was kept; open flow.mmd again, point at it and press Enter"
        assert len(app._kept) == 1


async def test_ctrl_q_quits_while_pointing(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "q")
        assert app.pointing and app.is_running  # q is not a point-mode key
        await pilot.press("ctrl+q")
        await pilot.pause(0.3)
        assert not app.is_running


# --- editing the text from a part ----------------------------------------------------------

async def test_e_on_a_document_with_nothing_to_point_at_edits_it(tmp_path):
    for name, body in [("empty.md", ""), ("comment.md", "<!-- todo -->\n")]:
        editor = FakeEditor()
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        app = make_app(tmp_path, editor=editor)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await app.open_item(Item(kind="file", title=name, path=path))
            assert app.check_action("edit", ())
            await pilot.press("e")
            await wait_for(pilot, lambda: editor.seen)
            assert not app.pointing and editor.seen[0][-1].endswith(name)


async def test_e_while_pointing_opens_the_editor_on_the_parts_line(tmp_path):
    editor = FakeEditor()
    app = make_app(tmp_path, editor=editor)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "l", "l", "e")
        await wait_for(pilot, lambda: editor.seen)
        command = editor.seen[0]
        assert command[0] == "/usr/bin/nvim" and "+3" in command and command[-1].endswith("flow.mmd")
        assert not app.pointing


async def test_a_click_while_the_note_is_open_does_not_move_it(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        view = await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "enter", *"about Browser")
        box = app.parts[2].boxes[0]
        await pilot.click(DiagramView, offset=(box.x + 2, box.y + 1 + 2))
        assert app.selected == 0 and app.query_one(NoteLine).value == "about Browser"


async def test_a_kept_note_stays_with_its_block_when_the_file_changes(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n\nFirst paragraph.\n\nSecond paragraph.\n", encoding="utf-8")
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="notes.md", path=path))
        await pilot.press("e", "j", "enter", *"shorter", "escape", "escape")
        path.write_text("# Notes\n\nNew opening.\n\nFirst paragraph.\n\nSecond paragraph.\n", encoding="utf-8")
        await pilot.press("r")
        await pilot.pause(0.2)
        await pilot.press("e", "j", "enter")  # the same position, now "New opening."
        assert app.query_one(NoteLine).value == ""
        await pilot.press("escape", "j", "enter")  # "First paragraph." again
        assert app.query_one(NoteLine).value == "shorter"


async def test_a_diagram_that_cannot_be_drawn_is_tried_once(tmp_path, monkeypatch):
    from herdr_blueprint import document
    from herdr_blueprint.diagram import DiagramError

    calls = []

    def failing(*args, **kwargs):
        calls.append(1)
        raise DiagramError("unsupported")

    monkeypatch.setattr(document, "draw_diagram", failing)
    monkeypatch.setattr(document, "render_diagram", failing)
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="flow.mmd", path=flow_file(tmp_path), draw=True))
        await wait_for(pilot, lambda: calls)
        await pilot.pause(0.3)
        assert len(calls) == 1


async def test_c_opens_the_code_behind_the_selected_box(tmp_path):
    code = tmp_path / "src" / "tollgate.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("x\n" * 20, encoding="utf-8")
    opened = []
    app = make_app(tmp_path)
    app._code_pane = lambda command, path: opened.append((command, path)) or "w1:pC"
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path, FLOW + "  %% code: B src/tollgate.py:12\n"))
        await pilot.press("e")
        assert not app.check_action("jump_code", ())  # Browser has no code line
        await pilot.press("l", "c")
        await wait_for(pilot, lambda: app.notice == "Opened src/tollgate.py:12")
        assert opened == [(["/usr/bin/nvim", "+12"], code)] and app.pointing and not app.editing


async def test_c_says_when_the_code_is_not_there(tmp_path):
    app = make_app(tmp_path)
    app._code_pane = lambda command, path: "w1:pC"
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path, FLOW + "  %% code: B src/gone.py:3\n"))
        await pilot.press("e", "l", "c")
        assert app.notice == "Cannot find src/gone.py"
