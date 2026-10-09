from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.document import DiagramView, DocumentView
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
