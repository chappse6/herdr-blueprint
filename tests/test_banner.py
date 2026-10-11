"""After a request goes to the agent, a banner says so plainly until the agent's new
version arrives, and the part the note was about stays marked in the drawing."""

import time
from pathlib import Path

from herdr_blueprint.item import Item
from test_point_app import FLOW, FakePrompter, flow_file, make_app, open_drawn, wait_for


def banner(app) -> str:
    widget = app.query_one("#banner")
    return str(widget.render()) if widget.display else ""


async def test_a_sent_note_shows_a_banner_and_marks_its_part(tmp_path):
    app = make_app(tmp_path, prompter=FakePrompter())
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        view = await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "l", "enter", *"cache it", "enter")
        await wait_for(pilot, lambda: 'Asked claude about the box "Tollgate"' in banner(app))
        assert "waiting for the new version" in banner(app)
        assert view.marked is not None and view.marked.label == "Tollgate"


async def test_the_agents_next_version_says_it_arrived(tmp_path):
    app = make_app(tmp_path, prompter=FakePrompter())
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        path = flow_file(tmp_path)
        await open_drawn(app, pilot, path)
        await pilot.press("e", "l", "enter", *"cache it", "enter")
        await wait_for(pilot, lambda: "waiting" in banner(app))
        path.write_text(FLOW + "  C --> D[Cache]\n", encoding="utf-8")
        await app.open_item(Item(kind="file", title="flow.mmd", path=path, draw=True, at=time.time() + 1))
        assert banner(app) == "✓ claude sent a new version" and app.pending is None


async def test_a_sent_edit_says_the_agents_version_comes_next(tmp_path):
    def writer(command):
        Path(command[-1]).write_text("graph LR\n  A[Browser] --> B[Tollgate]\n  B --> C[Valkey]\n", encoding="utf-8")
        return 0

    app = make_app(tmp_path, prompter=FakePrompter(), editor=writer)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "e")
        await wait_for(pilot, lambda: "Sent your edit to claude" in banner(app))
        assert "comes next" in banner(app)


async def test_a_failed_send_says_so_in_the_banner(tmp_path):
    app = make_app(tmp_path, prompter=FakePrompter((False, "claude is waiting for your answer")))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await open_drawn(app, pilot, flow_file(tmp_path))
        await pilot.press("e", "l", "enter", *"cache it", "enter")
        await wait_for(pilot, lambda: "claude is waiting for your answer" in banner(app))
        assert banner(app).startswith("⚠") and app.pending is None
