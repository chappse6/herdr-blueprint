<p align="center">
  <img src="docs/logo.png" width="160" alt="Blueprint logo: a rolled-out blueprint with a terminal prompt">
</p>

<h1 align="center">Blueprint</h1>

<p align="center">Live Markdown and Mermaid for your agent's side pane.</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-blue" alt="macOS, Linux and Windows">
  <img src="https://img.shields.io/badge/herdr-plugin-1f6feb" alt="herdr plugin">
</p>

<p align="center">
  <img src="docs/screenshots/document.png" width="49%" alt="Blueprint showing a Markdown document with a Mermaid flowchart">
  <img src="docs/screenshots/blueprint-theme.png" width="49%" alt="The same document in the Blueprint theme">
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
| `g` `G` | Top, bottom |
| `ctrl+d` `ctrl+u` | Half a page down, up |
| `a` | Ask the agent next to Blueprint to draw its task |
| `o` | Open a Markdown or Mermaid file from that pane's folder |
| `t` | Change theme |
| `r` | Reload |
| `q` | Quit |

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
root=$(herdr plugin list --json | jq -r '.result.plugins[] | select(.plugin_id == "seeun.blueprint") | .plugin_root')
uv run --project "$root" --no-sync herdr-blueprint send --draw --title "Flow" < flow.mmd
```

Teach Claude Code and Codex to send diagrams on their own:

    herdr plugin action invoke seeun.blueprint.install-skill

This links `skills/blueprint` into `~/.claude/skills` and `~/.agents/skills`
(a copy on Windows when symlinks are not allowed). Start a new agent session
to pick it up. Undo with `herdr-blueprint uninstall-skill`.

## Themes

Rosé Pine (default), Blueprint, Catppuccin Mocha, Tokyo Night, Nord, Gruvbox
and Dracula. Your choice is saved.

<img src="docs/screenshots/theme-picker.png" width="60%" alt="Theme picker with live preview">

## Development

```bash
uv sync
uv run pytest
uv run herdr-blueprint            # run outside herdr
herdr plugin link "$PWD"          # use this folder as the plugin
```

## Credits

Blueprint stands on these MIT-licensed projects:

- [termaid](https://github.com/fasouto/termaid) by Fabio Souto: Mermaid in the terminal
- [Textual](https://github.com/Textualize/textual) and [Rich](https://github.com/Textualize/rich) by Will McGugan and Textualize
- [watchfiles](https://github.com/samuelcolvin/watchfiles) by Samuel Colvin
- [platformdirs](https://github.com/tox-dev/platformdirs)

## License

MIT
