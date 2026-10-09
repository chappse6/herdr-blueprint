"""What the viewer offers before anything was sent: the start screen and the file picker."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from .sources.pane import Source, short_folder


class ActionRow(Horizontal):
    """One "key  what it does" line; clicking it does the same as the key."""

    def __init__(self, key: str, label: str, action: str) -> None:
        super().__init__(classes="start-action")
        self._key = key
        self._label = label
        self._action = action

    def compose(self) -> ComposeResult:
        yield Static(self._key, classes="start-key")
        yield Static(Text(self._label), classes="start-does")

    async def on_click(self) -> None:
        await self.app.run_action(self._action)


class StartView(Vertical):
    """Shown until the first send: the pane Blueprint sits next to, and what to do."""

    def __init__(self, source: Source) -> None:
        super().__init__()
        self.source = source

    def compose(self) -> ComposeResult:
        source = self.source
        yield Static("Nothing here yet.", classes="start-title")
        rows = [
            ("Next to", source.agent or "a shell"),
            ("Task", source.task),
            ("Folder", short_folder(source.cwd) if source.cwd else None),
        ]
        for label, value in rows:
            if value:
                with Horizontal(classes="start-row"):
                    yield Static(label, classes="start-name")
                    yield Static(Text(value), classes="start-value")
        with Vertical(classes="start-actions"):
            if source.agent:
                yield ActionRow("a", f"Ask {source.agent} to draw this task", "app.ask")
            if source.cwd:
                yield ActionRow("o", "Open a file from this folder", "app.open_file")
        yield Static("Agents can also send here with the blueprint skill.", classes="start-hint")


class FilePicker(ModalScreen[Path | None]):
    """Pick a Markdown or Mermaid file from a folder, newest first."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", show=False),
        Binding("j", "cursor(1)", show=False),
        Binding("k", "cursor(-1)", show=False),
    ]

    def __init__(self, folder: Path, finder: Callable[[Path], list[Path]]) -> None:
        super().__init__()
        self.folder = folder
        self.finder = finder
        self.paths: list[Path] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="files"):
            with Horizontal(id="files-top"):
                yield Static("Open a file", id="files-title")
                yield Static(Text(short_folder(self.folder)), id="files-folder")
            yield Static("Looking for files…", id="files-status")
            yield OptionList(id="files-list")
            yield Static("j/k move   ⏎ open   esc back", id="files-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).display = False
        # Listing a big repo takes a moment; keep the screen responsive.
        self.run_worker(self._find, thread=True, group="files")

    def _find(self) -> None:
        try:
            paths: list[Path] | None = self.finder(self.folder)
        except Exception:  # an unreadable folder must not close the viewer
            paths = None
        self.app.call_from_thread(self._show, paths)

    def _show(self, paths: list[Path] | None) -> None:
        self.paths = paths or []
        status = self.query_one("#files-status", Static)
        options = self.query_one(OptionList)
        if paths is None:
            status.update("Cannot list this folder.")
            return
        if not paths:
            status.update("No Markdown or Mermaid files in this folder.")
            return
        status.display = False
        options.add_options(Option(Text(self.relative(path))) for path in paths)
        options.display = True
        options.highlighted = 0
        options.focus()

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.folder).as_posix()
        except ValueError:
            return str(path)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.paths[event.option_index])

    def action_cursor(self, step: int) -> None:
        options = self.query_one(OptionList)
        if step > 0:
            options.action_cursor_down()
        else:
            options.action_cursor_up()

    def action_cancel(self) -> None:
        self.dismiss(None)
