"""The Blueprint viewer: one screen showing the latest thing the worker sent."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.markdown import MarkdownFence
from textual.widgets.option_list import Option

from . import config as config_store
from .diagram import MAX_DIAGRAM_CHARS
from .document import DiagramView, DocumentView, PlainView, as_code
from .item import Item, clean, is_large
from .sources import pane
from .sources.inbox import Refresh, watch_inbox
from .start import FilePicker, StartView
from .themes import PALETTES, get_palette, palette_for_theme

WELCOME = """\
# Blueprint

The latest doc or diagram your agent sends shows up here.

```mermaid
graph LR
  A[Agent sends] --> B[Blueprint shows it]
  B --> C[Next send replaces it]
```

Add `--draw` to draw Mermaid. Without it, diagrams stay as text.

| Key | Action |
|---|---|
| `h` `j` `k` `l` | Move left, down, up, right |
| `g` `G` | Top, bottom |
| `ctrl+d` `ctrl+u` | Half a page down, up |
| `t` | Change theme |
| `r` | Reload |
| `q` | Quit |
"""

# Below this width the header drops the brand and uses a short status.
NARROW_WIDTH = 60

# Columns moved by one h or l press.
SIDE_STEP = 4

# Seconds between checks of the pane next to Blueprint (an agent may start or quit).
NEIGHBOR_SECONDS = 5


def ago(at: float, now: float | None = None) -> str:
    seconds = max(0, int((time.time() if now is None else now) - at))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


class ThemePicker(ModalScreen[str | None]):
    """Pick a palette. Moving the cursor previews it; Esc puts the old one back."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", show=False),
        # vim keys, same as in the viewer
        Binding("j", "cursor(1)", show=False),
        Binding("k", "cursor(-1)", show=False),
    ]

    def __init__(self, current: str) -> None:
        super().__init__()
        self.original = current

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static("Theme", id="picker-title")
            yield OptionList(*(Option(p.label, id=p.name) for p in PALETTES.values()))
            yield Static("j/k preview   ⏎ keep   esc cancel", id="picker-hint")

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.highlighted = list(PALETTES).index(palette_for_theme(self.original).name)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.app.theme = get_palette(event.option.id).theme_name

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cursor(self, step: int) -> None:
        options = self.query_one(OptionList)
        if step > 0:
            options.action_cursor_down()
        else:
            options.action_cursor_up()

    def action_cancel(self) -> None:
        self.app.theme = self.original
        self.dismiss(None)


class BlueprintApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "Blueprint"
    BINDINGS = [
        # vim-style movement; the footer shows them as one "hjkl Move" hint.
        Binding("j", "move(0, 1)", "Move", key_display="hjkl"),
        Binding("k", "move(0, -1)", show=False),
        Binding("h", "move(-1, 0)", show=False),
        Binding("l", "move(1, 0)", show=False),
        Binding("g", "jump(False)", show=False),
        Binding("G", "jump(True)", show=False),
        Binding("ctrl+d", "page(1)", show=False),
        Binding("ctrl+u", "page(-1)", show=False),
        # Only offered when Blueprint knows the pane it was opened next to.
        Binding("a", "ask", "Ask"),
        Binding("o", "open_file", "Open"),
        Binding("t", "pick_theme", "Theme"),
        Binding("r", "reload", "Reload"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        settings: config_store.Config | None = None,
        source: pane.Source | None = None,
        *,
        asker: Callable[[pane.Source], tuple[bool, str]] | None = None,
        finder: Callable[[Path], list[Path]] | None = None,
        refresher: Callable[[str], pane.Source | None] | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings or config_store.load()
        # The pane Blueprint was opened next to; None outside herdr.
        self.source = source
        herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
        self._asker = asker or (lambda src: pane.ask_to_draw(herdr, src))
        self._finder = finder or pane.find_documents
        self._refresher = refresher or (lambda pane_id: pane.source_of(herdr, pane_id))
        self.current: Item | None = None
        self.notice: str | None = None
        # A notice is a warning unless it reports something that worked.
        self.notice_ok = False
        self.title_text = "Start" if source else "Welcome"
        # Sends, refreshes and keys can overlap; one render at a time.
        self._render_lock = asyncio.Lock()
        for palette in PALETTES.values():
            self.register_theme(palette.textual_theme())

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static("BLUEPRINT", id="brand")
            yield Static("", id="source")
            yield Static("", id="status")
        yield VerticalScroll(id="body")
        yield Footer()

    async def on_mount(self) -> None:
        self.theme = get_palette(self.settings.theme).theme_name
        if self.source:
            body = self.query_one("#body", VerticalScroll)
            await body.mount(StartView(self.source))
        else:
            # The welcome example is tiny and ours, so it is always drawn.
            await self._render_text(WELCOME, diagram=False, draw=True, plain=False)
        self._update_status()
        self.run_worker(self._read_inbox(), exclusive=False, exit_on_error=False)
        self.set_interval(30, self._update_status)
        if self.source:
            self.set_interval(NEIGHBOR_SECONDS, self.refresh_source)

    async def _read_inbox(self) -> None:
        # A broken inbox must not close the viewer: say so in the header instead.
        try:
            async for message in watch_inbox():
                if isinstance(message, Refresh):
                    await self.refresh_current(draw=message.draw)
                else:
                    await self.open_item(message)
        except Exception as exc:
            self._show_notice(f"Inbox stopped: {exc}")

    def _show_notice(self, message: str, ok: bool = False) -> None:
        self.notice = message
        self.notice_ok = ok
        self._update_status()

    # Rendering -------------------------------------------------------------

    async def open_item(self, item: Item) -> None:
        """Replace the screen with `item`. The previous one is dropped."""
        async with self._render_lock:
            await self._open_locked(item)

    async def refresh_current(self, draw: bool) -> None:
        """Re-read and redraw what is on screen, with the worker's draw flag."""
        # Read `current` under the lock, so a send in progress is not undone.
        async with self._render_lock:
            if self.current is not None:
                await self._open_locked(replace(self.current, draw=draw, at=time.time()))

    async def _open_locked(self, item: Item) -> None:
        try:
            text = item.read()
        except OSError:
            # Keep the last content on screen; say what went wrong in the header.
            self._show_notice(f"Cannot read {clean(item.title)}")
            return
        # termaid refuses big diagrams anyway; show them as text right away
        # instead of drawing a long error.
        too_big = item.is_diagram and item.draw and len(text) > MAX_DIAGRAM_CHARS
        draw = item.draw and not too_big
        plain = not (item.is_diagram and draw) and is_large(text)
        if too_big:
            self.notice = "Diagram too large to draw, shown as text"
        elif plain:
            self.notice = "Large file, shown as plain text"
        else:
            self.notice = None
        self.notice_ok = False
        await self._render_text(text, diagram=item.is_diagram, draw=draw, plain=plain)
        self.current = item
        self.title_text = clean(item.title)
        self._update_status()

    async def _render_text(self, text: str, *, diagram: bool, draw: bool, plain: bool) -> None:
        body = self.query_one("#body", VerticalScroll)
        await body.remove_children()
        if plain:
            view = PlainView()
            await body.mount(view)
            await view.load(text)
        elif diagram and draw:
            await body.mount(DiagramView(text))
        elif diagram:
            await body.mount(DocumentView(as_code(text)))
        else:
            await body.mount(DocumentView(text, draw_diagrams=draw))
        body.scroll_home(animate=False)

    def _update_status(self) -> None:
        try:
            brand = self.query_one("#brand", Static)
            source = self.query_one("#source", Static)
            status_bar = self.query_one("#status", Static)
        except NoMatches:
            return  # a timer or resize fired while the app was closing
        # Side panes are often ~40 columns: keep the title readable by
        # hiding the brand and shortening the status. A notice then takes
        # the title's place so its text stays visible.
        narrow = self.size.width < NARROW_WIDTH
        brand.display = not narrow
        palette = palette_for_theme(self.theme)
        if self.notice_ok:
            notice = Text(f"✓ {self.notice}", style=f"bold {palette.success}")
        else:
            notice = Text(f"⚠ {self.notice}", style=f"bold {palette.warning}")
        if narrow and self.notice:
            source.update(notice)
        else:
            source.update(Text(self.title_text))
        status = Text()
        if self.notice and not narrow:
            status.append_text(notice)
        elif self.current and narrow:
            status.append(ago(self.current.at).removesuffix(" ago").replace("just ", ""))
        elif self.current:
            status.append(f"from {self.current.sent_by} {ago(self.current.at)}")
        status_bar.update(status)

    def on_resize(self) -> None:
        if self.is_mounted:
            self._update_status()

    # Actions ---------------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Hidden from the footer and the keys do nothing when there is no pane to use.
        if action == "ask":
            return bool(self.source and self.source.agent)
        if action == "open_file":
            return bool(self.source and self.source.cwd)
        return True

    def action_ask(self) -> None:
        """Ask the agent next to Blueprint to draw what it is doing."""
        if self.source and self.source.agent:
            self.run_worker(self._ask, thread=True, group="ask", exclusive=True)

    def _ask(self) -> None:
        assert self.source is not None
        before = self.source
        # The agent may have quit since the last check: look again first.
        fresh = self._read_neighbor()
        if fresh.agent:
            ok, message = self._asker(fresh)
        else:
            ok, message = False, f"{before.agent} is not in that pane anymore"
        self.call_from_thread(self._show_notice, message, ok)

    # The pane next to Blueprint --------------------------------------------

    def on_app_focus(self, event: events.AppFocus) -> None:
        # Coming back to Blueprint is when its keys are about to be used.
        self.refresh_source()

    def refresh_source(self) -> None:
        """Look at the pane next to Blueprint again, in the background."""
        if self.source:
            self.run_worker(self._read_neighbor, thread=True, group="neighbor", exclusive=True)

    def _read_neighbor(self) -> pane.Source:
        """Ask herdr about the neighbor (in a worker thread) and apply the answer."""
        assert self.source is not None
        fresh = self._refresher(self.source.pane_id)
        if fresh is None:
            # The pane is gone: nobody to ask, but its folder can still be browsed.
            fresh = replace(self.source, agent=None, task=None)
        self.call_from_thread(self._apply_neighbor, fresh)
        return fresh

    async def _apply_neighbor(self, fresh: pane.Source) -> None:
        if fresh == self.source:
            return
        self.source = fresh
        self.refresh_bindings()
        async with self._render_lock:
            # Only the start screen shows the neighbor; never replace a send.
            old = self.query(StartView)
            if self.current is None and old:
                # Mount the new one first, so there is never a blank moment.
                await self.query_one("#body", VerticalScroll).mount(StartView(fresh))
                await old.remove()

    def action_open_file(self) -> None:
        """Pick a doc or diagram from the folder of the pane next to Blueprint."""
        if not (self.source and self.source.cwd):
            return
        folder = self.source.cwd

        def picked(path: Path | None) -> None:
            if path is None:
                return
            title = path.relative_to(folder).as_posix() if path.is_relative_to(folder) else path.name
            # Picking a file is a send from you, with the draw flag set.
            item = Item(kind="file", title=title, path=path, sent_by="you", draw=True)
            self.run_worker(self.open_item(item), group="open")

        self.push_screen(FilePicker(folder, self._finder), picked)

    def action_pick_theme(self) -> None:
        def chosen(name: str | None) -> None:
            if name:
                self.settings.theme = name
                config_store.save(self.settings)

        self.push_screen(ThemePicker(self.theme), chosen)

    def action_move(self, dx: int, dy: int) -> None:
        """Scroll like vim: j/k one line, h/l a few columns."""
        plain = self.query(PlainView)
        if plain:
            plain.first().scroll_relative(x=dx * SIDE_STEP, y=dy, animate=False)
            return
        body = self.query_one("#body", VerticalScroll)
        if dy:
            body.scroll_relative(y=dy, animate=False)
        if not dx:
            return
        if body.max_scroll_x > 0:
            body.scroll_relative(x=dx * SIDE_STEP, animate=False)
            return
        # Inside a document, wide diagrams and code scroll in their own blocks.
        for fence in self.query(MarkdownFence):
            if fence.max_scroll_x > 0:
                fence.scroll_relative(x=dx * SIDE_STEP, animate=False)

    def action_page(self, direction: int) -> None:
        """ctrl+d and ctrl+u: half a screen down or up, like vim."""
        plain = self.query(PlainView)
        target = plain.first() if plain else self.query_one("#body", VerticalScroll)
        half = max(1, target.scrollable_content_region.height // 2)
        target.scroll_relative(y=direction * half, animate=False)

    def action_jump(self, end: bool) -> None:
        """g to the top, G to the bottom."""
        plain = self.query(PlainView)
        target = plain.first() if plain else self.query_one("#body", VerticalScroll)
        if end:
            target.scroll_end(animate=False)
        else:
            target.scroll_home(animate=False)

    async def action_reload(self) -> None:
        async with self._render_lock:
            if self.current is not None:
                await self._open_locked(self.current)
