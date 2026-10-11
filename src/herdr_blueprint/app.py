"""The Blueprint viewer: one screen showing the latest thing the worker sent."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Region
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.markdown import MarkdownFence
from textual.widgets.option_list import Option

from . import config as config_store
from . import edit, point
from .diagram import MAX_DIAGRAM_CHARS
from .document import DiagramFence, DiagramView, DocumentView, PlainView, as_code
from .item import MAX_BYTES, Item, clean, is_large
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
| `e` | Point at a part and ask |
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
# How long "sent a new version" stays in the banner.
BANNER_SECONDS = 6
# Seconds after asking the agent to draw before `a` asks again: a held key sends once.
ASK_COOLDOWN = 10
# Unanswered checks in a row before the editor pane counts as closed by hand.
PANE_MISSES = 2


def scroll_into_view(widget: Widget, region: Region) -> None:
    """Scroll every container above `widget` until `region` (cells of its content) shows:
    Textual's scroll_to_widget, for part of a widget."""
    gutter = widget.styles.gutter
    region = region.translate(widget.virtual_region.offset).translate((gutter.left, gutter.top))
    while isinstance(widget.parent, Widget):
        container = widget.parent
        moved = container.scroll_to_region(region, animate=False, immediate=True)
        region = (
            region.translate(-moved)
            .translate(container.styles.margin.top_left)
            .translate(container.styles.border.spacing.top_left)
            .translate(container.virtual_region_with_margin.offset)
        )
        widget = container


def ago(at: float, now: float | None = None) -> str:
    seconds = max(0, int((time.time() if now is None else now) - at))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


@dataclass
class KeptEdit:
    """An edit the agent has not received yet; the next `e` opens it again."""

    item: Item
    base: str  # the text the person started from
    base_bytes: bytes | None  # the file as it was at `e`; None for text an agent sent
    edited: str


@dataclass
class LiveEdit:
    """The editor running in the pane below Blueprint, on a copy in `folder`."""

    item: Item
    kept: "KeptEdit | None"
    base: str
    base_bytes: bytes | None
    folder: Path
    copy: Path
    before: tuple[int, int]  # the copy's (mtime_ns, size) when the editor started
    pane_id: str
    stop: asyncio.Event
    misses: int = 0  # pane checks in a row that herdr didn't answer
    polling: bool = False  # the file watcher failed: the pane check reads the copy instead


@dataclass
class Pending:
    """A request that reached the agent, shown in the banner until its new version arrives."""

    agent: str
    described: str
    at: float
    is_edit: bool
    part_id: str | None = None  # the part a note was about


@dataclass
class KeptNote:
    """A note about a part that the agent has not received yet."""

    item: Item
    part_id: str
    text: str
    label: str = ""  # the part's label then: a note follows its part when blocks move


@dataclass
class Request:
    """Something for the agent: an edit, or a note about a part."""

    key: tuple  # where its kept copy lives in BlueprintApp._kept
    item: Item
    described: str  # "your edit", 'the box "Tollgate"'
    build: Callable[[pane.Source], str]  # the prompt for the agent that gets it
    kept: KeptEdit | KeptNote

    @property
    def is_edit(self) -> bool:
        return isinstance(self.kept, KeptEdit)


class NoteLine(Input):
    """One line under the drawing: what should change at the pointed part."""

    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, value: str = "") -> None:
        super().__init__(value=value, placeholder="What should change here?  (Enter asks, Esc goes back)")

    def action_cancel(self) -> None:
        self.app.call_later(self.app.note_cancelled)


def same_item(a: Item | None, b: Item | None) -> bool:
    """The same document, even after a reload made a new Item for it."""
    return a is not None and b is not None and doc_key(a) == doc_key(b)


def doc_key(item: Item) -> tuple:
    """What an unsent edit belongs to: a file by its path, sent text by its content."""
    if item.path is not None:
        return ("file", item.path.resolve())
    return ("text", item.kind, item.title, item.source)


