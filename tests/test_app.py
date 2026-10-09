import asyncio
import time

from herdr_blueprint.app import BlueprintApp, ago
from herdr_blueprint.config import Config
from herdr_blueprint.document import DiagramFence, DiagramView, DocumentView, PlainView
from herdr_blueprint.item import Item
from herdr_blueprint.sources import inbox


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
