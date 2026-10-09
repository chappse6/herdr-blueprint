"""The Blueprint viewer: header, document body, history tabs and key hints."""

from __future__ import annotations

import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from . import config as config_store
from .document import DiagramView, DocumentView
from .history import History, Item
from .sources.follow import follow
from .sources.inbox import watch_inbox
from .themes import PALETTES, get_palette, palette_for_theme

WELCOME = """\
# Blueprint

Live Markdown and Mermaid for your agent's side pane.

Save a `.md` or `.mmd` file in this workspace and it shows up here.
Agents can also send a diagram with `herdr-blueprint draw`.

```mermaid
graph LR
  A[Agent writes] --> B[Blueprint follows]
  B --> C[You read it here]
```

| Key | Action |
|---|---|
| `t` | Change theme |
| `←` `→` | Move through history |
| `f` | Follow new files on or off |
| `r` | Reload |
| `q` | Quit |
"""


def ago(at: float) -> str:
    seconds = max(0, int(time.time() - at))
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
        Binding("left", "back", "Back"),
        Binding("right", "forward", "Next"),
        Binding("f", "toggle_follow", "Follow"),
        Binding("r", "reload", "Reload"),
        Binding("q", "quit", "Quit"),
    ]

    following = reactive(True)

    def __init__(self, root: Path, settings: config_store.Config | None = None) -> None:
        super().__init__()
        self.root = root
        self.settings = settings or config_store.load()
        self.history = History()
        for palette in PALETTES.values():
            self.register_theme(palette.textual_theme())

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static("BLUEPRINT", id="brand")
            yield Static("", id="source")
            yield Static("", id="status")
        yield VerticalScroll(id="body")
        yield Static("", id="tabs")
        yield Footer()

    async def on_mount(self) -> None:
        self.theme = get_palette(self.settings.theme).theme_name
        self.following = self.settings.follow
        await self._render_text(WELCOME, diagram=False)
        self.query_one("#source", Static).update("Welcome")
        self.run_worker(self._follow_files(), exclusive=False)
        self.run_worker(self._read_inbox(), exclusive=False)

    # Sources ---------------------------------------------------------------

    async def _follow_files(self) -> None:
        async for item in follow(self.root):
            if self.following:
                await self.open_item(self.history.push(item))
            else:
                self.history.push(item, focus=False)
                self._update_tabs()

    async def _read_inbox(self) -> None:
        async for item in watch_inbox():
            await self.open_item(self.history.push(item))

    # Rendering -------------------------------------------------------------

    async def open_item(self, item: Item | None) -> None:
        if item is None:
            return
        try:
            text = item.read()
        except OSError as exc:
            self.notify(f"Could not read {item.title}: {exc.strerror}", severity="warning")
            return
        await self._render_text(text, diagram=item.is_diagram)
        self.query_one("#source", Static).update(item.title)
        self._update_status()
        self._update_tabs()

    async def _render_text(self, text: str, diagram: bool) -> None:
        body = self.query_one("#body", VerticalScroll)
        await body.remove_children()
        await body.mount(DiagramView(text) if diagram else DocumentView(text))
        body.scroll_home(animate=False)

    def _update_status(self) -> None:
        item = self.history.current
        mark = "◉ follow" if self.following else "○ paused"
        meta = ""
        if item:
            origin = "saved" if item.sent_by == "follow" else f"from {item.sent_by}"
            meta = f"{origin} {ago(item.at)}   "
        self.query_one("#status", Static).update(f"{meta}{mark}")

    def _update_tabs(self) -> None:
        palette = palette_for_theme(self.theme)
        tabs = Text()
        for i, item in enumerate(self.history.items):
            if tabs:
                tabs.append(" · ", style=palette.muted)
            label = ("✎ " if item.kind == "mermaid" else "") + item.title
            current = i == self.history.index
            tabs.append(label, style=f"bold {palette.accent}" if current else palette.muted)
        self.query_one("#tabs", Static).update(tabs)

    def watch_following(self) -> None:
        if self.is_mounted:
            self._update_status()

    def watch_theme(self) -> None:
        if self.is_mounted:
            self._update_tabs()

    # Actions ---------------------------------------------------------------

    def action_pick_theme(self) -> None:
        def chosen(name: str | None) -> None:
            if name:
                self.settings.theme = name
                config_store.save(self.settings)

        self.push_screen(ThemePicker(self.theme), chosen)

    async def action_back(self) -> None:
        await self.open_item(self.history.back())

    async def action_forward(self) -> None:
        await self.open_item(self.history.forward())

    async def action_reload(self) -> None:
        await self.open_item(self.history.current)

    def action_toggle_follow(self) -> None:
        self.following = not self.following
        self.settings.follow = self.following
        config_store.save(self.settings)
