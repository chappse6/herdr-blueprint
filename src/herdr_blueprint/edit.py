"""Editing what Blueprint shows: the editor command, the copy's name, the diff,
and the prompt that hands the change to the agent. No Textual in here.

Blueprint never writes the shown file. The person edits a copy; saving and
quitting sends the change to the agent, which is the only one writing.
"""

from __future__ import annotations

import difflib
import os
import re
import shlex
import shutil
import signal
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

# Longer prompt bodies go into a file the agent reads; the prompt names it.
INLINE_LIMIT = 6_000

# herdr's agent kinds, the common ones first. The kind is also the program name.
AGENT_KINDS = (
    "claude", "codex", "gemini", "grok", "cursor", "opencode", "copilot", "amp",
    "qwen", "kimi", "kiro", "droid", "cline", "pi", "devin", "agy", "omp",
    "mastracode", "hermes", "kilo", "qodercli", "letta", "maki", "muse",
)

# GUI editors return at once unless told to wait for the window to close.
_WAITING_FLAGS = {"code": "--wait", "codium": "--wait", "subl": "--wait"}

_DOC_SUFFIXES = (".md", ".markdown", ".mmd", ".mermaid")

# Names Windows reserves for devices, whatever the extension.
_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}

# C0 controls except tab and newline, and DEL: never part of a prompt.
_PROMPT_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class EditorError(Exception):
    """The editor setting can't be used; the message says why."""


def _program(path: str) -> str:
    name = Path(path.replace("\\", "/")).name.lower()
    return name.rsplit(".", 1)[0] if name.endswith((".exe", ".cmd", ".bat")) else name


def editor_command(
    environ: Mapping[str, str] | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    system: str | None = None,
) -> list[str] | None:
    """$BLUEPRINT_EDITOR, else $VISUAL, else $EDITOR, else vim, vi, nano; notepad last on Windows.

    None when nothing is set and nothing is installed. EditorError when the
    setting can't be parsed or names a program that isn't there: starting it
    would fail with the terminal handed over to it.
    """
    environ = os.environ if environ is None else environ
    system = os.name if system is None else system
    command: list[str] | None = None
    # BLUEPRINT_EDITOR first: $EDITOR is often set up for something else (say, starting in insert mode).
    for key in ("BLUEPRINT_EDITOR", "VISUAL", "EDITOR"):
        raw = environ.get(key, "").strip()
        if raw:
            try:
                # Windows keeps backslashes; quotes around a path are not part of it.
                parts = shlex.split(raw, posix=system != "nt")
            except ValueError as exc:
                raise EditorError(f"Cannot read ${key}: {exc}") from None
            command = [part.strip('"').strip("'") for part in parts if part.strip('"').strip("'")]
            break
    if not command:
        fallbacks = ("vim", "vi", "nano", "notepad") if system == "nt" else ("vim", "vi", "nano")
        found = next((path for name in fallbacks if (path := which(name))), None)
        if found is None:
            return None
        return [found]
    # Resolve the program: `EDITOR=code` finds code.cmd on Windows, ~ is home.
    program = os.path.expandvars(os.path.expanduser(command[0]))
    # A file given by path: written the system's way (~ on Windows mixes / and \).
    resolved = which(program) or (str(Path(program)) if Path(program).is_file() else None)
    if resolved is None:
        raise EditorError(f"Editor not found: {command[0]}")
    command[0] = resolved
    flag = _WAITING_FLAGS.get(_program(command[0]))
    if flag and flag not in command and "-w" not in command:
        command.append(flag)
    return command


# Editors that open at a line given as +N.
_LINE_EDITORS = {"vi", "nano", "hx", "helix", "kak", "micro", "emacs", "emacsclient", "ne", "joe"}


def line_arg(command: list[str], line: int | None) -> list[str]:
    """["+N"] when the editor opens at a line that way, else []."""
    if not line:
        return []
    program = _program(command[0])
    return [f"+{line}"] if "vim" in program or program in _LINE_EDITORS else []


