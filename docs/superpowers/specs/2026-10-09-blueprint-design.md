# Blueprint: design

Date: 2026-10-09
Status: draft, waiting for review

## Summary

Blueprint is a herdr plugin that turns a side pane into a live viewer for
Markdown and Mermaid. It follows the files an agent writes, and agents can
send diagrams to it on purpose. Everything renders as styled terminal text:
no browser, no images.

## Goals

- Render Markdown documents and Mermaid diagrams beautifully in a terminal.
- Follow mode: show the `.md` or `.mmd` file the agent just saved.
- Agent push: an agent sends a file or a Mermaid snippet to the viewer.
- One theme picker that colors both the UI and the diagrams. The choice is saved.
- Run on macOS, Linux and Windows.
- Simple English everywhere (UI, docs, code).

## Non-goals for v1

- Images, web page previews, kitty graphics, browser rendering.
- Editing files.
- Diagram types that termaid does not support.

## Screen

```
╭─ BLUEPRINT ───────────────────── ◉ follow ─╮
│ docs/order-flow.md          2m ago · agent │
├────────────────────────────────────────────┤
│  # Order flow                              │
│                                            │
│   ┌─────────────────┐                      │
│   │     Client      │   Mermaid fences     │
│   └────────┬────────┘   render as diagrams │
│            ▼                               │
│   ┌────────◇────────┐                      │
│   │   Auth filter   │                      │
│   └─────────────────┘                      │
├────────────────────────────────────────────┤
│ ◂ order-flow.md · erd.mmd · ✎ agent draw ▸ │
│ t theme  ←/→ history  f follow  q quit     │
╰────────────────────────────────────────────╯
```

- Header: current source, when it changed, who sent it, follow state.
- Body: Markdown rendered by Textual. Mermaid fences and `.mmd` files are
  drawn by termaid with theme colors. The body scrolls.
- Footer: recent items as tabs, plus key hints.

Keys:

| Key | Action |
|---|---|
| `t` | Open the theme picker |
| `←` / `→` | Previous / next item in history |
| `f` | Turn follow mode on or off |
| `r` | Reload the current item |
| `q` | Quit |

## Architecture

Python 3.10+, managed with `uv`. Package `herdr_blueprint`.

| Module | Job | Depends on |
|---|---|---|
| `cli.py` | Entry point: `herdr-blueprint` (run the viewer), `show PATH`, `draw` (Mermaid from stdin) | `app`, `inbox` |
| `app.py` | Textual app: layout, keys, wiring between sources and views | all below |
| `app.tcss` | Styles | |
| `themes.py` | Palettes. Each palette becomes a Textual theme and a termaid theme | `textual`, `termaid` |
| `diagram.py` | `render_diagram(source, palette) -> Text`. The only module that imports termaid | `termaid` |
| `document.py` | Markdown view. Mermaid fences use `diagram.py`; other fences keep normal highlighting | `textual`, `diagram` |
| `history.py` | Ordered list of recent items (path or snippet, source, time) | |
| `sources/follow.py` | Watches the workspace folder and reports saved `.md`/`.mmd` files | `watchfiles` |
| `sources/inbox.py` | File-based message queue for agent pushes | `platformdirs` |
| `config.py` | Saved settings (theme, follow on/off) | `platformdirs` |

Boundaries:

- `diagram.py` hides termaid. If termaid ever needs replacing (for example by
  mermaid-ascii), only this module changes.
- `sources/*` know nothing about the UI. They emit `Item` objects.
- `themes.py` is the single source of colors.

## Data flow

### Follow mode

1. `follow.py` watches the workspace root with `watchfiles`.
2. It keeps files ending in `.md`, `.markdown`, `.mmd`, `.mermaid`.
3. It skips `.git`, `node_modules`, `.venv`, `dist`, `build`, and hidden folders.
4. Changes are debounced (300 ms). The newest file becomes an `Item`.
5. If follow mode is on, the view switches to it. Otherwise it only joins history.

Workspace root, first match wins:

1. `--root PATH` on the command line.
2. The focused pane's `foreground_cwd`, then `cwd`, from `HERDR_PLUGIN_CONTEXT_JSON`.
3. The current directory.

