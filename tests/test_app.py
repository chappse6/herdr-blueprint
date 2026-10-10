import asyncio
import os
import time
from pathlib import Path

from textual.widgets import OptionList, Static

from herdr_blueprint.app import BlueprintApp, ago
from herdr_blueprint.config import Config
from herdr_blueprint.document import DiagramFence, DiagramView, DocumentView, PlainView
from herdr_blueprint.item import Item
from herdr_blueprint.sources import inbox
from herdr_blueprint.sources.pane import Source
from herdr_blueprint.start import FilePicker, StartView


def make_app() -> BlueprintApp:
    return BlueprintApp(settings=Config(theme="rose-pine"))


async def wait_for(pilot, condition, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition not met in time")


async def drawings_done(app) -> None:
    # An empty list would mean "wait for every worker", including the endless inbox one.
    drawing = [worker for worker in app.workers if worker.group == "draw"]
    if drawing:
        await app.workers.wait_for_complete(drawing)


def body_views(app):
    return list(app.query_one("#body").children)


# --- one screen ---------------------------------------------------------------------

async def test_starts_on_a_welcome_screen_with_a_drawn_example():
    app = make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        assert app.theme == "bp-rose-pine"
        assert app.query(DocumentView)
        assert app.query(DiagramFence)
        assert app.current is None


async def test_each_send_replaces_the_screen():
    app = make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause(0.3)
        inbox.send({"kind": "text", "source": "graph LR\n  A --> B", "title": "Flow", "draw": True})
        await wait_for(pilot, lambda: app.current and app.current.title == "Flow")
        inbox.send({"kind": "text", "source": "# Notes", "title": "Notes"})
        await wait_for(pilot, lambda: app.current and app.current.title == "Notes")
        views = body_views(app)
        assert len(views) == 1 and isinstance(views[0], DocumentView)


async def test_there_are_no_tabs_follow_or_history_keys():
    app = make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        assert not app.query("#tabs")
        keys = {binding.key for binding in app.BINDINGS}
        assert not keys & {"f", "left", "right"}


# --- the draw flag ------------------------------------------------------------------

async def test_without_the_flag_mermaid_stays_text(monkeypatch):
    def no_drawing(*args, **kwargs):
        raise AssertionError("termaid must not run without the draw flag")

    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        # The welcome example is always drawn; only the sent item is under test.
        await drawings_done(app)
        monkeypatch.setattr("herdr_blueprint.document.render_diagram", no_drawing)
        await app.open_item(Item(kind="mermaid", title="Flow", source="graph LR\n  A --> B"))
        await pilot.pause(0.3)
        assert not app.query(DiagramView)
        assert not app.query(DiagramFence)
        assert app.query(DocumentView)


async def test_with_the_flag_mermaid_is_drawn():
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="Flow", source="graph LR\n  Alpha --> Beta", draw=True))
        await wait_for(pilot, lambda: "Alpha" in str(app.query_one(DiagramView).render()))


async def test_the_flag_controls_fences_inside_documents(tmp_path):
    doc = tmp_path / "flow.md"
    doc.write_text("# Flow\n\n```mermaid\ngraph LR\n  A --> B\n```\n", encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="flow.md", path=doc))
        await pilot.pause()
        assert not app.query(DiagramFence)
        await app.open_item(Item(kind="file", title="flow.md", path=doc, draw=True))
        await pilot.pause()
        assert app.query(DiagramFence)


# --- refresh ------------------------------------------------------------------------

