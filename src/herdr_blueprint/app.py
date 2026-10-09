"""The Blueprint viewer: one screen showing the latest thing the worker sent."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from . import config as config_store
from .document import DiagramView, DocumentView, PlainView, as_code
from .item import Item, clean, is_large
from .sources.inbox import Refresh, watch_inbox
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
| `t` | Change theme |
| `r` | Reload |
| `q` | Quit |
"""

# Below this width the header drops the brand and uses a short status.
NARROW_WIDTH = 60


def ago(at: float, now: float | None = None) -> str:
    seconds = max(0, int((time.time() if now is None else now) - at))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


class ThemePicker(ModalScreen[str | None]):
    """Pick a palette. Moving the cursor previews it; Esc puts the old one back."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: str) -> None:
        super().__init__()
        self.original = current

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static("Theme", id="picker-title")
            yield OptionList(*(Option(p.label, id=p.name) for p in PALETTES.values()))
            yield Static("↑↓ preview   ⏎ keep   esc cancel", id="picker-hint")

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.highlighted = list(PALETTES).index(palette_for_theme(self.original).name)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.app.theme = get_palette(event.option.id).theme_name

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.app.theme = self.original
        self.dismiss(None)


class BlueprintApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "Blueprint"
    BINDINGS = [
        Binding("t", "pick_theme", "Theme"),
        Binding("r", "reload", "Reload"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, settings: config_store.Config | None = None) -> None:
        super().__init__()
        self.settings = settings or config_store.load()
        self.current: Item | None = None
        self.notice: str | None = None
        self.title_text = "Welcome"
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
        # The welcome example is tiny and ours, so it is always drawn.
        await self._render_text(WELCOME, diagram=False, draw=True, plain=False)
        self._update_status()
        self.run_worker(self._read_inbox(), exclusive=False, exit_on_error=False)
        self.set_interval(30, self._update_status)

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

    def _show_notice(self, message: str) -> None:
        self.notice = message
        self._update_status()

    # Rendering -------------------------------------------------------------

    async def open_item(self, item: Item) -> None:
        """Replace the screen with `item`. The previous one is dropped."""
        async with self._render_lock:
            try:
                text = item.read()
            except OSError:
                # Keep the last content on screen; say what went wrong in the header.
                self._show_notice(f"Cannot read {clean(item.title)}")
                return
            drawn_diagram = item.is_diagram and item.draw
            plain = not drawn_diagram and is_large(text)
            self.notice = "Large file, shown as plain text" if plain else None
            await self._render_text(text, diagram=item.is_diagram, draw=item.draw, plain=plain)
            self.current = item
            self.title_text = clean(item.title)
            self._update_status()

    async def refresh_current(self, draw: bool) -> None:
        """Re-read and redraw what is on screen, with the worker's draw flag."""
        if self.current is not None:
            await self.open_item(replace(self.current, draw=draw, at=time.time()))

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
        warning = f"bold {palette.warning}"
        if narrow and self.notice:
            source.update(Text(f"⚠ {self.notice}", style=warning))
        else:
            source.update(Text(self.title_text))
        status = Text()
        if self.notice and not narrow:
            status.append(f"⚠ {self.notice}", style=warning)
        elif self.current and narrow:
            status.append(ago(self.current.at).removesuffix(" ago").replace("just ", ""))
        elif self.current:
            status.append(f"from {self.current.sent_by} {ago(self.current.at)}")
        status_bar.update(status)

    def on_resize(self) -> None:
        if self.is_mounted:
            self._update_status()

    # Actions ---------------------------------------------------------------

    def action_pick_theme(self) -> None:
        def chosen(name: str | None) -> None:
            if name:
                self.settings.theme = name
                config_store.save(self.settings)

        self.push_screen(ThemePicker(self.theme), chosen)

    async def action_reload(self) -> None:
        if self.current is not None:
            await self.open_item(self.current)
