from herdr_blueprint.app import BlueprintApp, ago
from herdr_blueprint.config import Config
from herdr_blueprint.document import DiagramView, DocumentView
from herdr_blueprint.history import Item
from herdr_blueprint.sources import inbox


def make_app(tmp_path) -> BlueprintApp:
    return BlueprintApp(root=tmp_path, settings=Config(theme="rose-pine", follow=True))


async def test_starts_on_welcome_screen(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        assert app.theme == "bp-rose-pine"
        assert app.query(DocumentView)


async def test_agent_draw_opens_at_front(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause(0.3)
        inbox.send({"kind": "mermaid", "source": "graph LR\n  A --> B", "title": "Flow", "sent_by": "agent"})
        for _ in range(30):
            await pilot.pause(0.1)
            if app.query(DiagramView):
                break
        assert app.query(DiagramView)
        assert app.history.current.title == "Flow"


async def test_theme_picker_cancel_restores_theme(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.press("t", "down")
        assert app.theme == "bp-blueprint"
        await pilot.press("escape")
        assert app.theme == "bp-rose-pine"


async def test_theme_picker_enter_saves_choice(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.press("t", "down", "enter")
        assert app.theme == "bp-blueprint"
        assert app.settings.theme == "blueprint"


def test_ago_formats_relative_time():
    assert ago(100, now=130) == "just now"
    assert ago(100, now=100 + 120) == "2m ago"
    assert ago(100, now=100 + 7200) == "2h ago"


async def test_missing_file_shows_notice_and_keeps_content(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="gone.md", path=tmp_path / "gone.md"))
        assert "Cannot read gone.md" in str(app.query_one("#status").render())
        assert app.query(DocumentView)


async def test_notice_clears_after_a_good_file(tmp_path):
    good = tmp_path / "good.md"
    good.write_text("# Good", encoding="utf-8")
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="gone.md", path=tmp_path / "gone.md"))
        await app.open_item(Item(kind="file", title="good.md", path=good))
        assert app.notice is None
        assert "Cannot read" not in str(app.query_one("#status").render())


async def test_header_keeps_title_readable_in_a_narrow_pane(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(40, 20)) as pilot:
        await pilot.pause()
        await app.open_item(app.history.push(Item(kind="mermaid", title="Live check", source="graph LR\n  A --> B", sent_by="agent")))
        await pilot.pause()
        assert app.query_one("#source").size.width >= len("Live check")
        assert "◉" in str(app.query_one("#status").render())


async def test_header_shows_full_status_in_a_wide_pane(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(100, 20)) as pilot:
        await pilot.pause()
        await app.open_item(app.history.push(Item(kind="mermaid", title="Live check", source="graph LR\n  A --> B", sent_by="agent")))
        await pilot.pause()
        assert "from agent just now" in str(app.query_one("#status").render())
        assert app.query_one("#brand").display


# --- final review fixes ---------------------------------------------------------

import asyncio
import time

from herdr_blueprint.document import PlainView


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
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        item = app.history.push(Item(kind="file", title="CHANGELOG.md", path=doc))
        gap = await largest_pause(pilot, lambda: app.open_item(item), settle=1.0)
        assert gap < 0.5
        assert app.query(PlainView)
        assert "plain text" in str(app.query_one("#status").render())


async def test_a_big_diagram_keeps_the_ui_responsive(tmp_path):
    # A branching tree: termaid needs seconds for this, a straight chain is instant.
    source = "graph TD\n" + "".join(f"  N{i // 2}[Step {i // 2}] --> N{i}[Step {i}]\n" for i in range(1, 100))
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        item = app.history.push(Item(kind="mermaid", title="Big", source=source))
        gap = await largest_pause(pilot, lambda: app.open_item(item), settle=0.2)
        assert gap < 0.5
        for _ in range(200):
            if "Step 0" in str(app.query_one(DiagramView).render()):
                break
            await pilot.pause(0.05)
        assert "Step 0" in str(app.query_one(DiagramView).render())


async def test_titles_with_brackets_do_not_crash(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        await app.open_item(app.history.push(Item(kind="mermaid", title="Plan [/draft]", source="graph LR\n  A --> B")))
        await pilot.pause()
        assert "Plan [/draft]" in str(app.query_one("#source").render())


async def test_titles_lose_control_characters(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        await app.open_item(app.history.push(Item(kind="mermaid", title="x\x1b]0;evil\x07", source="graph LR\n  A --> B")))
        await pilot.pause()
        assert "\x1b" not in str(app.query_one("#source").render())
        assert "\x1b" not in str(app.query_one("#tabs").render())


async def test_two_opens_at_once_leave_one_view(tmp_path):
    doc = tmp_path / "a.md"
    doc.write_text("# A", encoding="utf-8")
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        first = app.history.push(Item(kind="file", title="a.md", path=doc))
        second = app.history.push(Item(kind="mermaid", title="B", source="graph LR\n  A --> B"))
        await asyncio.gather(app.open_item(first), app.open_item(second))
        await pilot.pause()
        assert len(app.query_one("#body").children) == 1
        assert str(app.query_one("#source").render()) == "B"


async def test_narrow_header_shows_the_warning_text(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(40, 20)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="file", title="gone.md", path=tmp_path / "gone.md"))
        await pilot.pause()
        assert "Cannot read gone.md" in str(app.query_one("#source").render())


async def test_status_updates_after_exit_are_ignored(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
    app._update_status()