async def test_a_refresh_rereads_the_current_file(tmp_path):
    doc = tmp_path / "notes.md"
    doc.write_text("# Old", encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause(0.3)
        await app.open_item(Item(kind="file", title="notes.md", path=doc))
        doc.write_text("# New", encoding="utf-8")
        inbox.send({"kind": "refresh"})
        await wait_for(pilot, lambda: app.query(DocumentView) and "# New" in app.query_one(DocumentView).source)


async def test_a_refresh_with_the_flag_draws(tmp_path):
    diagram = tmp_path / "flow.mmd"
    diagram.write_text("graph LR\n  A --> B", encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause(0.3)
        await app.open_item(Item(kind="file", title="flow.mmd", path=diagram))
        assert not app.query(DiagramView)
        inbox.send({"kind": "refresh", "draw": True})
        # current is set after the view mounts, so wait for both.
        await wait_for(pilot, lambda: app.current.draw and bool(app.query(DiagramView)))


async def test_a_refresh_with_nothing_on_screen_is_ignored():
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause(0.3)
        await app.refresh_current(draw=True)
        await pilot.pause()
        assert app.current is None
        assert app.query(DocumentView)


async def test_r_reloads_the_current_file(tmp_path):
    doc = tmp_path / "notes.md"
    doc.write_text("# Old", encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="notes.md", path=doc))
        doc.write_text("# New", encoding="utf-8")
        await pilot.press("r")
        await wait_for(pilot, lambda: "# New" in app.query_one(DocumentView).source)


async def test_on_start_the_newest_waiting_send_is_shown():
    inbox.send({"kind": "text", "source": "# First", "title": "First"})
    inbox.send({"kind": "text", "source": "# Second", "title": "Second"})
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await wait_for(pilot, lambda: app.current is not None)
        assert app.current.title == "Second"


# --- theme --------------------------------------------------------------------------

async def test_theme_picker_cancel_restores_theme():
    app = make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.press("t", "down")
        assert app.theme == "bp-blueprint"
        await pilot.press("escape")
        assert app.theme == "bp-rose-pine"


async def test_theme_picker_enter_saves_choice():
    app = make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.press("t", "down", "enter")
        assert app.theme == "bp-blueprint"
        assert app.settings.theme == "blueprint"


# --- header -------------------------------------------------------------------------

def test_ago_formats_relative_time():
    assert ago(100, now=130) == "just now"
    assert ago(100, now=100 + 120) == "2m ago"
    assert ago(100, now=100 + 7200) == "2h ago"


async def test_missing_file_shows_notice_and_keeps_content(tmp_path):
    app = make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="gone.md", path=tmp_path / "gone.md"))
        assert "Cannot read gone.md" in str(app.query_one("#status").render())
        assert app.query(DocumentView)


async def test_notice_clears_after_a_good_file(tmp_path):
    good = tmp_path / "good.md"
    good.write_text("# Good", encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="gone.md", path=tmp_path / "gone.md"))
        await app.open_item(Item(kind="file", title="good.md", path=good))
        assert app.notice is None
        assert "Cannot read" not in str(app.query_one("#status").render())


async def test_header_keeps_title_readable_in_a_narrow_pane():
    app = make_app()
    async with app.run_test(size=(40, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="Live check", source="graph LR\n  A --> B", sent_by="agent"))
        await pilot.pause()
        assert app.query_one("#source").size.width >= len("Live check")
        assert not app.query_one("#brand").display


async def test_header_shows_full_status_in_a_wide_pane():
    app = make_app()
    async with app.run_test(size=(100, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="Live check", source="graph LR\n  A --> B", sent_by="agent"))
        await pilot.pause()
        status = str(app.query_one("#status").render())
        assert "from agent just now" in status
        assert "follow" not in status
        assert app.query_one("#brand").display


async def test_narrow_header_shows_the_warning_text(tmp_path):
    app = make_app()
    async with app.run_test(size=(40, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="gone.md", path=tmp_path / "gone.md"))
        await pilot.pause()
        assert "Cannot read gone.md" in str(app.query_one("#source").render())


async def test_status_updates_after_exit_are_ignored():
    app = make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
    app._update_status()


# --- odd input ----------------------------------------------------------------------

async def largest_pause(pilot, action, settle: float) -> float:
    """Longest gap the event loop could not run while `action` and layout happen."""
    gaps: list[float] = []
    running = True

    async def ticker():
        last = time.perf_counter()
        while running:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await action()
    await pilot.pause(settle)
    running = False
    await task
    return max(gaps)


def changelog(size: int) -> str:
    section = "## v1.{n}.0\n\n- Fix a bug in the parser (#{n})\n- Add an option\n- Update docs\n\n"
    parts, total, n = ["# Changelog\n\n"], 0, 0
    while total < size:
        part = section.format(n=n)
        parts.append(part)
        total += len(part)
        n += 1
    return "".join(parts)


