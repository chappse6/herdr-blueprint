"""Write the theme screenshots in docs/screenshots.

Run: uv run python scripts/screenshots.py

Textual saves SVG; scripts/png.py turns it into a 2x PNG without a browser.
The herdr screenshots come from scripts/demo (see its README).
"""

import asyncio
import tempfile
from pathlib import Path

from png import local_fonts, write_png

from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.item import Item

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
FIXTURE = ROOT / "tests" / "fixtures" / "order-flow.md"
async def drawings_done(app) -> None:
    # An empty list would mean "wait for every worker", including the endless inbox one.
    drawing = [worker for worker in app.workers if worker.group == "draw"]
    if drawing:
        await app.workers.wait_for_complete(drawing)


async def open_fixture(pilot) -> None:
    await pilot.app.open_item(Item(kind="file", title="docs/order-flow.md", path=FIXTURE, draw=True))
    await drawings_done(pilot.app)


async def open_picker(pilot) -> None:
    await open_fixture(pilot)
    await pilot.press("t", "down")


async def shoot(name: str, theme: str, action) -> None:
    app = BlueprintApp(settings=Config(theme=theme))
    async with app.run_test(size=(76, 38)) as pilot:
        await pilot.pause(0.3)
        await action(pilot)
        await pilot.pause(0.3)
        with tempfile.TemporaryDirectory() as tmp:
            svg = Path(app.save_screenshot(filename=f"{name}.svg", path=tmp))
            write_png(local_fonts(svg.read_text(encoding="utf-8")), OUT / f"{name}.png")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    await shoot("document", "rose-pine", open_fixture)
    await shoot("blueprint-theme", "blueprint", open_fixture)
    await shoot("theme-picker", "rose-pine", open_picker)


if __name__ == "__main__":
    asyncio.run(main())