def copy_name(name: str, diagram: bool) -> str:
    """A safe file name for the copy: the original's stem, cleaned, with the right suffix."""
    stem = name
    for suffix in _DOC_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(stem.replace("\\", "/")).name)
    stem = stem.strip(".-")[:60] or "draft"
    if stem.split(".")[0].upper() in _DEVICE_NAMES:
        stem = f"draft-{stem}"
    return f"{stem}{'.mmd' if diagram else '.md'}"


def _lines(text: str) -> list[str]:
    """Lines as the file has them: only newlines split, a final newline doesn't count."""
    return text.replace("\r\n", "\n").removesuffix("\n").split("\n")


def same_text(a: str, b: str) -> bool:
    """Equal apart from line endings and a final newline, which editors change on save."""
    return _lines(a) == _lines(b)


def unified_diff(original: str, edited: str, name: str) -> str:
    """A unified diff, or "" when the lines are the same."""
    lines = difflib.unified_diff(
        _lines(original), _lines(edited), fromfile=name, tofile=name, lineterm="",
    )
    body = "\n".join(lines)
    return f"{body}\n" if body else ""


def display_path(path: Path, cwd: Path | None) -> str:
    """The path as the agent should read it: relative to its folder when inside it."""
    if cwd is not None:
        try:
            return path.resolve().relative_to(cwd.resolve()).as_posix()
        except ValueError:
            pass
    return str(path)