### Agent push

1. The agent runs `herdr-blueprint show PATH` or pipes Mermaid into
   `herdr-blueprint draw`.
2. The CLI writes one JSON message into the inbox folder:
   `<user state dir>/herdr-blueprint/inbox/<workspace id>/`.
   It writes a temp file first, then renames it, so the viewer never reads half a file.
3. The viewer watches the inbox, reads each message, deletes it, and shows the item
   at the front, even when follow mode is off.
4. Workspace id comes from `HERDR_WORKSPACE_ID`, or `default` outside herdr.

Message shape:

```json
{"kind": "file", "path": "/abs/path/flow.md", "sent_by": "agent", "sent_at": 1791373241}
{"kind": "mermaid", "source": "graph LR\n  A --> B", "title": "Order flow", "sent_by": "agent", "sent_at": 1791373241}
```

### Rendering

- `.md` / `.markdown`: Markdown view. Fences with info `mermaid` become diagrams.
- `.mmd` / `.mermaid` and `draw` snippets: one diagram, centered.
- Diagrams use the active palette. Changing the theme redraws them.

## Themes

A palette holds UI colors (background, surface, text, muted, primary, accent,
warning, error, success) and diagram roles (node, edge, arrow, label,
edge label, subgraph).

Built-in palettes:

- `rose-pine` (default, matches herdr's default look)
- `blueprint` (signature theme: deep blue background, white and cyan lines)
- `catppuccin-mocha`, `tokyo-night`, `nord`, `gruvbox`, `dracula`

Each palette is registered as a Textual theme and as a termaid theme. termaid
only accepts theme names today, so Blueprint adds its themes to termaid's
theme table. This uses termaid internals, so the termaid version is pinned
(`>=0.9,<0.10`). Upstream proposal: let `render_rich` accept a `Theme` object.

Theme picker (`t`): a list of palettes. Moving the cursor previews the theme
live. `Enter` saves it, `Esc` restores the previous one.

## Errors

| Case | What the user sees |
|---|---|
| termaid returns empty output | A notice "Could not draw this diagram" and the Mermaid source |
| termaid raises an exception | The same notice with the error message |
| Followed file deleted or unreadable | Header notice; the last content stays on screen |
| Inbox message is not valid JSON | Message skipped and logged |
| No item yet | A welcome screen with key hints and an example diagram |

## herdr integration

- Plugin id `seeun.blueprint`, name "Blueprint", platforms `macos`, `linux`, `windows`.
- Build step: `uv sync --frozen`.
- Pane entrypoint `viewer`: `uv run --frozen herdr-blueprint`, opened as a split.
- Action `open`: opens the viewer pane next to the focused pane.
- Agent skill `blueprint` (in `skills/`): tells agents how to send a file or a
  diagram. It finds the plugin folder with `herdr plugin list --json`, then runs
  `uv run --project <root> herdr-blueprint draw`.

## Cross-platform rules

- No bash in any command. All logic is Python; commands start with `uv`.
- Paths use `pathlib`. Config and state folders come from `platformdirs`.
- File watching uses `watchfiles` (macOS, Linux, Windows).
- Agent push uses files, not sockets or named pipes.

## Testing

- Unit tests (pytest): palette mapping, inbox write/read and atomic rename,
  follow filters, Markdown fence handling, diagram error handling, CLI arguments.
- Visual tests: `pytest-textual-snapshot` for the welcome screen, a Markdown
  document with a diagram, and every palette. These catch visual regressions.
- CI: GitHub Actions on `ubuntu-latest`, `macos-latest`, `windows-latest` with `uv`.

## Open source

- termaid, Textual, Rich and watchfiles use the MIT license; platformdirs uses MIT.
  Blueprint installs them as normal dependencies and credits them in the README.
- Blueprint itself is MIT.

## Naming

"Blueprint", package and repo `herdr-blueprint`. Checked on 2026-10-09: no
`herdr-plugin` topic repo uses the name, no GitHub repo is named
`herdr-blueprint`, and the name is free on PyPI and npm.