async def test_a_1_mb_document_keeps_the_ui_responsive(tmp_path):
    doc = tmp_path / "CHANGELOG.md"
    doc.write_text(changelog(1_000_000), encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        item = Item(kind="file", title="CHANGELOG.md", path=doc)
        gap = await largest_pause(pilot, lambda: app.open_item(item), settle=1.0)
        assert gap < 0.5
        assert app.query(PlainView)
        assert "plain text" in str(app.query_one("#status").render())


async def test_a_big_diagram_keeps_the_ui_responsive():
    # A branching tree: termaid needs seconds for this, a straight chain is instant.
    source = "graph TD\n" + "".join(f"  N{i // 2}[Step {i // 2}] --> N{i}[Step {i}]\n" for i in range(1, 100))
    app = make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        item = Item(kind="mermaid", title="Big", source=source, draw=True)
        gap = await largest_pause(pilot, lambda: app.open_item(item), settle=0.2)
        assert gap < 0.5
        await wait_for(pilot, lambda: "Step 0" in str(app.query_one(DiagramView).render()), seconds=15)


async def test_titles_with_brackets_do_not_crash():
    app = make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="Plan [/draft]", source="graph LR\n  A --> B"))
        await pilot.pause()
        assert "Plan [/draft]" in str(app.query_one("#source").render())


async def test_titles_lose_control_characters():
    app = make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="x\x1b]0;evil\x07", source="graph LR\n  A --> B"))
        await pilot.pause()
        assert "\x1b" not in str(app.query_one("#source").render())


async def test_two_opens_at_once_leave_one_view(tmp_path):
    doc = tmp_path / "a.md"
    doc.write_text("# A", encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        first = Item(kind="file", title="a.md", path=doc)
        second = Item(kind="mermaid", title="B", source="graph LR\n  A --> B", draw=True)
        await asyncio.gather(app.open_item(first), app.open_item(second))
        await pilot.pause()
        assert len(body_views(app)) == 1
        assert str(app.query_one("#source").render()) == "B"


# --- wide diagrams --------------------------------------------------------------------

WIDE = (
    "sequenceDiagram\n"
    "  participant A as Customer\n  participant B as Gateway\n  participant C as OrderService\n"
    "  participant D as Inventory\n  participant E as PaymentProvider\n"
    "  A->>B: place order\n  B->>C: create order\n  C->>D: reserve stock\n  C->>E: charge card\n"
)


async def test_a_wide_diagram_gets_a_horizontal_scrollbar():
    app = make_app()
    async with app.run_test(size=(50, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="Wide", source=WIDE, draw=True))
        await drawings_done(app)
        await pilot.pause()
        body = app.query_one("#body")
        assert body.max_scroll_x > 0
        assert body.show_horizontal_scrollbar


async def test_a_wide_diagram_in_a_document_gets_a_horizontal_scrollbar():
    app = make_app()
    async with app.run_test(size=(50, 30)) as pilot:
        await pilot.pause()
        doc = f"# Flow\n\n```mermaid\n{WIDE}```\n"
        await app.open_item(Item(kind="markdown", title="Doc", source=doc, draw=True))
        await drawings_done(app)
        await pilot.pause()
        fence = app.query_one(DiagramFence)
        assert fence.max_scroll_x > 0
        assert fence.show_horizontal_scrollbar
        assert fence.styles.scrollbar_size_horizontal == 1


async def test_a_diagram_that_fits_has_no_horizontal_scrollbar():
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="Small", source="graph LR\n  A --> B", draw=True))
        await drawings_done(app)
        await pilot.pause()
        assert not app.query_one("#body").show_horizontal_scrollbar


async def test_a_wide_diagram_opens_scrolled_to_the_left():
    app = make_app()
    async with app.run_test(size=(50, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="Wide", source=WIDE, draw=True))
        await drawings_done(app)
        await pilot.pause(0.3)
        body = app.query_one("#body")
        assert body.max_scroll_x > 0
        assert body.scroll_x == 0


# --- second review fixes ----------------------------------------------------------------

