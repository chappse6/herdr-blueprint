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
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.markdown import MarkdownFence
from textual.widgets.option_list import Option

from . import config as config_store
from . import edit
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
| `e` | Edit and send to the agent |
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
        # Only offered when there is something to edit.
        Binding("e", "edit", "Edit"),
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
        # Unsent edits, one per document; the next `e` on that document reopens it.
        self._kept: dict[tuple, KeptEdit] = {}
        self._sending = False
        self._closing = False
        # Long prompt bodies for the agent; removed when Blueprint closes.
        self._attachments: Path | None = None
        self._shown_key: tuple | None = None
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
        previous_key = self._shown_key
        self._shown_key = doc_key(item)
        dropped = self._kept.get(previous_key) if previous_key != self._shown_key else None
        if dropped and previous_key[0] == "text" and not self._sending:
            # Sent text can't be shown again, so its edit can't be reopened.
            del self._kept[previous_key]
            self.notice = f"Your unsent edit of '{clean(dropped.item.title)}' was dropped"
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
        return self._kept.get(doc_key(item)) if item is not None else None

    def _editable(self) -> bool:
        item = self.current
        if item is None or self._sending:
            return False
        if item.kind != "file" or self._kept_for(item):
            return True
        try:
            return item.path is not None and item.path.stat().st_size <= MAX_BYTES
        except OSError:
            return False

    def action_edit(self) -> None:
        """Open what is shown in the editor. Save and quit sends it; quit without saving doesn't."""
        item = self.current
        if item is None or not self._editable():
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
        outcome = self._edit_copy(item, start, command)
        if outcome is None:
            return
        saved, edited = outcome
        key = doc_key(item)
        if not saved:
            # :q! keeps whatever was waiting; nothing is sent.
            self._show_notice("Not saved. Your edit is kept; press e to edit it again" if kept else "No changes")
            return
        if kept and not edited.strip():
            del self._kept[key]
            self._show_notice("Edit thrown away")
            return
        if edit.same_text(edited, base):
            self._kept.pop(key, None)
            self._show_notice("No changes")
            return
        self._kept[key] = KeptEdit(item, base, base_bytes, edited)
        self._send_kept(self._kept[key])

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

    def _send_kept(self, kept: KeptEdit) -> None:
        self._sending = True
        self.refresh_bindings()
        self._show_notice("Sending your edit…", ok=True)
        self.run_worker(lambda: self._deliver(kept), thread=True, group="edit", exit_on_error=False)

    def _deliver(self, kept: KeptEdit) -> None:
        """Find the agent and send (worker thread). Never raises."""
        try:
            if self.source is None:
                result = (False, "No agent next to Blueprint")
            else:
                fresh = self._read_neighbor()
                if self._closing:
                    return
                if fresh.agent:
                    result = self._prompter(fresh, self._prompt_for(kept, fresh))
                elif self._idle_shell(fresh.pane_id) and (kinds := self._kinds()):
                    self.call_from_thread(self._offer_agent, kept, fresh, kinds)
                    return
                else:
                    result = (False, "No agent next to Blueprint")
        except Exception as exc:
            result = (False, f"Could not send: {exc}")
        self._after_send(kept, *result)

    def _offer_agent(self, kept: KeptEdit, fresh: pane.Source, kinds: list[str]) -> None:
        def chosen(kind: str | None) -> None:
            if not kind:
                self._sent(kept, False, "No agent started")
                return
            self._show_notice(f"Starting {kind}…", ok=True)
            self.run_worker(
                lambda: self._start_and_send(kept, fresh, kind), thread=True, group="edit", exit_on_error=False,
            )

        self.push_screen(AgentPicker(kinds), chosen)

    def _start_and_send(self, kept: KeptEdit, fresh: pane.Source, kind: str) -> None:
        try:
            started, message = self._starter(fresh, kind)
            if started and not self._closing:
                ready = self._read_neighbor()
                target = ready if ready.agent else replace(fresh, agent=kind)
                if self._closing:
                    return
                started, message = self._prompter(target, self._prompt_for(kept, target))
        except Exception as exc:
            started, message = False, f"Could not start {kind}: {exc}"
        self._after_send(kept, started, message)

    def _after_send(self, kept: KeptEdit, ok: bool, message: str) -> None:
        if self._closing:
            return
        try:
            self.call_from_thread(self._sent, kept, ok, message)
        except RuntimeError:
            pass  # Blueprint closed meanwhile

    def _sent(self, kept: KeptEdit, ok: bool, message: str) -> None:
        self._sending = False
        key = doc_key(kept.item)
        here = same_item(kept.item, self.current)
        if ok:
            if self._kept.get(key) is kept:
                del self._kept[key]
            agent = message.removeprefix("Sent to ") if message.startswith("Sent to ") else "the agent"
            self._show_notice(f"Sent your edit to {agent}", ok=True)
        elif here:
            self._show_notice(f"{clean(message)}. Your edit was not sent; press e to edit it again")
        elif key[0] == "file":
            where = clean(kept.item.title)
            self._show_notice(f"{clean(message)}. Your edit was not sent; open {where} again and press e")
        else:
            self._kept.pop(key, None)
            self._show_notice(f"{clean(message)}. Your edit of '{clean(kept.item.title)}' was not sent and was dropped")
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
            text = edit.file_prompt(label, diff, diverged=diverged, attachment=attachment)
        else:
            source = kept.edited
            stem, suffix = edit.copy_name(item.title, diagram=item.is_diagram).rsplit(".", 1)
            attachment = self._attach(source, stem, f".{suffix}") if edit.needs_attachment(source) else None
            text = edit.text_prompt(clean(item.title), source, diagram=item.is_diagram, attachment=attachment)
        return edit.prompt_safe(text)

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
