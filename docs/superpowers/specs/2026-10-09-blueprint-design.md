# Blueprint: design

Date: 2026-10-09
Status: v1 decided

## The rule

> Blueprint shows one thing, the latest file or diagram the worker sent. A send
> or a requested refresh replaces the screen. It keeps updating with the
> situation, and it analyzes and draws only when the worker sets a flag.
> Nothing but the theme is kept.

The worker is the coding agent (or the person) that sends things to the viewer.

## Summary

Blueprint is a herdr plugin that turns a side pane into a viewer for Markdown
and Mermaid. A worker sends a file or a snippet; Blueprint shows it, styled for
the terminal, and replaces it when the next one arrives. No browser, no images.

## Goals

- Show the latest Markdown document or Mermaid diagram the worker sent, beautifully.
- Each send, and each refresh the worker asks for, replaces the screen.
- Draw Mermaid only when the worker sets the `--draw` flag. Without it, show
  the source cleanly as text. Drawing is the expensive part.
- One theme picker for the UI and the diagrams. Only the theme is saved.
- macOS, Linux and Windows. Simple English everywhere.

## Non-goals for v1

- History, tabs, or a list of past diagrams. Closing the viewer forgets everything but the theme.
- Following files on its own. Blueprint never switches the screen because some file changed.
- Images, web previews, browser rendering, editing.

## Screen

```
╭─ BLUEPRINT ───────────────────────────────╮
│ order-flow.md         from agent just now │
├───────────────────────────────────────────┤
│  # Order flow                             │
│                                           │
│   ┌─────────────────┐                     │
│   │     Client      │   drawn only with   │
│   └────────┬────────┘   --draw            │
│            ▼                              │
│   ┌────────◇────────┐                     │
│   │   Auth filter   │                     │
│   └─────────────────┘                     │
├───────────────────────────────────────────┤
│ t Theme   r Reload   q Quit               │
╰───────────────────────────────────────────╯
```

- Header: what is shown, who sent it and when. Below 60 columns the brand is
  hidden and the status is shortened so the title stays readable; a notice
  then takes the title's place.
- Body: Markdown by Textual. Mermaid is drawn by termaid when `--draw` is set,
  otherwise shown as a code block. Large documents (over 100 KB or 2,000 lines)
  are shown as plain text so the screen never freezes.
- Footer: key hints.

| Key | Action |
|---|---|
| `t` | Theme picker (live preview, `Enter` keeps, `Esc` cancels) |
| `r` | Reload what is shown |
| `q` | Quit |

## Commands

| Command | What the viewer does |
|---|---|
| `herdr-blueprint show PATH [--draw]` | Shows a `.md`, `.markdown`, `.mmd` or `.mermaid` file |
| `herdr-blueprint send [--title T] [--draw]` | Shows Markdown or Mermaid read from stdin |
| `herdr-blueprint refresh [--draw]` | Re-reads and redraws what is shown |
| `herdr-blueprint open` | Opens the viewer to the right of the focused pane, keeping focus |
| `herdr-blueprint install-skill` / `uninstall-skill` | Links the agent skill into Claude Code and Codex |

`--draw` belongs to each request: a refresh without it shows Mermaid as text
again. A snippet whose first line opens a Mermaid diagram (`graph`,
`sequenceDiagram`, `erDiagram`, …) is a diagram; anything else is Markdown.

## Architecture

Python 3.10+, managed with `uv` (uv's own Python builds only). Package `herdr_blueprint`.

| Module | Job |
|---|---|
| `cli.py` | Commands above; sends messages to the inbox |
| `app.py` | Textual app: one screen, header, keys, theme picker |
| `document.py` | Markdown view, diagram view, plain view; drawing runs in a worker thread |
| `diagram.py` | `render_diagram(source, palette, max_width)`; the only module that imports termaid; cached; refuses sources over 10,000 characters |
| `item.py` | What is shown: a file or a snippet, with its draw flag; safe reading |
| `sources/inbox.py` | File-based message queue, one folder per herdr workspace |
| `themes.py` | Palettes for Textual and termaid |
| `config.py` | Saves the theme |
| `skills_install.py` | Links or copies the agent skill |

## Data flow

1. The CLI writes one JSON message into
   `<user state dir>/herdr-blueprint/inbox/<HERDR_WORKSPACE_ID or "default">/`,
   via a temp file and a rename.
2. The viewer claims each message with a rename (so two viewers never show the
   same one), reads it and deletes it. Messages older than one hour are dropped.
3. On start, only the newest waiting item is shown; older items and refreshes are dropped.
4. An item replaces the screen. A refresh re-reads the current item with the
   refresh's draw flag. With nothing on screen, a refresh does nothing.

Message shapes:

```json
{"kind": "file", "path": "/abs/flow.md", "draw": false, "sent_by": "agent", "sent_at": 1791373241}
{"kind": "text", "source": "graph LR\n  A --> B", "title": "Flow", "draw": true, "sent_by": "agent", "sent_at": 1791373241}
{"kind": "refresh", "draw": false, "sent_at": 1791373241}
```

## Themes

Rosé Pine (default), Blueprint (deep blue, white and cyan lines), Catppuccin
Mocha, Tokyo Night, Nord, Gruvbox, Dracula. Each palette is a Textual theme
and a termaid theme. termaid only takes theme names, so Blueprint adds its
themes to termaid's theme table (internal API, version pinned `>=0.9,<0.10`;
upstream proposal: accept a `Theme` object).

## Errors

| Case | What the user sees |
|---|---|
| File missing or unreadable | Header notice; the screen keeps the last content |
| Bad bytes or control characters | Replaced with `�`; nothing reaches the terminal raw |
| File over 1 MB | First 1,000,000 bytes, with a notice |
| Mermaid termaid can't draw, or over 10,000 characters | "Could not draw this diagram" and the source |
| Broken, unknown or old inbox message | Skipped and logged |
| The inbox stops working | Header notice; the app stays open |

## herdr integration

- Plugin id `seeun.blueprint`; platforms `macos`, `linux`, `windows`.
- Build step `uv sync --frozen --no-dev`; pane `viewer` runs `uv run --no-sync herdr-blueprint` as a split.
- Actions: `open`, `install-skill`.
- Agent skill `skills/blueprint`: send diagrams with `send --draw`, files with
  `show`, and `refresh` after editing what is on screen.

## Cross-platform rules

No bash in any command; paths with `pathlib`; folders from `platformdirs`;
the inbox uses files, not sockets; CLI input and output are UTF-8 whatever the
console code page is; skill install falls back to a copy when symlinks are refused.

## Testing

Unit tests for items, inbox, CLI, diagrams, themes and skill install; app tests
in Textual's headless mode, including UI responsiveness with a 1 MB document and
a slow diagram (largest event-loop pause under 0.5 s); snapshot tests for every
theme and the main screens. CI runs on Ubuntu, macOS and Windows.

## Open source

termaid, Textual, Rich, watchfiles and platformdirs are MIT-licensed and
installed as normal dependencies; the README credits them. Blueprint is MIT.

## Naming

"Blueprint", package and repo `herdr-blueprint`. Checked on 2026-10-09: free
on the herdr marketplace, GitHub, PyPI and npm.
