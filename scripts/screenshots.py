"""Write README screenshots to docs/screenshots.

Run: uv run python scripts/screenshots.py

Textual saves SVG. Box-drawing lines in an SVG look dashed when GitHub scales
it down, so each one is turned into a 2x PNG with headless Chrome when Chrome
is installed; otherwise the SVG is kept.
"""

import asyncio
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.item import Item

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
FIXTURE = ROOT / "tests" / "fixtures" / "order-flow.md"
CHROMES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]


def find_chrome() -> str | None:
    for candidate in CHROMES:
        if Path(candidate).exists() or shutil.which(candidate):
            return candidate
    return None


def to_png(chrome: str, svg: Path, png: Path) -> None:
    width, height = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text()).groups()
    subprocess.run(
        [
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=2", "--default-background-color=00000000",
            f"--window-size={int(float(width))},{int(float(height)) + 1}",
            f"--screenshot={png}", svg.as_uri(),
        ],
        check=True,
        capture_output=True,
    )


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


async def shoot(name: str, theme: str, action, chrome: str | None) -> None:
    app = BlueprintApp(settings=Config(theme=theme))
    async with app.run_test(size=(76, 38)) as pilot:
        await pilot.pause(0.3)
        await action(pilot)
        await pilot.pause(0.3)
        if chrome is None:
            app.save_screenshot(filename=f"{name}.svg", path=str(OUT))
            return
        with tempfile.TemporaryDirectory() as tmp:
            svg = Path(app.save_screenshot(filename=f"{name}.svg", path=tmp))
            to_png(chrome, svg, OUT / f"{name}.png")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    if chrome is None:
        print("Chrome not found: keeping SVG screenshots")
    await shoot("document", "rose-pine", open_fixture, chrome)
    await shoot("blueprint-theme", "blueprint", open_fixture, chrome)
    await shoot("theme-picker", "rose-pine", open_picker, chrome)


if __name__ == "__main__":
    asyncio.run(main())
