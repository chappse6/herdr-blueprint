"""Seeing what the agent is building at a glance: Blueprint asks for a drawing when it
opens next to an agent, and says when the code behind a drawing changed since."""

import os
import time
from pathlib import Path

from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.item import Item
from herdr_blueprint.sources.pane import Source


async def wait_for(pilot, condition, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition not met in time")


def make_app(tmp_path, status="idle", agent="claude", asked=None, state=None):
    state = state if state is not None else {"status": status}
    asked = asked if asked is not None else []

    def neighbor(pane_id=None):
        return Source("w1:p1", agent=agent, cwd=tmp_path, status=state["status"])

    def ask(source):
        asked.append(source)
        return True, f"Asked {source.agent} to draw its task"

    return BlueprintApp(
        settings=Config(theme="rose-pine"), source=neighbor(), refresher=neighbor, asker=ask,
        prompter=lambda s, t: (True, "Sent to claude"), idle_shell=lambda pane_id: False, kinds=lambda: [],
    )


# --- asking when it opens -----------------------------------------------------------

async def test_opened_next_to_an_idle_agent_it_asks_for_a_drawing(tmp_path):
    asked = []
    app = make_app(tmp_path, asked=asked)
    async with app.run_test(size=(100, 30)) as pilot:
        await wait_for(pilot, lambda: asked)
        await pilot.pause(0.3)
        assert len(asked) == 1 and app.notice == "Asked claude to draw its task"


async def test_a_busy_agent_is_asked_when_it_finishes(tmp_path):
    asked, state = [], {"status": "working"}
    app = make_app(tmp_path, asked=asked, state=state)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        assert asked == [] and "asks it to draw when it finishes" in app.notice
        state["status"] = "idle"
        app.refresh_source()
        await wait_for(pilot, lambda: asked)
        app.refresh_source()
        await pilot.pause(0.3)
        assert len(asked) == 1  # once


async def test_nothing_is_asked_without_an_agent_or_when_something_is_shown(tmp_path):
    asked = []
    app = make_app(tmp_path, agent=None, asked=asked)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.5)
        assert asked == []


# --- code changed since the drawing ------------------------------------------------

DESIGN = "graph LR\n  A[Check] --> B[Issue]\n  %% code: A src/check.ts:10\n  %% code: B src/issue.ts:4\n"


async def test_the_header_says_when_code_behind_the_drawing_changed(tmp_path):
    (tmp_path / "src").mkdir()
    for name in ("check.ts", "issue.ts"):
        (tmp_path / "src" / name).write_text("x", encoding="utf-8")
    app = make_app(tmp_path, status="working")  # no auto ask in the way
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.open_item(Item(kind="mermaid", title="Flow", source=DESIGN, draw=True, at=time.time()))
        app.notice = None
        app.check_freshness()
        assert app.stale_files == []
        later = time.time() + 60
        os.utime(tmp_path / "src" / "check.ts", (later, later))
        app.check_freshness()
        assert app.stale_files == ["src/check.ts"]
        status = str(app.query_one("#status").render())
        assert "changed since" in status and "a redraws" in status
        # The agent's next drawing is current again.
        await app.open_item(Item(kind="mermaid", title="Flow", source=DESIGN, draw=True, at=later + 1))
        app.check_freshness()
        assert app.stale_files == []
