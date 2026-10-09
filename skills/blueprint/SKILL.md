---
name: blueprint
description: Use when running inside a herdr pane (HERDR_ENV=1) with the Blueprint plugin and a diagram or document would explain things better than chat text — architecture, request flows, sequences, data models, state machines — or when the user asks to "draw", "show a diagram", "visualize", "explain this in Blueprint", "open this in Blueprint", or "refresh Blueprint".
---

# Blueprint

Blueprint is a viewer in a herdr side pane. It shows one thing: the latest file
or diagram you sent. Each send replaces the screen. Send a diagram instead of
drawing ASCII art in chat.

## Commands

If `HERDR_ENV` is not `1`, skip this skill. Run the `root=` and `blueprint_cli()`
lines in the same shell command as each call: shell variables don't carry over
between tool calls.

```bash
root=$(herdr plugin list --plugin seeun.blueprint --json | uv run --no-project --quiet python -c 'import json, sys; p = json.load(sys.stdin)["result"]["plugins"]; print(p[0]["plugin_root"] if p else "")')
# Not named blueprint: a `blueprint` alias would make this a parse error.
blueprint_cli() { uv run --project "$root" --no-sync python -m herdr_blueprint "$@"; }

# A diagram you write now: --draw makes Blueprint draw it
blueprint_cli send --draw --title "Order flow" <<'EOF2'
sequenceDiagram
  Client->>API: POST /orders
  API->>DB: INSERT order
  API-->>Client: 201 Created
EOF2

# Markdown works too: notes, tables, code, with Mermaid fences drawn by --draw
blueprint_cli send --title "Plan" <<'EOF2'
# Plan
- step one
- step two
EOF2

# A file (.md, .markdown, .mmd, .mermaid); add --draw if it holds Mermaid to draw
blueprint_cli show docs/design.md

# You changed the file on screen: redraw it in place
blueprint_cli refresh --draw
```

Empty `$root` means the plugin is not installed: tell the user to run
`herdr plugin install chappse6/herdr-blueprint`.

## When to use `--draw`

- Use it when the point of the send is the diagram.
- Leave it off for plain documents or when the user only wants the text; Mermaid
  then shows as code, which is faster.
- The flag belongs to each send or refresh.

## When asked what you are working on

The user pressed `a` in Blueprint. A bare diagram doesn't say what is going on,
so send Markdown with `--draw`, in the user's language:

1. A `#` heading with the task.
2. Two to four short lines: the goal, what is done, what you are doing now.
3. A `mermaid` fence with the flow or structure you are working on.

## When asked to explain a feature

Read the code first: explain what it does, not what it probably does. Then send
Markdown with `--draw`, in the user's language:

1. A `#` heading naming the feature.
2. Two or three lines: what it does and the one idea that makes it work.
3. A `mermaid` fence: `sequenceDiagram` for a flow between parts, `flowchart`
   for structure or decisions.
4. A table of the steps: `file:line` and the key point of each.

## Writing diagrams

- Keep the title short; it shows in the header.
- Plain Mermaid works best: `graph`/`flowchart`, `sequenceDiagram`, `erDiagram`,
  `classDiagram`, `stateDiagram-v2`. Keep diagrams under about 100 nodes.
- Labels are plain text: no `<br/>` or other HTML, which breaks the node.
- The side pane is narrow, about 100 columns. Keep a `sequenceDiagram` to four
  participants and every message or edge label to about 20 characters
  (`POST /token`, not `POST /token with code_verifier and client_id`); put the
  detail in the text or table instead. Wide diagrams scroll out of view.
- Labels may use the user's language.
- In chat, say in one line what you sent ("Sent the order flow to Blueprint").