async def test_r_during_a_send_keeps_the_new_item(tmp_path):
    old = tmp_path / "a.md"
    old.write_text("# A", encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="a.md", path=old))
        new = Item(kind="markdown", title="B", source="# B\n\n" + "text\n\n" * 200)
        sending = asyncio.create_task(app.open_item(new))
        await asyncio.sleep(0)  # the send has started and holds the render lock
        await app.action_reload()
        await sending
        await pilot.pause()
        assert app.current.title == "B"
        assert str(app.query_one("#source").render()) == "B"


async def test_a_drawn_diagram_over_the_limit_shows_as_text_without_freezing():
    from herdr_blueprint.diagram import MAX_DIAGRAM_CHARS

    huge = "graph TD\n" + "".join(f"  N{i} --> N{i + 1}\n" for i in range(MAX_DIAGRAM_CHARS // 5))
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        item = Item(kind="mermaid", title="Huge", source=huge, draw=True)
        gap = await largest_pause(pilot, lambda: app.open_item(item), settle=0.5)
        assert gap < 0.5
        assert not app.query(DiagramView)
        assert "too large to draw" in str(app.query_one("#status").render())


# --- vim keys ---------------------------------------------------------------------------

LONG_DOC = "# Long\n\n" + "\n\n".join(f"Paragraph {i}" for i in range(80))


async def test_j_and_k_scroll_the_page():
    app = make_app()
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="markdown", title="Long", source=LONG_DOC))
        await pilot.pause()
        body = app.query_one("#body")
        await pilot.press("j", "j", "j")
        await pilot.pause()
        assert body.scroll_y == 3
        await pilot.press("k")
        await pilot.pause()
        assert body.scroll_y == 2


async def test_g_and_shift_g_jump_to_top_and_bottom():
    app = make_app()
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="markdown", title="Long", source=LONG_DOC))
        await pilot.pause()
        body = app.query_one("#body")
        await pilot.press("G")
        await pilot.pause()
        assert body.scroll_y == body.max_scroll_y > 0
        await pilot.press("g")
        await pilot.pause()
        assert body.scroll_y == 0


async def test_h_and_l_scroll_a_wide_diagram():
    app = make_app()
    async with app.run_test(size=(50, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="Wide", source=WIDE, draw=True))
        await drawings_done(app)
        await pilot.pause()
        body = app.query_one("#body")
        await pilot.press("l", "l")
        await pilot.pause()
        assert body.scroll_x > 0
        await pilot.press("h", "h")
        await pilot.pause()
        assert body.scroll_x == 0


async def test_h_and_l_scroll_wide_diagrams_inside_a_document():
    app = make_app()
    async with app.run_test(size=(50, 30)) as pilot:
        await pilot.pause()
        doc = f"# Flow\n\n```mermaid\n{WIDE}```\n"
        await app.open_item(Item(kind="markdown", title="Doc", source=doc, draw=True))
        await drawings_done(app)
        await pilot.pause()
        fence = app.query_one(DiagramFence)
        await pilot.press("l")
        await pilot.pause()
        assert fence.scroll_x > 0


async def test_j_scrolls_the_plain_view(tmp_path):
    big = "# Log\n\n" + "\n".join(f"line {i}" for i in range(3000))
    app = make_app()
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="markdown", title="big", source=big))
        await pilot.pause()
        await pilot.press("j", "j")
        await pilot.pause()
        assert app.query_one(PlainView).scroll_y == 2


async def test_ctrl_d_and_ctrl_u_move_half_a_page():
    app = make_app()
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="markdown", title="Long", source=LONG_DOC))
        await pilot.pause()
        body = app.query_one("#body")
        half = body.scrollable_content_region.height // 2
        assert half > 5
        await pilot.press("ctrl+d", "ctrl+d")
        await pilot.pause()
        assert body.scroll_y == 2 * half
        await pilot.press("ctrl+u")
        await pilot.pause()
        assert body.scroll_y == half


async def test_ctrl_d_moves_the_plain_view_half_a_page():
    big = "# Log\n\n" + "\n".join(f"line {i}" for i in range(3000))
    app = make_app()
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="markdown", title="big", source=big))
        await pilot.pause()
        view = app.query_one(PlainView)
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert view.scroll_y == view.scrollable_content_region.height // 2 > 5


async def test_the_footer_shows_the_move_keys():
    app = make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        shown = [b for b in app.BINDINGS if b.show]
        assert any(b.key_display == "hjkl" for b in shown)


