from pathlib import Path

import pytest

from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.history import Item
from herdr_blueprint.themes import PALETTES

FIXTURE = Path(__file__).parent / "fixtures" / "order-flow.md"
SIZE = (72, 36)


def make_app(tmp_path: Path, theme: str = "rose-pine") -> BlueprintApp:
    # follow=False keeps file events in tmp_path from changing the screen.
    return BlueprintApp(root=tmp_path, settings=Config(theme=theme, follow=False))


async def open_fixture(pilot) -> None:
    item = pilot.app.history.push(Item(kind="file", title="order-flow.md", path=FIXTURE))
    await pilot.app.open_item(item)


@pytest.mark.parametrize("theme", list(PALETTES))
def test_welcome_in_every_theme(snap_compare, tmp_path, theme):
    assert snap_compare(make_app(tmp_path, theme), terminal_size=SIZE)


def test_document_with_diagram(snap_compare, tmp_path):
    assert snap_compare(make_app(tmp_path), terminal_size=SIZE, run_before=open_fixture)


def test_theme_picker(snap_compare, tmp_path):
    assert snap_compare(make_app(tmp_path), terminal_size=SIZE, run_before=open_fixture, press=["t", "down"])
