# Blueprint

Live Markdown and Mermaid for your agent's side pane.

Blueprint is a [herdr](https://herdr.dev) plugin. Put it next to your coding
agent and it shows the docs and diagrams the agent writes, drawn in your
terminal with colors that match your theme.

- **Follow mode.** Save a `.md` or `.mmd` file and Blueprint opens it.
- **Agent push.** Your agent can send a diagram while it explains something.
- **Real diagrams in text.** Mermaid is drawn with [termaid](https://github.com/fasouto/termaid):
  flowcharts, sequence, ER, class, state and more.
- **Themes.** Press `t` to pick a theme. The UI and the diagrams change together.
- **Runs everywhere.** macOS, Linux and Windows. No browser, no images.

## Install

You need [uv](https://docs.astral.sh/uv/) and herdr 0.9.3 or newer.

```bash
herdr plugin install chappse6/herdr-blueprint
herdr plugin action invoke seeun.blueprint.open
```

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
| `t` | Change theme |
| `←` `→` | Move through history |
| `f` | Follow new files on or off |
| `r` | Reload |
| `q` | Quit |

## Send things from an agent

```bash
herdr-blueprint show docs/design.md
printf 'graph LR\n  A --> B\n' | herdr-blueprint draw --title "Flow"
```

The command lives in the plugin's own environment. From an agent, run it
through the plugin folder:

```bash
root=$(herdr plugin list --json | jq -r '.result.plugins[] | select(.plugin_id == "seeun.blueprint") | .plugin_root')
uv run --project "$root" --no-sync herdr-blueprint draw --title "Flow" < flow.mmd
```

Teach Claude Code and Codex to send diagrams on their own:

    herdr plugin action invoke seeun.blueprint.install-skill

This links `skills/blueprint` into `~/.claude/skills` and `~/.agents/skills`
(a copy on Windows when symlinks are not allowed). Start a new agent session
to pick it up. Undo with `herdr-blueprint uninstall-skill`.

## Themes

Rosé Pine (default), Blueprint, Catppuccin Mocha, Tokyo Night, Nord, Gruvbox
and Dracula. Your choice is saved.

## Development

```bash
uv sync
uv run pytest
uv run herdr-blueprint --root .   # run outside herdr
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