async def test_theme_picker_moves_with_j_and_k():
    app = make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.press("t", "j")
        assert app.theme == "bp-blueprint"
        await pilot.press("j")
        assert app.theme == "bp-catppuccin-mocha"
        await pilot.press("k")
        assert app.theme == "bp-blueprint"
        await pilot.press("enter")
        assert app.settings.theme == "blueprint"


async def test_theme_picker_q_cancels_like_escape():
    app = make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.press("t", "j", "q")
        assert app.theme == "bp-rose-pine"
        assert app.is_running


# --- the start screen ---------------------------------------------------------------



def start_app(source=None, asker=None, finder=None, refresher=None) -> BlueprintApp:
    # Tests never ask the real herdr: the neighbor stays as given unless a test says otherwise.
    return BlueprintApp(
        settings=Config(theme="rose-pine"), source=source, asker=asker, finder=finder,
        refresher=refresher or (lambda pane_id: source),
    )


def start_text(app) -> str:
    return "\n".join(str(static.content) for static in app.query_one(StartView).query(Static))


def header_text(app) -> str:
    return str(app.query_one("#source", Static).content) + " | " + str(app.query_one("#status", Static).content)


async def test_opened_next_to_an_agent_it_shows_who_and_what_to_do(tmp_path):
    source = Source("w1:p1", agent="claude", task="Fix login", cwd=Path.home() / "work" / "shop" / "web")
    app = start_app(source)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        text = start_text(app)
        assert "Nothing here yet" in text
        assert "claude" in text and "Fix login" in text
        assert "~/…/shop/web".replace("/", os.sep) in text
        assert "Ask claude to draw this task" in text
        assert "Open a file from this folder" in text
        assert app.check_action("ask", ()) and app.check_action("open_file", ())
        assert app.current is None


async def test_next_to_a_shell_there_is_nobody_to_ask(tmp_path):
    app = start_app(Source("w1:p1", cwd=tmp_path))
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert "Ask" not in start_text(app)
        assert not app.check_action("ask", ())
        assert app.check_action("open_file", ())


async def test_outside_herdr_it_keeps_the_welcome_screen():
    app = start_app(None)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert not app.query(StartView)
        assert app.query(DocumentView)
        assert not app.check_action("ask", ()) and not app.check_action("open_file", ())


async def test_a_send_replaces_the_start_screen(tmp_path):
    app = start_app(Source("w1:p1", agent="claude", cwd=tmp_path))
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause(0.3)
        inbox.send({"kind": "text", "source": "# Plan", "title": "Plan"})
        await wait_for(pilot, lambda: app.current and app.current.title == "Plan")
        assert not app.query(StartView)
        # a and o keep working after the first send.
        assert app.check_action("ask", ()) and app.check_action("open_file", ())


async def test_a_asks_the_agent_and_says_so(tmp_path):
    asked = []

    def asker(source):
        asked.append(source.pane_id)
        return True, "Asked claude to draw"

    app = start_app(Source("w1:p1", agent="claude", cwd=tmp_path), asker=asker)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await wait_for(pilot, lambda: app.notice == "Asked claude to draw")
        assert asked == ["w1:p1"]
        header = header_text(app)
        assert "Asked claude to draw" in header and "⚠" not in header


