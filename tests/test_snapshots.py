from pathlib import Path

import pytest
from textual.widgets import OptionList

from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.item import Item
from herdr_blueprint.sources.pane import Source
from herdr_blueprint.themes import PALETTES

FIXTURE = Path(__file__).parent / "fixtures" / "order-flow.md"
SIZE = (72, 36)


def make_app(theme: str = "rose-pine") -> BlueprintApp:
    return BlueprintApp(settings=Config(theme=theme))


async def settle(pilot) -> None:
    # Diagrams are drawn in worker threads; wait so snapshots never catch a placeholder.
    # (Only the draw workers: the inbox worker runs until the app closes.)
    # An empty list would mean "wait for every worker", so check first.
    drawing = [worker for worker in pilot.app.workers if worker.group == "draw"]
    if drawing:
        await pilot.app.workers.wait_for_complete(drawing)
    await pilot.pause()


async def open_fixture(pilot) -> None:
    await pilot.app.open_item(Item(kind="file", title="order-flow.md", path=FIXTURE, draw=True))
    await settle(pilot)


@pytest.mark.parametrize("theme", list(PALETTES))
def test_welcome_in_every_theme(snap_compare, theme):
    assert snap_compare(make_app(theme), terminal_size=SIZE, run_before=settle)


def test_document_with_diagram(snap_compare):
    assert snap_compare(make_app(), terminal_size=SIZE, run_before=open_fixture)


def test_theme_picker(snap_compare):
    assert snap_compare(make_app(), terminal_size=SIZE, run_before=open_fixture, press=["t", "down"])


# --- start screen -----------------------------------------------------------------------

SOURCE = Source("w1:p1", agent="claude", task="Fix the login flow", cwd=Path("/work/shop/web"))
FILES = [Path("/work/shop/web") / name for name in ("README.md", "docs/login-flow.mmd", "docs/api.md")]


def start_app(monkeypatch) -> BlueprintApp:
    # The folder line uses the OS path separator; keep snapshots the same everywhere.
    monkeypatch.setattr("herdr_blueprint.start.short_folder", lambda path, home=None: "~/…/shop/web")
    return BlueprintApp(settings=Config(theme="rose-pine"), source=SOURCE, finder=lambda folder: FILES)


def test_start_screen(snap_compare, monkeypatch):
    assert snap_compare(start_app(monkeypatch), terminal_size=SIZE, run_before=settle)


def test_start_screen_narrow(snap_compare, monkeypatch):
    assert snap_compare(start_app(monkeypatch), terminal_size=(40, 24), run_before=settle)


async def open_file_picker(pilot) -> None:
    await settle(pilot)
    await pilot.press("o")
    # The list fills in from a worker thread.
    for _ in range(100):
        if pilot.app.screen.query_one(OptionList).option_count:
            break
        await pilot.pause(0.05)
    await pilot.pause()


def test_file_picker(snap_compare, monkeypatch):
    assert snap_compare(start_app(monkeypatch), terminal_size=SIZE, run_before=open_file_picker)
