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
