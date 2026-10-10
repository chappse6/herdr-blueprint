"""The Blueprint viewer: one screen showing the latest thing the worker sent."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.markdown import MarkdownFence
from textual.widgets.option_list import Option

from . import config as config_store
from . import edit, point
from .diagram import MAX_DIAGRAM_CHARS
from .document import DiagramView, DocumentView, PlainView, as_code
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


@dataclass
class KeptNote:
    """A note about a part that the agent has not received yet."""

    item: Item
    part_id: str
    text: str


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
_EDITOR_SIGNALS = [getattr(signal, name) for name in ("SIGINT", "SIGQUIT", "SIGTSTP") if hasattr(signal, name)]


@contextmanager
def editor_signals_ignored() -> Iterator[None]:
    """Ignore terminal signals in Blueprint (only; the editor keeps the defaults)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = [(sig, signal.signal(sig, signal.SIG_IGN)) for sig in _EDITOR_SIGNALS]
    try:
        yield
    finally:
        for sig, handler in previous:
            signal.signal(sig, handler if handler is not None else signal.SIG_DFL)


class AgentPicker(ModalScreen[str | None]):
    """No agent next to Blueprint, but a shell: start one to receive the edit?"""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", show=False),
        Binding("j", "cursor(1)", show=False),
        Binding("k", "cursor(-1)", show=False),
    ]

    def __init__(self, kinds: list[str]) -> None:
        super().__init__()
        self.kinds = kinds

    def compose(self) -> ComposeResult:
        with Vertical(id="agents"):
            yield Static("Start an agent next to Blueprint and send your edit?", id="agents-title")
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
        # Point mode only.
        Binding("enter", "point_ask", "Ask", key_display="⏎"),
        Binding("escape", "point_done", "Done", key_display="esc"),
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
        editor: Callable[[list[str]], int] | None = None,
        find_editor: Callable[[], list[str] | None] | None = None,
        prompter: Callable[[pane.Source, str], tuple[bool, str]] | None = None,
        starter: Callable[[pane.Source, str], tuple[bool, str]] | None = None,
        idle_shell: Callable[[str], bool] | None = None,
        kinds: Callable[[], list[str]] | None = None,
        pane_editor: Callable[[list[str], Path], str | None] | None = None,
        focus_pane: Callable[[str], None] | None = None,
        pane_alive: Callable[[str], bool] | None = None,
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
        self._live: LiveEdit | None = None
        self._held: Item | None = None  # the newest send that arrived during a live edit
        self.preview_text: str | None = None  # what the live preview shows
        # Unsent edits, one per document; the next `e` on that document reopens it.
        self._kept: dict[tuple, KeptEdit] = {}
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

    @property
    def editing(self) -> bool:
        return self._live is not None

    async def open_item(self, item: Item) -> None:
        """Replace the screen with `item`. The previous one is dropped."""
        if self._live is not None:
            self._held = item  # shown, or dropped, when the editor closes
            return
        async with self._render_lock:
            await self._open_locked(item)

    async def refresh_current(self, draw: bool) -> None:
        """Re-read and redraw what is on screen, with the worker's draw flag."""
        if self._live is not None:
            return  # the editor is open on what is shown
        # Read `current` under the lock, so a send in progress is not undone.
        async with self._render_lock:
            if self.current is not None:
                await self._open_locked(replace(self.current, draw=draw, at=time.time()))

    async def _open_locked(self, item: Item) -> None:
        if self.pointing:
            self._end_point()  # a send replaces the screen; a typed note is kept
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
        self._shown_text, self._shown_drawn = text, item.is_diagram and draw
        self.current = item
        self.title_text = clean(item.title)
        previous_key = self._shown_key
        self._shown_key = doc_key(item)
        if previous_key and previous_key[0] == "text" and previous_key != self._shown_key and not self._sending:
            # Sent text can't be shown again, so work kept for it can't be picked up.
            dropped = [key for key, kept in self._kept.items() if doc_key(kept.item) == previous_key]
            for key in dropped:
                title = self._kept.pop(key).item.title
            if dropped:
                self.notice = f"Your unsent work on '{clean(title)}' was dropped"
                self.notice_ok = False
        self.refresh_bindings()
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
        if action in ("point_ask", "point_done"):
            return self.pointing
        if self.pointing and action in ("ask", "open_file", "pick_theme", "reload", "quit", "jump", "page"):
            return False  # point mode offers only its own keys
        # Hidden from the footer and the keys do nothing when there is no pane to use.
        if action == "ask":
            return bool(self.source and self.source.agent)
        if action == "open_file":
            return bool(self.source and self.source.cwd)
        if action == "edit":
            return self._editable()
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

    # Editing ------------------------------------------------------------------

    def _kept_for(self, item: Item | None) -> KeptEdit | None:
        kept = self._kept.get(doc_key(item)) if item is not None else None
        return kept if isinstance(kept, KeptEdit) else None

    def _editable(self) -> bool:
        if self._live is not None:
            return True  # e focuses the editor pane
        item = self.current
        if item is None or self._sending or self.query(PlainView):
            return False
        if item.kind != "file" or self._kept_for(item):
            return True
        try:
            return item.path is not None and item.path.stat().st_size <= MAX_BYTES
        except OSError:
            return False

    def action_edit(self) -> None:
        """First e points at a part; e while pointing edits the text there."""
        if self._live is not None:
            self._focus_pane(self._live.pane_id)
            return
        if not self._editable():
            return
        if not self.pointing:
            self._start_point()
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
        if self._start_live(item, kept, base, base_bytes, start, command):
            return
        outcome = self._edit_copy(item, start, command)
        if outcome is None:
            return
        self._after_edit(item, kept, base, base_bytes, *outcome)

    def _after_edit(
        self, item: Item, kept: KeptEdit | None, base: str, base_bytes: bytes | None, saved: bool, edited: str,
    ) -> bool:
        """Act on the editor's result; True when the change went off to the agent."""
        key = doc_key(item)
        if not saved:
            # :q! keeps whatever was waiting; nothing is sent.
            self._show_notice("Not saved. Your edit is kept; press e to edit it again" if kept else "No changes")
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
    ) -> bool:
        """Open the editor in a pane below, with a live preview. False to borrow this pane instead."""
        name = edit.copy_name(item.path.name if item.path else item.title, diagram=item.is_diagram)
        try:
            folder = Path(tempfile.mkdtemp(prefix="blueprint-edit-"))
            copy = folder / name
            copy.write_text(text, encoding="utf-8", newline="")
            stat = copy.stat()
        except OSError:
            return False
        try:
            pane_id = self._pane_editor(command, copy)
        except Exception:
            pane_id = None
        if not pane_id:
            shutil.rmtree(folder, ignore_errors=True)
            return False
        live = LiveEdit(item, kept, base, base_bytes, folder, copy, (stat.st_mtime_ns, stat.st_size), pane_id, asyncio.Event())
        self._live = live
        self._show_notice(f"Editing {name}: save to preview, quit to finish", ok=True)
        self.run_worker(self._watch_live(live), group="live", exit_on_error=False)
        self._live_timer = self.set_interval(2, self._check_live_pane)
        self.refresh_bindings()
        return True

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
            await self._finish_live(live, closed=True)

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
            if not self._pane_alive(live.pane_id) and not edit.exit_file(live.copy).exists():
                self.call_from_thread(self._close_live_by_hand, live)

        self.run_worker(check, thread=True, group="live-check", exclusive=True, exit_on_error=False)

    async def _close_live_by_hand(self, live: LiveEdit) -> None:
        await self._finish_live(live, closed=True)

    async def _finish_live(self, live: LiveEdit, closed: bool = False) -> None:
        if self._live is not live:
            return
        self._live = None
        live.stop.set()
        if getattr(self, "_live_timer", None) is not None:
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
                self._show_notice("The editor closed. Your saved changes are kept; press e to send them")
            else:
                self._show_notice("The editor closed")
        elif code == 127:
            self._show_notice("Could not run the editor")
        elif code != 0:
            self._show_notice("Edit cancelled")
        else:
            sent = self._after_edit(live.item, live.kept, live.base, live.base_bytes, saved, edited)
        if sent and self.preview_text != edited:
            # Saved and quit faster than a redraw: show what was sent.
            self.preview_text = edited
            async with self._render_lock:
                await self._render_text(clean(edited), diagram=live.item.is_diagram, draw=live.item.draw or self._shown_drawn, plain=False)
        if not sent:
            # Back to the original, or the newest send that waited; keep the message.
            notice, ok = self.notice, self.notice_ok
            self.preview_text = None
            async with self._render_lock:
                await self._open_locked(held or live.item)
            self._show_notice(notice, ok) if notice else None
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
                with editor_signals_ignored():
                    code = process.wait()
        if error is not None:
            raise error
        return code

    # Sending to the agent -------------------------------------------------------

    def _send_request(self, request: Request) -> None:
        self._sending = True
        self.refresh_bindings()
        self._show_notice("Sending your edit…" if request.is_edit else "Asking…", ok=True)
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

        self.push_screen(AgentPicker(kinds), chosen)

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
        elif not request.is_edit:
            self._show_notice(f"{clean(message)}. Your note was kept; point at it and press Enter to send it again")
        elif here:
            self._show_notice(f"{clean(message)}. Your edit was not sent; press e to edit it again")
        elif request.key[0] == "file":
            where = clean(request.item.title)
            self._show_notice(f"{clean(message)}. Your edit was not sent; open {where} again and press e")
        else:
            self._kept.pop(request.key, None)
            self._show_notice(f"{clean(message)}. Your edit of '{clean(request.item.title)}' was not sent and was dropped")
        self.refresh_bindings()

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
            return edit.file_prompt(label, diff, diverged=diverged, attachment=attachment)
        source = kept.edited
        stem, suffix = edit.copy_name(item.title, diagram=item.is_diagram).rsplit(".", 1)
        attachment = self._attach(source, stem, f".{suffix}") if edit.needs_attachment(source) else None
        return edit.text_prompt(clean(item.title), source, diagram=item.is_diagram, attachment=attachment)

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
            self._part_widgets = blocks
            described = [(b._token.type, b.source_range, getattr(b._token, "info", "") or "") for b in blocks]
            return point.document_parts(described, self._shown_text)
        return []

    def _start_point(self, select: int = 0) -> None:
        self.parts = self._compute_parts()
        if not self.parts:
            return
        self.pointing = True
        self.refresh_bindings()
        self._select(min(select, len(self.parts) - 1))

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
        for i, widget in enumerate(self._part_widgets):
            widget.set_class(i == index, "pointed")
        if index < len(self._part_widgets):
            body.scroll_to_widget(self._part_widgets[index], animate=False)

    def _end_point(self) -> None:
        notes = self.query(NoteLine)
        if notes:
            self._keep_note(notes.first().value)
            notes.remove()
        self.pointing = False
        for view in self.query(DiagramView):
            view.show(None)
        for widget in self._part_widgets:
            widget.remove_class("pointed")
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
        if text.strip():
            self._kept[key] = KeptNote(self.current, key[1], text)
        elif isinstance(self._kept.get(key), KeptNote):
            del self._kept[key]

    def action_point_ask(self) -> None:
        if not self.pointing or self._sending or self.query(NoteLine):
            return
        kept = self._kept.get(self._note_key())
        note = NoteLine(kept.text if isinstance(kept, KeptNote) else "")
        self.mount(note, before=self.query_one(Footer))
        note.focus()

    def note_cancelled(self) -> None:
        """Esc in the note: keep the words, go back to pointing."""
        notes = self.query(NoteLine)
        if notes:
            self._keep_note(notes.first().value)
            notes.remove()
        self.set_focus(None)

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
        kept = KeptNote(item, part.id, text)
        self._kept[key] = kept
        event.input.remove()
        self.set_focus(None)
        self.pointing = False  # the note is out of the line now; nothing to keep on leaving
        self._end_point()
        source_lines = self._shown_text.split("\n")
        line_text = source_lines[part.lines[0] - 1] if part.lines and part.lines[0] <= len(source_lines) else ""
        shown = self._shown_text
        described = point.describe(part)

        def build(target: pane.Source) -> str:
            if item.kind == "file" and item.path is not None:
                return point.point_prompt(
                    described, text, lines=part.lines, line_text=line_text,
                    file_label=edit.display_path(item.path, target.cwd),
                )
            stem, suffix = edit.copy_name(item.title, diagram=item.is_diagram).rsplit(".", 1)
            attachment = self._attach(shown, stem, f".{suffix}") if edit.needs_attachment(shown) else None
            return point.point_prompt(
                described, text, lines=part.lines, line_text=line_text, title=clean(item.title),
                diagram=item.is_diagram, source=shown, attachment=attachment,
            )

        self._send_request(Request(key, item, described, build, kept))

    def action_point_done(self) -> None:
        if self.query(NoteLine):
            self.note_cancelled()
        else:
            self._end_point()

    def on_diagram_view_drawn(self, event: DiagramView.Drawn) -> None:
        # A redraw (theme, resize) can change the layout: find the parts again.
        if self.pointing and self.query(DiagramView):
            chosen = self.parts[self.selected].id if self.parts else ""
            self.parts = self._compute_parts()
            ids = [p.id for p in self.parts]
            self._select(ids.index(chosen) if chosen in ids else 0)

    def on_diagram_view_clicked(self, event: DiagramView.Clicked) -> None:
        if self._sending or not self._editable():
            return
        if not self.pointing:
            self._start_point()
        for i, part in enumerate(self.parts):
            if any(box.contains(event.x, event.y) for box in part.boxes):
                self._select(i)
                return

    def on_click(self, event: events.Click) -> None:
        # A click on a document block points at it.
        widget = event.widget
        while widget is not None and not (hasattr(widget, "source_range") and isinstance(widget.parent, DocumentView)):
            widget = widget.parent
        if widget is None or self._sending or not self._editable():
            return
        if not self.pointing:
            self._start_point()
        if widget in self._part_widgets:
            self._select(self._part_widgets.index(widget))

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
            self._live.stop.set()
            shutil.rmtree(self._live.folder, ignore_errors=True)
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
            if self._part_widgets or not self.parts[self.selected].boxes:
                if dy:  # documents: next or previous block
                    self._select(max(0, min(len(self.parts) - 1, self.selected + dy)))
                return
            direction = "right" if dx > 0 else "left" if dx < 0 else "down" if dy > 0 else "up"
            self._select(point.nearest(self.parts, self.selected, direction))
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