async def test_a_failed_ask_is_a_warning(tmp_path):
    app = start_app(
        Source("w1:p1", agent="claude", cwd=tmp_path),
        asker=lambda source: (False, "claude is waiting for your answer"),
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await wait_for(pilot, lambda: app.notice == "claude is waiting for your answer")
        assert "⚠" in header_text(app)


async def test_o_picks_a_file_from_the_folder_and_draws_it(tmp_path):
    (tmp_path / "docs").mkdir()
    flow = tmp_path / "docs" / "flow.md"
    flow.write_text("# Flow\n\n```mermaid\ngraph LR\n  A --> B\n```\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("# Readme", encoding="utf-8")
    folders = []

    def finder(folder):
        folders.append(folder)
        return [readme, flow]

    app = start_app(Source("w1:p1", agent="claude", cwd=tmp_path), finder=finder)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await wait_for(pilot, lambda: isinstance(app.screen, FilePicker) and app.screen.query_one(OptionList).option_count == 2)
        assert folders == [tmp_path]
        await pilot.press("j", "enter")
        await wait_for(pilot, lambda: app.current is not None)
        assert (app.current.title, app.current.path, app.current.sent_by, app.current.draw) == (
            "docs/flow.md", flow, "you", True,
        )
        assert "from you" in header_text(app)


async def test_the_file_picker_closes_with_escape(tmp_path):
    app = start_app(Source("w1:p1", cwd=tmp_path), finder=lambda folder: [])
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await wait_for(pilot, lambda: isinstance(app.screen, FilePicker))
        await wait_for(pilot, lambda: "No Markdown or Mermaid files" in str(app.screen.query_one("#files-status", Static).content))
        await pilot.press("escape")
        await wait_for(pilot, lambda: not isinstance(app.screen, FilePicker))
        assert app.query(StartView) and app.current is None


async def test_a_failing_file_search_does_not_close_the_viewer(tmp_path):
    def broken(folder):
        raise PermissionError("no access")

    app = start_app(Source("w1:p1", cwd=tmp_path), finder=broken)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await wait_for(pilot, lambda: isinstance(app.screen, FilePicker))
        await wait_for(pilot, lambda: "Cannot list" in str(app.screen.query_one("#files-status", Static).content))
        assert app.is_running


# --- the neighbor changes ---------------------------------------------------------------

class Neighbor:
    """What herdr reports for the pane next to Blueprint, changeable mid-test."""

    def __init__(self, source):
        self.source = source
        self.reads = 0

    def __call__(self, pane_id):
        self.reads += 1
        return self.source


async def test_an_agent_started_later_shows_up(tmp_path):
    neighbor = Neighbor(Source("w1:p1", cwd=tmp_path))
    app = start_app(neighbor.source, refresher=neighbor)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert not app.check_action("ask", ())
        neighbor.source = Source("w1:p1", agent="claude", task="Fix login", cwd=tmp_path)
        app.refresh_source()
        await wait_for(pilot, lambda: app.check_action("ask", ()))
        await wait_for(pilot, lambda: "Ask claude to draw this task" in start_text(app))
        assert "Fix login" in start_text(app)


async def test_focusing_blueprint_rereads_the_neighbor(tmp_path):
    from textual import events

    neighbor = Neighbor(Source("w1:p1", cwd=tmp_path))
    app = start_app(neighbor.source, refresher=neighbor)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        before = neighbor.reads
        app.post_message(events.AppFocus())
        await wait_for(pilot, lambda: neighbor.reads > before)


async def test_a_closed_neighbor_hides_ask_but_keeps_the_folder(tmp_path):
    neighbor = Neighbor(Source("w1:p1", agent="claude", cwd=tmp_path))
    app = start_app(neighbor.source, refresher=neighbor)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        neighbor.source = None  # herdr no longer knows the pane
        app.refresh_source()
        await wait_for(pilot, lambda: not app.check_action("ask", ()))
        assert app.check_action("open_file", ())


async def test_a_new_neighbor_does_not_replace_what_was_sent(tmp_path):
    neighbor = Neighbor(Source("w1:p1", cwd=tmp_path))
    app = start_app(neighbor.source, refresher=neighbor)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="markdown", title="Plan", source="# Plan"))
        neighbor.source = Source("w1:p1", agent="claude", cwd=tmp_path)
        app.refresh_source()
        await wait_for(pilot, lambda: app.check_action("ask", ()))
        await pilot.pause()
        assert not app.query(StartView) and app.current.title == "Plan"


async def test_a_rereads_and_says_when_the_agent_left(tmp_path):
    asked = []
    neighbor = Neighbor(Source("w1:p1", agent="claude", cwd=tmp_path))
    app = start_app(neighbor.source, asker=lambda s: asked.append(s) or (True, "Asked"), refresher=neighbor)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        neighbor.source = Source("w1:p1", cwd=tmp_path)  # claude exited a moment ago
        await pilot.press("a")
        await wait_for(pilot, lambda: app.notice == "claude is not in that pane anymore")
        assert asked == []
        assert not app.check_action("ask", ())
