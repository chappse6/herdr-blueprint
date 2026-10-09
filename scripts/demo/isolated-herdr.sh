#!/bin/sh
# Run herdr (or `herdr <command>`) with its own config, state and shell, so the
# demo shows no saved machines, workspaces or prompt from your own setup.
# Usage: scripts/demo/isolated-herdr.sh herdr [args...]
HERE="$(cd "$(dirname "$0")" && pwd)"
DEMO="${BLUEPRINT_DEMO_DIR:-$HOME/.cache/blueprint-demo}"
mkdir -p "$DEMO/herdr" "$DEMO/zsh"
cp "$HERE/herdr-config.toml" "$DEMO/herdr/config.toml"
cp "$HERE/zshrc" "$DEMO/zsh/.zshrc"
# Drop the outer herdr and agent session, so nothing treats this as nested.
for name in $(env | sed -n -E 's/^((HERDR_|CLAUDE|CODEX_)[A-Z_]*|AI_AGENT)=.*/\1/p'); do
  unset "$name"
done
export XDG_CONFIG_HOME="$DEMO" XDG_STATE_HOME="$DEMO/state" ZDOTDIR="$DEMO/zsh"
export TERM=xterm-256color COLORTERM=truecolor
exec "$@"