# Signals the terminal sends to the whole foreground group: while the editor runs
# they are the editor's (Ctrl+C, Ctrl+\\, Ctrl+Z), not Blueprint's. git does the same.
class AgentPicker(ModalScreen[str | None]):
    """No agent next to Blueprint, but a shell: start one to receive the edit?"""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", show=False),
        Binding("j", "cursor(1)", show=False),
        Binding("k", "cursor(-1)", show=False),
    ]

    def __init__(self, kinds: list[str], what: str = "edit") -> None:
        super().__init__()
        self.kinds = kinds
        self.what = what

    def compose(self) -> ComposeResult:
        with Vertical(id="agents"):
            yield Static(f"Start an agent next to Blueprint and send your {self.what}?", id="agents-title")
            options = [Option(f"Start {kind}", id=kind) for kind in self.kinds]
            yield OptionList(*options, Option("Don't send", id=""))
            yield Static("j/k move   ⏎ choose   esc don't send", id="agents-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id or None)

    def action_cursor(self, step: int) -> None:
        options = self.query_one(OptionList)
        if step > 0:
            options.action_cursor_down()
        else:
            options.action_cursor_up()

    def action_cancel(self) -> None:
        self.dismiss(None)


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
        # Only offered when there is something to point at; e again edits the text.
        Binding("e", "edit", "Edit"),
        Binding("e", "edit_text", "Edit text"),  # while pointing: edit the text at the part
        # Point mode only.
        Binding("enter", "point_ask", "Ask", key_display="⏎"),
        Binding("c", "jump_code", "Code"),  # pointing at a part tied to code: open that code
        Binding("escape", "point_done", "Done", key_display="esc"),
        # Only offered when Blueprint knows the pane it was opened next to.
        Binding("a", "ask", "Ask"),
        Binding("o", "open_file", "Open"),
        Binding("t", "pick_theme", "Theme"),
        Binding("r", "reload", "Reload"),
        Binding("q", "leave", "Quit"),
    ]

    def __init__(
        self,
        settings: config_store.Config | None = None,
        source: pane.Source | None = None,
        *,
        asker: Callable[[pane.Source], tuple[bool, str]] | None = None,
        finder: Callable[[Path], list[Path]] | None = None,
        refresher: Callable[[str], pane.Source | None] | None = None,
        editor: Callable[[list[str]], int] | None = None,
        find_editor: Callable[[], list[str] | None] | None = None,
        prompter: Callable[[pane.Source, str], tuple[bool, str]] | None = None,
        starter: Callable[[pane.Source, str], tuple[bool, str]] | None = None,
        idle_shell: Callable[[str], bool] | None = None,
        kinds: Callable[[], list[str]] | None = None,
        pane_editor: Callable[[list[str], Path], str | None] | None = None,
        focus_pane: Callable[[str], None] | None = None,
        pane_alive: Callable[[str], bool] | None = None,
        ask_on_open: bool = True,
    ) -> None:
        super().__init__()
        self.settings = settings or config_store.load()
        # The pane Blueprint was opened next to; None outside herdr.
        self.source = source
        herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
        self._asker = asker or (lambda src: pane.ask_to_draw(herdr, src))
        self._finder = finder or pane.find_documents
        self._refresher = refresher or (lambda pane_id: pane.source_of(herdr, pane_id))
        # Editing: the editor runs in this pane; the change goes to the agent.
        self._editor = editor or self._run_editor
        self._find_editor = find_editor or edit.editor_command
        self._prompter = prompter or (lambda src, text: pane.send_prompt(herdr, src, text))
        self._starter = starter or (lambda src, kind: pane.start_agent(herdr, src, kind))
        self._idle_shell = idle_shell or (lambda pane_id: pane.shell_is_idle(herdr, pane_id))
        self._kinds = kinds or edit.kinds_on_path
        # The live editor pane (inside herdr): open, focus, and check it.
        own_pane = os.environ.get("HERDR_PANE_ID")
        self._pane_editor = pane_editor or (
            lambda command, copy: pane.open_editor_pane(herdr, own_pane, command, copy) if self.source else None
        )
        self._focus_pane = focus_pane or (lambda pane_id: pane.focus_plugin_pane(herdr, pane_id))
        self._pane_alive = pane_alive or (lambda pane_id: pane.pane_exists(herdr, pane_id))
        # `c`: the code behind a part, opened in the editor pane (its exit code goes to the temp folder).
        jump_exit = Path(tempfile.gettempdir()) / f"blueprint-code-{os.getpid()}.exit"
        self._code_pane = (
            lambda command, path: pane.open_editor_pane(herdr, own_pane, command, path, exit_path=jump_exit)
            if self.source else None
        )
        self._live: LiveEdit | None = None
        self._held: Item | None = None  # the newest send that arrived during a live edit or a note
        self._noting = False  # the note line is open: sends wait for it
        self._opening = False  # herdr is opening the editor pane: sends and e wait for it
        self._auto_asked = not ask_on_open  # asked the neighbor to draw once, when Blueprint opened
        self._waiting_to_ask = False
        self._asked_at = float("-inf")  # when `a` last asked (or the ask is still going)
        self.stale_files: list[str] = []  # code behind the drawing changed since it was sent
        self.pending: Pending | None = None  # what the banner says went to the agent
        self._live_timer = None
        self.preview_text: str | None = None  # what the live preview shows
        # Unsent edits, one per document; the next `e` on that document reopens it.
        self._kept: dict[tuple, KeptEdit | KeptNote] = {}
        self._sending = False
        self._closing = False
        # Long prompt bodies for the agent; removed when Blueprint closes.
        self._attachments: Path | None = None
        self._shown_key: tuple | None = None
        # What is on screen, for pointing: its text and whether a diagram is drawn.
        self._shown_text = ""
        self._shown_drawn = False
        # Point mode.
        self.pointing = False
        self.parts: list[point.Part] = []
        self.selected = 0
        self._part_widgets: list = []  # document blocks, parallel to parts
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
        yield Static("", id="banner")
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
        self.set_interval(30, self._show_pending)  # "2m ago" moves on
        if self.source:
            self.set_interval(NEIGHBOR_SECONDS, self.refresh_source)
            self.set_interval(NEIGHBOR_SECONDS, self.check_freshness)
            self._auto_ask()

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

    @property
    def editing(self) -> bool:
        return self._live is not None

    async def open_item(self, item: Item) -> None:
        """Replace the screen with `item`. The previous one is dropped."""
        if self._live is not None or self._opening:
            self._held = item  # shown, or dropped, when the editor closes
            return
        if self._noting:
            # The agent redraws after each piece of work; never swap the screen mid-sentence.
            self._held = item
            self._show_notice("An update is waiting; it shows when you close the note", ok=True)
            return
        async with self._render_lock:
            await self._open_locked(item)

    def _release_held(self) -> None:
        """The note line closed: show the send that waited for it."""
        if self._held is not None and self._live is None and not self._noting and not self._opening:
            item, self._held = self._held, None
            self.run_worker(self.open_item(item), group="open", exit_on_error=False)

    async def refresh_current(self, draw: bool) -> None:
        """Re-read and redraw what is on screen, with the worker's draw flag."""
        if self._live is not None:
            return  # the editor is open on what is shown
        if self._noting and self.current is not None:
            self._held = self._held or replace(self.current, draw=draw, at=time.time())
            return
        # Read `current` under the lock, so a send in progress is not undone.
        async with self._render_lock:
            if self.current is not None:
                await self._open_locked(replace(self.current, draw=draw, at=time.time()))

    async def _open_locked(self, item: Item) -> bool:
        """Show `item`; True when that dropped unsent work kept for sent text."""
        previous = self.current
        if self.pointing:
            self._end_point()  # a send replaces the screen; a typed note is kept
        try:
            text = item.read()
        except OSError:
            # Keep the last content on screen; say what went wrong in the header.
            self._show_notice(f"Cannot read {clean(item.title)}")
            return False
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
        self._shown_text, self._shown_drawn = text, item.is_diagram and draw
        self.preview_text = None
        self.current = item
        self.title_text = clean(item.title)
        previous_key = self._shown_key
        self._shown_key = doc_key(item)
        dropped = []
        if previous_key and previous_key[0] == "text" and previous_key != self._shown_key and not self._sending:
            # Sent text can't be shown again, so work kept for it can't be picked up. A new
            # version of the same note (the agent redraws as it works) takes its notes over;
            # an edit is dropped all the same: sending it would undo the agent's update.
            newer = previous is not None and item.kind != "file" and bool(item.title) and item.title == previous.title
            for key, kept in list(self._kept.items()):
                if newer and isinstance(kept, KeptNote) and doc_key(kept.item) == previous_key:
                    del self._kept[key]
                    self._kept[(self._shown_key, kept.part_id)] = replace(kept, item=item)
            dropped = [key for key, kept in self._kept.items() if doc_key(kept.item) == previous_key]
            for key in dropped:
                title = self._kept.pop(key).item.title
            if dropped:
                self.notice = f"Your unsent work on '{clean(title)}' was dropped"
                self.notice_ok = False
        self.stale_files = []
        self._arrived(item)
        self.refresh_bindings()
        self._update_status()
        return bool(dropped)

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
        elif self.stale_files and self.current:
            count = len(self.stale_files)
            stale = "⚠ code changed" if narrow else f"⚠ code changed since drawn ({count} file{'s' if count > 1 else ''}) · a redraws"
            status.append(stale, style=f"bold {palette.warning}")
        elif self.current and narrow:
            status.append(ago(self.current.at).removesuffix(" ago").replace("just ", ""))
        elif self.current:
            status.append(f"from {self.current.sent_by} {ago(self.current.at)}")
        status_bar.update(status)

    def check_freshness(self) -> None:
        """Note the files behind the drawing (its `%% code:` lines) changed since it was sent."""
        stale: list[str] = []
        item, folder = self.current, self.source.cwd if self.source else None
        if item is not None and folder is not None:
            files = dict.fromkeys(re.sub(r":\d+(-\d+)?$", "", where.strip()) for where in point.code_links(self._shown_text).values())
            for name in files:
                try:
                    if (folder / name).stat().st_mtime > item.at:
                        stale.append(name)
                except OSError:
                    continue
        if stale != self.stale_files:
            self.stale_files = stale
            self._update_status()

    def on_resize(self) -> None:
        if self.is_mounted:
            self._update_status()

    # Actions ---------------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("point_ask", "point_done"):
            return self.pointing
        if action == "jump_code":
            part = self.parts[self.selected] if self.pointing and self.parts else None
            return bool(part and part.code) and not self._noting
        if self.pointing and action in ("ask", "open_file", "pick_theme", "reload", "leave"):
            return False  # point mode offers only its own keys
        # Hidden from the footer and the keys do nothing when there is no pane to use.
        if self._live is not None and action in ("reload", "open_file"):
            return False  # the editor is open on what is shown
        if action == "ask":
            return bool(self.source and self.source.agent)
        if action == "open_file":
            return bool(self.source and self.source.cwd)
        if action == "edit":
            return not self.pointing and self._editable()
        if action == "edit_text":
            return self.pointing and self._editable()
        return True

    async def action_leave(self) -> None:
        """q quits, outside point mode; ctrl+q always does."""
        await self.action_quit()

    def _auto_ask(self) -> None:
        """Opened next to an agent: ask it once to draw what it is doing. A working agent
        queues the request; one waiting at an approval or question dialog is asked after."""
        source = self.source
        if self._auto_asked or self.current is not None or not (source and source.agent):
            return
        if source.status == "blocked":
            if not self._waiting_to_ask:
                self._waiting_to_ask = True
                self._show_notice(f"{source.agent} is waiting for your answer; Blueprint asks it to draw after", ok=True)
            return
        self._auto_asked = True
        self.action_ask()

    def action_ask(self) -> None:
        """Ask the agent next to Blueprint to draw what it is doing, once in a while."""
        if not (self.source and self.source.agent):
            return
        if time.time() - self._asked_at < ASK_COOLDOWN:
            return  # just asked (a held key, or a second press): the agent is on it
        self._asked_at = time.time()
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
        self.call_from_thread(self._asked, ok, message)

    def _asked(self, ok: bool, message: str) -> None:
        if not ok:
            self._asked_at = float("-inf")  # nothing reached the agent: a may try again now
        self._show_notice(message, ok)

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
            self.source = fresh  # the same neighbor; its status may have changed
            self._auto_ask()
            return
        self.source = fresh
        self.refresh_bindings()
        self._auto_ask()
        async with self._render_lock:
            # Only the start screen shows the neighbor; never replace a send.
            old = self.query(StartView)
            if self.current is None and old:
                # Mount the new one first, so there is never a blank moment.
                await self.query_one("#body", VerticalScroll).mount(StartView(fresh))
                await old.remove()

    # Editing ------------------------------------------------------------------

    def _kept_for(self, item: Item | None) -> KeptEdit | None:
        kept = self._kept.get(doc_key(item)) if item is not None else None
        return kept if isinstance(kept, KeptEdit) else None

    def _editable(self) -> bool:
        if self._opening:
            return False
        if self._live is not None:
            return True  # e focuses the editor pane
        item = self.current
        if item is None or self._sending or self.query(PlainView):
            return False
        if self.preview_text is not None:
            # A sent edit's preview: wait for the agent, unless the send failed and the edit is kept.
            return self._kept_for(item) is not None
        if item.kind != "file" or self._kept_for(item):
            return True
        try:
            return item.path is not None and item.path.stat().st_size <= MAX_BYTES
        except OSError:
            return False

    def _pointable(self) -> bool:
        """Point mode needs the document itself on screen, not an edit's preview."""
        return self._live is None and self.preview_text is None and self._editable()

    def action_edit_text(self) -> None:
        self.action_edit()

    def action_edit(self) -> None:
        """First e points at a part; e while pointing edits the text there."""
        if self._live is not None:
            self._focus_pane(self._live.pane_id)
            return
        if not self._editable():
            return
        if not self.pointing:
            if not (self._pointable() and self._start_point()):
                self._edit_text()  # nothing to point at: edit the text straight away
            return
        part = self.parts[self.selected] if self.parts else None
        line = part.lines[0] if part and part.lines and part.kind != "diagram" else None
        self._end_point()
        self._edit_text(line)

    def _edit_text(self, line: int | None = None) -> None:
        """Open what is shown in the editor. Save and quit sends it; quit without saving doesn't."""
        item = self.current
        if item is None:
            return
        kept = self._kept_for(item)
        if kept:
            base, base_bytes, start = kept.base, kept.base_bytes, kept.edited
        else:
            try:
                base, base_bytes = self._edit_base(item)
            except OSError:
                self._show_notice(f"Cannot read {clean(item.title)}")
                return
            start = base
        try:
            command = self._find_editor()
        except edit.EditorError as exc:
            self._show_notice(clean(str(exc)))
            return
        if not command:
            self._show_notice("No editor found: set $EDITOR")
            return
        command = [*command, *edit.line_arg(command, line)]
        self._start_live(item, kept, base, base_bytes, start, command)

    def _borrow_pane(
        self, item: Item, kept: KeptEdit | None, base: str, base_bytes: bytes | None, text: str, command: list[str],
    ) -> None:
        """No editor pane: hand this pane's terminal to the editor."""
        outcome = self._edit_copy(item, text, command)
        if outcome is not None:
            self._after_edit(item, kept, base, base_bytes, *outcome)
        self._release_held()

    def _after_edit(
        self, item: Item, kept: KeptEdit | None, base: str, base_bytes: bytes | None, saved: bool, edited: str,
    ) -> bool:
        """Act on the editor's result; True when the change went off to the agent."""
        key = doc_key(item)
        if not saved:
            # :q! keeps whatever was waiting; nothing is sent.
            self._show_notice("Not saved. Your edit is kept; press e twice to edit it again" if kept else "No changes")
            return False
        if kept and not edited.strip():
            del self._kept[key]
            self._show_notice("Edit thrown away")
            return False
        if edit.same_text(edited, base):
            self._kept.pop(key, None)
            self._show_notice("No changes")
            return False
        kept_edit = KeptEdit(item, base, base_bytes, edited)
        self._kept[key] = kept_edit
        self._send_request(Request(key, item, "your edit", lambda target: self._prompt_for(kept_edit, target), kept_edit))
        return True

    # The live editor pane -------------------------------------------------------

    def _start_live(
        self, item: Item, kept: KeptEdit | None, base: str, base_bytes: bytes | None, text: str, command: list[str],
    ) -> None:
        """Open the editor in a pane below, with a live preview; else borrow this pane.
        herdr can take a moment, so the pane opens in the background."""
        name = edit.copy_name(item.path.name if item.path else item.title, diagram=item.is_diagram)
        try:
            folder = Path(tempfile.mkdtemp(prefix="blueprint-edit-"))
            copy = folder / name
            copy.write_text(text, encoding="utf-8", newline="")
            stat = copy.stat()
        except OSError:
            self._borrow_pane(item, kept, base, base_bytes, text, command)
            return
        self._opening = True
        self.refresh_bindings()
        self._show_notice("Opening the editor…", ok=True)

        def open_pane() -> None:
            try:
                pane_id = self._pane_editor(command, copy)
            except Exception:
                pane_id = None
            try:
                self.call_from_thread(self._editor_opened, pane_id, item, kept, base, base_bytes, text, command, copy, stat)
            except RuntimeError:
                shutil.rmtree(folder, ignore_errors=True)  # Blueprint closed meanwhile

        self.run_worker(open_pane, thread=True, group="editor-pane", exit_on_error=False)

    def _editor_opened(
        self, pane_id: str | None, item: Item, kept: KeptEdit | None, base: str, base_bytes: bytes | None,
        text: str, command: list[str], copy: Path, stat,
    ) -> None:
        self._opening = False
        self.refresh_bindings()
        folder, name = copy.parent, copy.name
        if not pane_id or self._closing:
            shutil.rmtree(folder, ignore_errors=True)
            if not self._closing:
                self._borrow_pane(item, kept, base, base_bytes, text, command)
            return
        live = LiveEdit(item, kept, base, base_bytes, folder, copy, (stat.st_mtime_ns, stat.st_size), pane_id, asyncio.Event())
        self._live = live
        self._show_notice(f"Editing {name}: save to preview, quit to finish", ok=True)
        self.run_worker(self._watch_live(live), group="live", exit_on_error=False)
        self._live_timer = self.set_interval(2, self._check_live_pane)
        self.refresh_bindings()

    async def _watch_live(self, live: LiveEdit) -> None:
        from watchfiles import awatch

        done = edit.exit_file(live.copy)
        try:
            async for changes in awatch(live.folder, stop_event=live.stop, debounce=50, step=50):
                names = {Path(path).name for _change, path in changes}
                if done.name in names or done.exists():
                    await self._finish_live(live)
                    return
                if live.copy.name in names:
                    await self._live_redraw(live)
        except Exception:
            live.polling = True  # the pane check reads the copy from now on

    async def _live_redraw(self, live: LiveEdit) -> None:
        try:
            text = live.copy.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if self._live is not live or text == (self.preview_text or live.base):
            return
        self.preview_text = text
        async with self._render_lock:
            await self._render_text(clean(text), diagram=live.item.is_diagram, draw=live.item.draw or self._shown_drawn, plain=False)

    def _check_live_pane(self) -> None:
        live = self._live
        if live is None:
            return

        def check() -> None:
            if edit.exit_file(live.copy).exists():
                self.call_from_thread(self._finish_live, live)
            elif self._pane_alive(live.pane_id):
                live.misses = 0
                if live.polling:
                    self.call_from_thread(self._live_redraw, live)
            else:
                # herdr can miss one answer (a timeout); twice in a row, the pane is gone.
                live.misses += 1
                if live.misses >= PANE_MISSES:
                    self.call_from_thread(self._finish_live, live, True)

        self.run_worker(check, thread=True, group="live-check", exclusive=True, exit_on_error=False)

    async def _finish_live(self, live: LiveEdit, closed: bool = False) -> None:
        if self._live is not live:
            return
        self._live = None
        live.stop.set()
        if self._live_timer is not None:
            self._live_timer.stop()
        try:
            code = None if closed else int(edit.exit_file(live.copy).read_text(encoding="utf-8").strip() or 1)
        except (OSError, ValueError):
            code = 1
        try:
            stat = live.copy.stat()
            saved = (stat.st_mtime_ns, stat.st_size) != live.before
            edited = live.copy.read_text(encoding="utf-8", errors="replace", newline="")
        except OSError:
            saved, edited = False, live.base
        shutil.rmtree(live.folder, ignore_errors=True)
        held, self._held = self._held, None
        sent = False
        if closed:
            if saved and not edit.same_text(edited, live.base):
                self._kept[doc_key(live.item)] = KeptEdit(live.item, live.base, live.base_bytes, edited)
                self._show_notice("The editor closed. Your saved changes are kept; press e twice to send them")
            else:
                self._show_notice("The editor closed")
        elif code == 127:
            self._show_notice("Could not run the editor")
        elif code != 0:
            self._show_notice("Edit cancelled")
        else:
            sent = self._after_edit(live.item, live.kept, live.base, live.base_bytes, saved, edited)
        preview = edited.replace("\r\n", "\n")  # as the live redraws read it; `edited` keeps the bytes for the diff
        if sent and self.preview_text != preview:
            # Saved and quit faster than a redraw: show what was sent.
            self.preview_text = preview
            async with self._render_lock:
                await self._render_text(clean(preview), diagram=live.item.is_diagram, draw=live.item.draw or self._shown_drawn, plain=False)
        if not sent:
            # Back to the original, or the newest send that waited; keep the message
            # unless that send dropped the work it talks about.
            notice, ok = self.notice, self.notice_ok
            self.preview_text = None
            async with self._render_lock:
                dropped = await self._open_locked(held or live.item)
            if notice and not dropped:
                self._show_notice(notice, ok)
        self.refresh_bindings()

    def _edit_base(self, item: Item) -> tuple[str, bytes | None]:
        if item.kind != "file":
            return item.source or "", None
        assert item.path is not None
        data = item.path.read_bytes()
        if len(data) > MAX_BYTES:
            raise OSError("too large to edit")
        return data.decode("utf-8-sig", errors="replace"), data

    def _edit_copy(self, item: Item, text: str, command: list[str]) -> tuple[bool, str] | None:
        """Run the editor on a copy: (saved, text), or None when it was cancelled or failed."""
        name = edit.copy_name(item.path.name if item.path else item.title, diagram=item.is_diagram)
        try:
            folder = Path(tempfile.mkdtemp(prefix="blueprint-edit-"))
        except OSError as exc:
            self._show_notice(f"Cannot make a copy to edit: {clean(str(exc))}")
            return None
        try:
            copy = folder / name
            copy.write_text(text, encoding="utf-8", newline="")
            before = copy.stat()
            if self._editor([*command, str(copy)]) != 0:
                self._show_notice("Edit cancelled")
                return None
            after = copy.stat()
            saved = (after.st_mtime_ns, after.st_size) != (before.st_mtime_ns, before.st_size)
            return saved, copy.read_text(encoding="utf-8", errors="replace", newline="")
        except SuspendNotSupported:
            self._show_notice("This terminal cannot run an editor from Blueprint")
        except (OSError, ValueError) as exc:
            self._show_notice(f"Could not run the editor: {clean(str(exc))}")
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        return None

    def _run_editor(self, command: list[str]) -> int:
        """Hand the terminal to the editor. Nothing may escape the suspend block:
        Textual would skip taking the terminal back, and Blueprint would hang."""
        error: OSError | None = None
        code = 1
        with self.suspend():
            try:
                process = subprocess.Popen(command)
            except OSError as exc:
                error = exc
            else:
                with edit.signals_ignored():
                    code = process.wait()
        if error is not None:
            raise error
        return code

    # Sending to the agent -------------------------------------------------------

    def _send_request(self, request: Request) -> None:
        self._sending = True
        self.refresh_bindings()
        agent = self.source.agent if self.source and self.source.agent else None
        self._show_notice("Sending your edit…" if request.is_edit else f"Asking {agent or 'the agent'}…", ok=True)
        self.run_worker(lambda: self._deliver(request), thread=True, group="edit", exit_on_error=False)

    def _deliver(self, request: Request) -> None:
        """Find the agent and send (worker thread). Never raises."""
        try:
            if self.source is None:
                result = (False, "No agent next to Blueprint")
            else:
                fresh = self._read_neighbor()
                if self._closing:
                    return
                if fresh.agent:
                    result = self._prompter(fresh, edit.prompt_safe(request.build(fresh)))
                elif self._idle_shell(fresh.pane_id) and (kinds := self._kinds()):
                    self.call_from_thread(self._offer_agent, request, fresh, kinds)
                    return
                else:
                    result = (False, "No agent next to Blueprint")
        except Exception as exc:
            result = (False, f"Could not send: {exc}")
        self._after_send(request, *result)

    def _offer_agent(self, request: Request, fresh: pane.Source, kinds: list[str]) -> None:
        def chosen(kind: str | None) -> None:
            if not kind:
                self._sent(request, False, "No agent started")
                return
            self._show_notice(f"Starting {kind}…", ok=True)
            self.run_worker(
                lambda: self._start_and_send(request, fresh, kind), thread=True, group="edit", exit_on_error=False,
            )

        self.push_screen(AgentPicker(kinds, "edit" if request.is_edit else "note"), chosen)

    def _start_and_send(self, request: Request, fresh: pane.Source, kind: str) -> None:
        try:
            started, message = self._starter(fresh, kind)
            if started and not self._closing:
                ready = self._read_neighbor()
                target = ready if ready.agent else replace(fresh, agent=kind)
                if self._closing:
                    return
                started, message = self._prompter(target, edit.prompt_safe(request.build(target)))
        except Exception as exc:
            started, message = False, f"Could not start {kind}: {exc}"
        self._after_send(request, started, message)

    def _after_send(self, request: Request, ok: bool, message: str) -> None:
        if self._closing:
            return
        try:
            self.call_from_thread(self._sent, request, ok, message)
        except RuntimeError:
            pass  # Blueprint closed meanwhile

    def _sent(self, request: Request, ok: bool, message: str) -> None:
        self._sending = False
        here = same_item(request.item, self.current)
        if ok:
            if self._kept.get(request.key) is request.kept:
                del self._kept[request.key]
            agent = message.removeprefix("Sent to ") if message.startswith("Sent to ") else "the agent"
            text = f"Sent your edit to {agent}" if request.is_edit else f"Asked {agent} about {request.described}"
            self._show_notice(text, ok=True)
            part_id = None if request.is_edit else request.key[1]
            self.pending = Pending(agent, request.described, time.time(), request.is_edit, part_id)
            self._show_pending()
            if part_id and here:
                self._mark(part_id)
            self.refresh_bindings()
            return
        self.pending = None
        self._set_banner(f"⚠ Not sent: {clean(message)}", ok=False)
        if here and not request.is_edit:
            self._show_notice(f"{clean(message)}. Your note was kept; point at it and press Enter to send it again")
        elif here:
            again = "press e" if self.preview_text is not None else "press e twice"  # a preview edits on the first e
            self._show_notice(f"{clean(message)}. Your edit was not sent; {again} to edit it again")
        elif doc_key(request.item)[0] == "file":
            where = clean(request.item.title)
            if request.is_edit:
                self._show_notice(f"{clean(message)}. Your edit was not sent; open {where} again and press e twice")
            else:
                self._show_notice(f"{clean(message)}. Your note was kept; open {where} again, point at it and press Enter")
        else:
            if self._kept.get(request.key) is request.kept:
                del self._kept[request.key]
            what = "edit of" if request.is_edit else "note on"
            self._show_notice(f"{clean(message)}. Your {what} '{clean(request.item.title)}' was not sent and was dropped")
        self.refresh_bindings()

    # The banner -----------------------------------------------------------------

    def _set_banner(self, text: str | None, ok: bool = True) -> None:
        try:
            banner = self.query_one("#banner", Static)
        except NoMatches:
            return
        banner.display = bool(text)
        banner.set_class(not ok, "-warn")
        banner.update(Text(text or ""))

    def _show_pending(self) -> None:
        pending = self.pending
        if pending is None:
            return
        if pending.is_edit:
            rest = "showing your version" if self.preview_text is not None else "the agent's version comes next"
            self._set_banner(f"✓ Sent your edit to {pending.agent} {ago(pending.at)} · {rest}")
        else:
            self._set_banner(f"✓ Asked {pending.agent} about {pending.described} {ago(pending.at)} · waiting for the new version")

    def _mark(self, part_id: str) -> None:
        """Mark the part a note was sent about, in the drawing or as a document block."""
        parts = self._compute_parts()
        index = next((i for i, part in enumerate(parts) if part.id == part_id), None)
        if index is not None:
            part, views = parts[index], self.query(DiagramView)
            if views and part.boxes:
                views.first().mark(part)
            elif index < len(self._part_widgets):
                widget = self._part_widgets[index]
                if part.boxes and isinstance(widget, DiagramFence):
                    widget.mark(part)
                else:
                    widget.add_class("requested")
        if not self.pointing:
            self._part_widgets = []

    def _arrived(self, item: Item) -> None:
        """A new version from the agent after a request: say so, briefly."""
        pending = self.pending
        if pending is None:
            return
        if item.sent_by == "you":
            self.pending = None  # another document: the banner was about the last one
            self._set_banner(None)
            return
        if item.at <= pending.at:
            return  # the same version again (r)
        self.pending = None
        self._set_banner(f"✓ {pending.agent} sent a new version")
        self.set_timer(BANNER_SECONDS, lambda: self._set_banner(None) if self.pending is None else None)

    def _prompt_for(self, kept: KeptEdit, target: pane.Source) -> str:
        item = kept.item
        if item.kind == "file" and item.path is not None:
            label = edit.display_path(item.path, target.cwd)
            diff = edit.unified_diff(kept.base, kept.edited, label)
            try:
                diverged = item.path.read_bytes() != kept.base_bytes
            except OSError:
                diverged = True
            stem = edit.copy_name(item.path.name, diagram=False).removesuffix(".md")
            attachment = self._attach(diff, stem, ".diff") if edit.needs_attachment(diff) else None
            return edit.file_prompt(
                label, diff, diverged=diverged, attachment=attachment,
                tied_to_code=edit.touches_code(kept.base, kept.edited, diagram=item.is_diagram),
            )
        source = kept.edited
        stem, suffix = edit.copy_name(item.title, diagram=item.is_diagram).rsplit(".", 1)
        attachment = self._attach(source, stem, f".{suffix}") if edit.needs_attachment(source) else None
        return edit.text_prompt(
            clean(item.title), source, diagram=item.is_diagram, attachment=attachment,
            tied_to_code=edit.touches_code(kept.base, source, diagram=item.is_diagram),
        )

    # Point mode -----------------------------------------------------------------

    def _compute_parts(self) -> list[point.Part]:
        self._part_widgets = []
        views = self.query(DiagramView)
        if views:
            view = views.first()
            parts = point.diagram_parts(view.source, view.drawing.padding, view.drawing.text) if view.drawing else []
            return parts or [point.whole_diagram(view.source)]
        if self.current is not None and self.current.is_diagram:
            return [point.whole_diagram(self._shown_text)]  # shown as text, not drawn
        documents = self.query(DocumentView)
        if documents:
            blocks = [b for b in documents.first().children if hasattr(b, "source_range")]
            described = [(b._token.type, b.source_range, getattr(b._token, "info", "") or "") for b in blocks]
            parts: list[point.Part] = []
            for block, part in zip(blocks, point.document_parts(described, self._shown_text)):
                # A drawn diagram is pointed at box by box; anything else as a whole block.
                drawing = block.drawing if isinstance(block, DiagramFence) else None
                inner = point.diagram_parts(block.code, drawing.padding, drawing.text) if drawing else []
                for each in point.fence_parts(part, inner) if inner else [part]:
                    parts.append(each)
                    self._part_widgets.append(block)
            return parts
        return []

    def _start_point(self, select: int = 0) -> bool:
        self.parts = self._compute_parts()
        if not self.parts:
            return False
        self.pointing = True
        self.refresh_bindings()
        self._select(min(select, len(self.parts) - 1))
        return True

    def _select(self, index: int) -> None:
        self.selected = index
        part = self.parts[index]
        body = self.query_one("#body", VerticalScroll)
        views = self.query(DiagramView)
        if views:
            view = views.first()
            view.show(part if part.boxes else None)
            if part.boxes:
                box = part.boxes[0]
                region = view.virtual_region
                # +1: the view's top padding (see app.tcss).
                body.scroll_to_region(
                    Region(region.x + box.x, region.y + 1 + box.y, box.width, box.height), animate=False,
                )
            return
        target = self._part_widgets[index] if index < len(self._part_widgets) else None
        for widget in dict.fromkeys(self._part_widgets):
            widget.set_class(widget is target and not part.boxes, "pointed")
            if isinstance(widget, DiagramFence):
                wanted = part if widget is target and part.boxes else None
                if widget.shown != wanted:
                    widget.show(wanted)
        if target is None:
            return
        if part.boxes:
            box = part.boxes[0]
            scroll_into_view(target.query_one("#code-content"), Region(box.x, box.y, box.width, box.height))
        else:
            body.scroll_to_widget(target, animate=False)

    def _end_point(self, keep_note: bool = True) -> None:
        notes = self.query(NoteLine)
        if notes:
            if keep_note:
                self._keep_note(notes.first().value)
            notes.remove()
        self._noting = False
        self.pointing = False
        for view in self.query(DiagramView):
            view.show(None)
        for widget in dict.fromkeys(self._part_widgets):
            widget.remove_class("pointed")
            if isinstance(widget, DiagramFence):
                widget.show(None)
        self._part_widgets = []
        self.refresh_bindings()

    def _note_key(self) -> tuple | None:
        if self.current is None or not self.parts:
            return None
        return (doc_key(self.current), self.parts[self.selected].id)

    def _keep_note(self, text: str) -> None:
        key = self._note_key()
        if key is None:
            return
        label = self.parts[self.selected].label
        here = self._kept.get(key)
        if text.strip():
            self._store_note(key, KeptNote(self.current, key[1], text, label))
        elif isinstance(here, KeptNote) and here.label in ("", label):
            del self._kept[key]

    def _store_note(self, key: tuple, note: KeptNote) -> None:
        here = self._kept.get(key)
        if isinstance(here, KeptNote) and here.label and here.label != note.label:
            # The part that was here has moved: keep its note for when it is pointed at.
            self._kept[(key[0], f"{key[1]}@{here.label}")] = here
        self._kept[key] = note

    def _kept_note(self) -> KeptNote | None:
        """The note kept for the selected part: under its position, or, if blocks moved,
        under another position with the same label (moved here)."""
        key = self._note_key()
        if key is None:
            return None
        label = self.parts[self.selected].label
        here = self._kept.get(key)
        if isinstance(here, KeptNote) and here.label in ("", label):
            return here
        for other_key, other in list(self._kept.items()):
            if isinstance(other, KeptNote) and other_key[0] == key[0] and other.label and other.label == label:
                del self._kept[other_key]
                self._store_note(key, replace(other, part_id=key[1]))
                return self._kept[key]
        return None

    def action_point_ask(self) -> None:
        if not self.pointing or self._sending or self.query(NoteLine):
            return
        kept = self._kept_note()
        note = NoteLine(kept.text if kept else "")
        self._noting = True
        self.mount(note, before=self.query_one(Footer))
        note.focus()

    def note_cancelled(self) -> None:
        """Esc in the note: keep the words, go back to pointing."""
        notes = self.query(NoteLine)
        if notes:
            self._keep_note(notes.first().value)
            notes.remove()
        self.set_focus(None)
        self._noting = False
        if self._held is not None:
            self._release_held()
        else:
            self._refresh_parts()  # a diagram drawn while the note was open

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not isinstance(event.input, NoteLine):
            return
        text = event.value.strip()
        if not text:
            self.note_cancelled()
            return
        item, part = self.current, self.parts[self.selected]
        key = self._note_key()
        assert item is not None and key is not None
        kept = KeptNote(item, part.id, text, part.label)
        self._store_note(key, kept)
        self.set_focus(None)
        self._end_point(keep_note=False)  # `kept` holds the note until it is delivered
        source_lines = self._shown_text.split("\n")
        line_text = source_lines[part.lines[0] - 1] if part.lines and part.lines[0] <= len(source_lines) else ""
        shown = self._shown_text
        described = point.describe(part)

        def build(target: pane.Source) -> str:
            if item.kind == "file" and item.path is not None:
                return point.point_prompt(
                    described, text, lines=part.lines, line_text=line_text,
                    file_label=edit.display_path(item.path, target.cwd), code=part.code,
                )
            stem, suffix = edit.copy_name(item.title, diagram=item.is_diagram).rsplit(".", 1)
            attachment = self._attach(shown, stem, f".{suffix}") if edit.needs_attachment(shown) else None
            return point.point_prompt(
                described, text, lines=part.lines, line_text=line_text, title=clean(item.title),
                diagram=item.is_diagram, source=shown, attachment=attachment, code=part.code,
            )

        self._send_request(Request(key, item, described, build, kept))
        self._release_held()

    def action_jump_code(self) -> None:
        """Open the code behind the selected part in the editor pane (or this pane)."""
        if not self.check_action("jump_code", ()):
            return
        where = self.parts[self.selected].code.strip()
        found = re.match(r"^(.*?)(?::(\d+))?(?:-\d+)?$", where)
        name, line = found.group(1), int(found.group(2)) if found.group(2) else None
        folder = self.source.cwd if self.source and self.source.cwd else Path.cwd()
        path = folder / name
        if not path.is_file():
            self._show_notice(f"Cannot find {clean(name)}")
            return
        try:
            command = self._find_editor()
        except edit.EditorError as exc:
            self._show_notice(clean(str(exc)))
            return
        if not command:
            self._show_notice("No editor found: set $EDITOR")
            return
        command = [*command, *edit.line_arg(command, line)]

        def open_code() -> None:
            try:
                pane_id = self._code_pane(command, path)
            except Exception:
                pane_id = None
            self.call_from_thread(self._code_opened, pane_id, command, path, where)

        self.run_worker(open_code, thread=True, group="code-pane", exit_on_error=False)

    def _code_opened(self, pane_id: str | None, command: list[str], path: Path, where: str) -> None:
        if not pane_id:
            try:
                self._editor([*command, str(path)])  # no editor pane: borrow this one
            except (OSError, SuspendNotSupported) as exc:
                self._show_notice(f"Could not run the editor: {clean(str(exc))}")
                return
        self._show_notice(f"Opened {clean(where)}", ok=True)

    def action_point_done(self) -> None:
        if self.query(NoteLine):
            self.note_cancelled()
        else:
            self._end_point()

    def on_diagram_view_drawn(self, event: DiagramView.Drawn) -> None:
        if self.pointing and self.query(DiagramView):
            self._refresh_parts()
        if self.pending and self.pending.part_id and not self.pointing:
            self._mark(self.pending.part_id)  # a resize can move the boxes

    def on_diagram_fence_drawn(self, event: DiagramFence.Drawn) -> None:
        if self.pointing and self.query(DocumentView):
            self._refresh_parts()

    def _refresh_parts(self) -> None:
        """A (re)drawn diagram can change the layout: find the parts again, keeping the selection.
        A block that got drawn meanwhile keeps it on its first box, and the other way round."""
        if self._noting:
            return  # the note is about the part selected now; recomputed when it closes
        chosen = self.parts[self.selected].id if self.parts else ""
        self.parts = self._compute_parts()
        if not self.parts:
            return
        ids = [p.id for p in self.parts]
        block = chosen.split("/")[0]
        index = ids.index(chosen) if chosen in ids else next((i for i, id_ in enumerate(ids) if id_.split("/")[0] == block), 0)
        self._select(index)

    def on_diagram_view_clicked(self, event: DiagramView.Clicked) -> None:
        if self._noting:
            return  # the note is about the part selected now
        if not self.pointing and not (self._pointable() and self._start_point()):
            return
        for i, part in enumerate(self.parts):
            if any(box.contains(event.x, event.y) for box in part.boxes):
                self._select(i)
                return

    def on_click(self, event: events.Click) -> None:
        # A click on a document block points at it. Look under the mouse: a click
        # inside a fence (a scroll container) arrives here with the screen as its widget.
        if self._noting:
            return  # the note is about the part selected now
        try:
            widget, _region = self.screen.get_widget_at(event.screen_x, event.screen_y)
        except Exception:
            return
        while widget is not None and not (hasattr(widget, "source_range") and isinstance(widget.parent, DocumentView)):
            widget = widget.parent
        if widget is None or not self.pointing and not (self._pointable() and self._start_point()):
            return
        group = [i for i, w in enumerate(self._part_widgets) if w is widget]
        if not group:
            return
        if self.parts[group[0]].boxes:  # a drawn diagram: the box under the click
            offset = event.get_content_offset(widget.query_one("#code-content"))
            hits = [i for i in group if offset is not None and any(b.contains(offset.x, offset.y) for b in self.parts[i].boxes)]
            self._select(hits[0] if hits else group[0])
        else:
            self._select(group[0])

    def _attach(self, text: str, stem: str, suffix: str) -> Path | None:
        """Put a long prompt body in its own file for the agent; None to inline it instead."""
        if self._closing:
            return None
        try:
            if self._attachments is None:
                self._attachments = Path(tempfile.mkdtemp(prefix="blueprint-"))
            handle, name = tempfile.mkstemp(dir=self._attachments, prefix=f"{stem}-", suffix=suffix)
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(text)
            return Path(name)
        except OSError:
            return None

    def on_unmount(self) -> None:
        self._closing = True
        if self._live is not None:
            # The editor in its pane may still save: leave it the copy (it is in the temp folder).
            self._live.stop.set()
        if self._attachments is not None:
            shutil.rmtree(self._attachments, ignore_errors=True)

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
        """Scroll like vim: j/k one line, h/l a few columns. In point mode, move the selection."""
        if self.pointing:
            if self.query(NoteLine) or not self.parts:
                return
            if self._part_widgets:
                self._move_in_document(dx, dy)
                return
            if self.parts[self.selected].boxes:
                self._select(point.nearest(self.parts, self.selected, self._direction(dx, dy)))
            return
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

    @staticmethod
    def _direction(dx: int, dy: int) -> str:
        return "right" if dx > 0 else "left" if dx < 0 else "down" if dy > 0 else "up"

    def _move_in_document(self, dx: int, dy: int) -> None:
        """j/k: the next or previous block. Inside a drawn diagram, hjkl move box to box
        by direction; going down past its bottom (or up past its top) leaves it."""
        current, widgets = self.selected, self._part_widgets

        def boxes_of(index: int) -> list[int]:
            return [i for i, w in enumerate(widgets) if w is widgets[index]]

        if self.parts[current].boxes:
            group = boxes_of(current)
            beside = group[point.nearest([self.parts[i] for i in group], group.index(current), self._direction(dx, dy))]
            if beside != current or not dy:
                self._select(beside)
                return
            target = group[-1] + 1 if dy > 0 else group[0] - 1
        elif dy:
            target = current + dy
        else:
            return
        if not 0 <= target < len(self.parts):
            return
        if self.parts[target].boxes:
            # Into a diagram: its top box going down, its bottom box going up.
            def place(i: int) -> tuple[float, float]:
                x, y = point.anchor(self.parts[i])
                return (y, x) if dy > 0 else (-y, x)

            target = min(boxes_of(target), key=place)
        self._select(target)

    def _part_top(self, index: int) -> tuple[int, int]:
        """Where a part starts on screen, (row, column); rows past the screen too."""
        part = self.parts[index]
        if self._part_widgets:
            widget = self._part_widgets[index]
            if part.boxes:
                region = widget.query_one("#code-content").content_region
                return region.y + part.boxes[0].y, region.x + part.boxes[0].x
            return widget.region.y, widget.region.x
        views = self.query(DiagramView)
        if views and part.boxes:
            region = views.first().content_region
            return region.y + part.boxes[0].y, region.x + part.boxes[0].x
        return 0, 0

    def _point_jump(self, rows: int | None, end: bool = False) -> None:
        """Select the first or last part (rows None), or the part about `rows` below or above."""
        if self.query(NoteLine) or not self.parts:
            return
        tops = [self._part_top(i) for i in range(len(self.parts))]
        if rows is None:
            pick = max if end else min
            self._select(pick(range(len(tops)), key=lambda i: (tops[i][0], -tops[i][1]) if end else tops[i]))
            return
        row = tops[self.selected][0] + rows
        ahead = [i for i, (y, _x) in enumerate(tops) if (y >= row if rows > 0 else y <= row) and i != self.selected]
        if not ahead:
            self._point_jump(None, end=rows > 0)
            return
        nearest_row = min(abs(tops[i][0] - row) for i in ahead)
        column = tops[self.selected][1]
        self._select(min((i for i in ahead if abs(tops[i][0] - row) == nearest_row), key=lambda i: abs(tops[i][1] - column)))

    def action_page(self, direction: int) -> None:
        """ctrl+d and ctrl+u: half a screen down or up, like vim. In point mode, the selection moves."""
        if self.pointing:
            body = self.query_one("#body", VerticalScroll)
            self._point_jump(direction * max(1, body.scrollable_content_region.height // 2))
            return
        plain = self.query(PlainView)
        target = plain.first() if plain else self.query_one("#body", VerticalScroll)
        half = max(1, target.scrollable_content_region.height // 2)
        target.scroll_relative(y=direction * half, animate=False)

    def action_jump(self, end: bool) -> None:
        """g to the top, G to the bottom. In point mode, the first or last part."""
        if self.pointing:
            self._point_jump(None, end)
            return
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
