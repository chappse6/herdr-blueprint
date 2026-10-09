from pathlib import Path

import pytest

from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.item import Item
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
