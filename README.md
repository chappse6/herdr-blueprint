<p align="center">
  <img src="docs/logo.png" width="160" alt="Blueprint logo: a rolled-out blueprint with a terminal prompt">
</p>

<h1 align="center">Blueprint</h1>

<p align="center"><b>Your coding agent explains its work as diagrams, right next to the chat.</b></p>

<p align="center">Live Markdown and Mermaid for your agent's side pane, in your terminal.</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-blue" alt="macOS, Linux and Windows">
  <img src="https://img.shields.io/badge/herdr-plugin-1f6feb" alt="herdr plugin">
</p>

<p align="center">
  <img src="docs/screenshots/herdr-demo.gif" alt="Claude Code is asked to explain a login flow, and seconds later Blueprint draws it as a sequence diagram next to the chat">
  <br>
  <sub>Ask your agent to explain something. Blueprint draws it next to the chat. (<a href="docs/screenshots/herdr-demo.mp4">MP4</a>)</sub>
</p>

Blueprint is a [herdr](https://herdr.dev) plugin. Put it next to your coding
agent and it shows one thing: the latest doc or diagram the agent sent, drawn
in your terminal with colors that match your theme.

- **One screen.** Each send replaces what is shown. Nothing piles up.
- **Draw on request.** Mermaid is drawn with [termaid](https://github.com/fasouto/termaid)
  when the sender adds `--draw`; otherwise it stays as clean text.
- **Refresh on request.** The agent (or `r`) redraws what is on screen.
- **Knows its neighbor.** Opened next to an agent, it shows the agent, its task
  and folder. Press `a` to ask the agent to draw its task, or `o` to open a doc
  from that folder.
- **Themes.** Press `t` to pick a theme. Only your theme is saved.
- **Runs everywhere.** macOS, Linux and Windows. No browser, no images.

<p align="center">
  <img src="docs/screenshots/herdr-demo.png" alt="herdr with Claude Code on the left explaining a login flow and Blueprint on the right drawing it as a sequence diagram">
</p>

## Next to your agent

Opened next to an agent, Blueprint starts with what it can do there: ask the
agent to draw its task (`a`), or open a doc from its folder (`o`).

<p align="center">
  <img src="docs/screenshots/start-screen.png" width="49%" alt="Start screen showing the agent next to Blueprint, its task and folder">
  <img src="docs/screenshots/file-picker.png" width="49%" alt="File picker listing the folder's Markdown and Mermaid files, newest first">
</p>

## Install

You need [uv](https://docs.astral.sh/uv/) and herdr 0.9.3 or newer.

```bash
herdr plugin install chappse6/herdr-blueprint
herdr plugin action invoke seeun.blueprint.open
```

It opens to the right of the focused pane and takes the focus. If Blueprint is
already open in the workspace, it is focused instead of opening a second one.
Bind it to a key in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+alt+b"
type = "plugin_action"
command = "seeun.blueprint.open"
description = "open Blueprint"
```

## Keys

| Key | Action |
|---|---|
| `h` `j` `k` `l` | Move left, down, up, right |
| `g` `G` | Top, bottom (pointing: first, last part) |
| `ctrl+d` `ctrl+u` | Half a page down, up (pointing: the selection moves) |
| `e` | Point at a part to ask about it (then `e` again edits the text) |
| `a` | Ask the agent next to Blueprint to draw its task |
| `o` | Open a Markdown or Mermaid file from that pane's folder |
| `t` | Change theme |
| `r` | Reload |
| `q` | Quit |

## Point and ask

Press `e` and the first part of what Blueprint shows is selected: a box or a
branch (a labelled arrow, or an arm of a choice) in a flowchart or state
diagram, a participant or message in a sequence diagram, a heading, paragraph,
list or table in a document. A diagram drawn inside a document is pointed at
box by box too: `j` and `k` walk the document and step into it, `h` `j` `k` `l`
move between its boxes, and going past its top or bottom leaves it. Click a
part to select it. Press Enter, say what should change, and press Enter again:
the agent next to Blueprint gets the part, where it is in the source and your
note. Esc steps back. While you type a note, whatever the agent sends waits
until you close it, and a note you keep moves to the next version of the same
diagram.

When the agent draws the code it is building, it ties each box to that code
with a comment Blueprint doesn't draw:

```
flowchart TD
  T[POST /token] --> V{PKCE valid}
  V -->|no| E[400 invalid_grant]
  V -->|yes| I[Issue tokens]
  %% Blueprint: the code each box stands for (not drawn)
  %% code: V src/auth/pkce.ts:10
```

Point at such a box (or one of its branches), write "return 401 when the code
expired", and the request is a work order: the agent changes the code at
`src/auth/pkce.ts:10`, runs the tests, and redraws the design.

To change the text yourself, press `e` again while pointing. Your editor
(`$BLUEPRINT_EDITOR`, `$VISUAL`, `$EDITOR`, else vim, vi or nano) opens on that
line, in a pane below Blueprint inside herdr, and every save redraws the drawing
above. Save and quit (`:wq`) and the change goes to the agent: a diff for a
file, the new version for something the agent sent. Quit without saving (`:q!`)
and nothing happens. Blueprint never writes the file itself: the agent applies
the change, and you roll it back the way you roll back any agent change. Set
`BLUEPRINT_EDITOR` when your `$EDITOR` is set up for something else, such as an
editor that starts in insert mode.

With no agent there but a shell, Blueprint offers to start one. A note that
could not be sent is kept for that part; an edit, for that document. Point
again (or press `e` twice) to pick it up, or save an edit empty to throw it
away.

## Send things from an agent

```bash
herdr-blueprint show docs/design.md               # a file
printf 'graph LR\n  A --> B\n' | herdr-blueprint send --draw --title "Flow"
herdr-blueprint refresh --draw                    # redraw what is shown
```

`--draw` makes Blueprint draw Mermaid. Without it, diagrams show as text, which
is faster. Each send or refresh carries its own flag.

The command lives in the plugin's own environment. From an agent, run it
through the plugin folder:

```bash
root=$(herdr plugin list --plugin seeun.blueprint --json | uv run --no-project --quiet python -c 'import json, sys; p = json.load(sys.stdin)["result"]["plugins"]; print(p[0]["plugin_root"] if p else "")')
uv run --project "$root" --no-sync python -m herdr_blueprint send --draw --title "Flow" < flow.mmd
```

Teach Claude Code and Codex to send diagrams on their own:

    herdr plugin action invoke seeun.blueprint.install-skill

This links `skills/blueprint` into `~/.claude/skills` and `~/.agents/skills`
(a copy on Windows when symlinks are not allowed). Start a new agent session
to pick it up. Undo with `herdr-blueprint uninstall-skill`.

## Token use

Blueprint itself uses no tokens; the agent does when it writes what Blueprint
shows. The skill asks the agent to redraw its design after each unit of work
(a feature piece, a fix, a change to the flow), but only while Blueprint is
open: it first runs `status`, a short command, and draws nothing when Blueprint
is closed. Agents load a skill when its description matches what they are
doing; if yours doesn't redraw, press `a`.

Rough figures, not measurements (they vary with the model, the language and the
size of the diagram):

| What | Tokens |
|---|---|
| One redraw: a summary, a diagram of 10 to 20 boxes, its `%% code:` lines | about 0.5k to 1.5k written; text in languages other than English takes more |
| The check when Blueprint is closed | about 100 written, plus one more step |
| Loading the skill, once per session | about 2k read |
| An hour with ten units of work | about 5k to 15k written |

Each redraw is also one more step for the agent, which reads its conversation
again; prompt caching makes that cheap, but it counts toward usage limits. To
use fewer tokens, close Blueprint while you don't look at it, or tell the agent
to redraw only when you press `a`.

## Themes

Rosé Pine (default), Blueprint, Catppuccin Mocha, Tokyo Night, Nord, Gruvbox
and Dracula. Your choice is saved.

<p align="center">
  <img src="docs/screenshots/document.png" width="49%" alt="A Markdown document with a Mermaid flowchart in Rosé Pine">
  <img src="docs/screenshots/blueprint-theme.png" width="49%" alt="The same document in the Blueprint theme">
</p>

<img src="docs/screenshots/theme-picker.png" width="60%" alt="Theme picker with live preview">

## Development

```bash
uv sync
uv run pytest
uv run herdr-blueprint            # run outside herdr
herdr plugin link "$PWD"          # use this folder as the plugin
```

README images come from `scripts/screenshots.py` (themes) and
[`scripts/demo`](scripts/demo/README.md) (herdr with an agent).

## Credits

Blueprint stands on these MIT-licensed projects:

- [termaid](https://github.com/fasouto/termaid) by Fabio Souto: Mermaid in the terminal
- [Textual](https://github.com/Textualize/textual) and [Rich](https://github.com/Textualize/rich) by Will McGugan and Textualize
- [watchfiles](https://github.com/samuelcolvin/watchfiles) by Samuel Colvin
- [platformdirs](https://github.com/tox-dev/platformdirs)

## License

MIT