def fenced(text: str, lang: str) -> str:
    """`text` in a code fence longer than any backtick run inside it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{lang}\n{text.rstrip(chr(10))}\n{fence}"


def prompt_safe(text: str) -> str:
    """The prompt without control characters (argv can't even hold NUL)."""
    return _PROMPT_CONTROL.sub("", text)


def needs_attachment(body: str) -> bool:
    return len(body) > INLINE_LIMIT


def tied_to_code(text: str) -> bool:
    """Whether a diagram ties its boxes to code with `%% code:` lines."""
    from .point import code_links

    return bool(code_links(text))


_MERMAID_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*mermaid\b")


def _code_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Line ranges [start, end) of the Mermaid blocks that have `%% code:` lines."""
    blocks, i = [], 0
    while i < len(lines):
        opened = _MERMAID_FENCE.match(lines[i])
        if not opened:
            i += 1
            continue
        mark = opened.group(1)
        end = next(
            (j for j in range(i + 1, len(lines)) if lines[j].strip().startswith(mark[0] * len(mark)) and not lines[j].strip().strip(mark[0])),
            len(lines) - 1,
        )
        if tied_to_code("\n".join(lines[i + 1:end])):
            blocks.append((i, end + 1))
        i = end + 1
    return blocks


def touches_code(base: str, edited: str, *, diagram: bool) -> bool:
    """Whether an edit changes a diagram tied to code: any change to a diagram that has
    `%% code:` lines, or in a document, a change inside a Mermaid block that has them."""
    if diagram:
        return tied_to_code(base) or tied_to_code(edited)
    before, after = _lines(base), _lines(edited)
    old_blocks, new_blocks = _code_blocks(before), _code_blocks(after)
    if not (old_blocks or new_blocks):
        return False
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        # A deletion still happened somewhere: count the line it happened at.
        if any(s <= n < e for n in range(i1, max(i2, i1 + 1)) for s, e in old_blocks):
            return True
        if any(s <= n < e for n in range(j1, max(j2, j1 + 1)) for s, e in new_blocks):
            return True
    return False


# An edit of a diagram tied to code is a work order on that code.
CODE_WORK = "make the change in the code its `%% code:` lines point to, run the tests, then "


def file_prompt(
    label: str, diff: str, *, diverged: bool, attachment: Path | None = None, tied_to_code: bool = False,
) -> str:
    """What the agent gets after an edit of a file."""
    if tied_to_code:
        first = f"I edited `{label}` in Blueprint. Apply this change to the file, {CODE_WORK}show the result in Blueprint."
    else:
        first = (
            f"I edited `{label}` in Blueprint. Apply this change to the file and to anything "
            "that depends on it, then show the result in Blueprint."
        )
    lines = [first]
    if diverged:
        lines.append(
            "The file changed on disk while I was editing, so read the file first and "
            "apply the change by hand rather than as a patch."
        )
    lines.append(f"The diff is in `{attachment}`." if attachment else fenced(diff, "diff"))
    return "\n".join(lines) + "\n"


def text_prompt(
    title: str, source: str, *, diagram: bool, attachment: Path | None = None, tied_to_code: bool = False,
) -> str:
    """What the agent gets after an edit of text it sent: the new version, not a diff."""
    what = "diagram" if diagram else "note"
    todo = f"{CODE_WORK}send the updated version to Blueprint." if tied_to_code else (
        "update what it describes, then send the new version to Blueprint."
    )
    lines = [
        f"I edited the {what} you sent ('{title}') in Blueprint. This is what I want instead; {todo}",
        f"The new version is in `{attachment}`." if attachment else fenced(source, "mermaid" if diagram else "markdown"),
    ]
    return "\n".join(lines) + "\n"


def kinds_on_path(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Agent kinds whose program is installed, the common ones first."""
    return [kind for kind in AGENT_KINDS if which(kind)]


def agent_name(kind: str, pane_id: str) -> str:
    """A herdr agent name ([a-z][a-z0-9_-]{0,31}), one per kind and pane."""
    pane = re.sub(r"[^a-z0-9]+", "-", pane_id.lower()).strip("-")
    return f"bp-{kind}-{pane}"[:32].rstrip("-")



# The editor pane: Blueprint opens it with the copy and the editor command in
# these variables; the pane runs the editor and leaves the exit code next to the
# copy, then closes.
SESSION_COPY = "BLUEPRINT_EDIT_COPY"
SESSION_COMMAND = "BLUEPRINT_EDIT_COMMAND"
EXIT_SUFFIX = ".exit"


def exit_file(copy: Path) -> Path:
    return copy.with_name(copy.name + EXIT_SUFFIX)


_TERMINAL_SIGNALS = [getattr(signal, name) for name in ("SIGINT", "SIGQUIT", "SIGTSTP") if hasattr(signal, name)]


@contextmanager
def signals_ignored() -> Iterator[None]:
    """Ignore terminal signals while waiting for an editor already started: ctrl+c is
    the editor's. Start the editor first, or it inherits the ignoring."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = [(sig, signal.signal(sig, signal.SIG_IGN)) for sig in _TERMINAL_SIGNALS]
    try:
        yield
    finally:
        for sig, handler in previous:
            signal.signal(sig, handler if handler is not None else signal.SIG_DFL)


def run_session(environ: Mapping[str, str], run: Callable | None = None) -> int:
    """Run the editor on the copy and write its exit code next to it, whatever happens:
    Blueprint waits for that file."""
    import json
    import subprocess
    import sys

    try:
        copy = Path(environ[SESSION_COPY])
        command = [*json.loads(environ[SESSION_COMMAND]), str(copy)]
    except (KeyError, ValueError, TypeError):
        print("herdr-blueprint: edit-session runs in the editor pane Blueprint opens", file=sys.stderr)
        return 2
    code = 1
    try:
        if run is not None:
            code = run(command).returncode
        else:
            process = subprocess.Popen(command)
            with signals_ignored():
                code = process.wait()
    except OSError:
        code = 127
    finally:
        done = exit_file(copy)
        try:
            tmp = done.with_name(done.name + ".tmp")
            tmp.write_text(str(code), encoding="utf-8")
            os.replace(tmp, done)
        except OSError:
            pass  # Blueprint closed and its folder went with it
    return 0
