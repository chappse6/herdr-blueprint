---
name: blueprint
description: Use when running inside a herdr pane (HERDR_ENV=1) with the Blueprint plugin and a diagram or document would explain things better than chat text — architecture, request flows, sequences, data models, state machines — or when the user asks to "draw", "show a diagram", "visualize", or "open this in Blueprint".
---

# Blueprint

Blueprint is a viewer in a herdr side pane. It draws Mermaid diagrams and Markdown
in the terminal. Send it a diagram instead of drawing ASCII art in chat.

## Commands

If `HERDR_ENV` is not `1`, skip this skill.

```bash
root=$(herdr plugin list --json | jq -r '.result.plugins[] | select(.plugin_id == "seeun.blueprint") | .plugin_root')

# A diagram you write now
uv run --project "$root" --no-sync herdr-blueprint draw --title "Order flow" <<'EOF'
sequenceDiagram
  Client->>API: POST /orders
  API->>DB: INSERT order
  API-->>Client: 201 Created
EOF

# A file that already exists (.md, .markdown, .mmd, .mermaid)
uv run --project "$root" --no-sync herdr-blueprint show docs/design.md
```

Empty `$root` means the plugin is not installed: tell the user to run
`herdr plugin install chappse6/herdr-blueprint`.

## Writing diagrams

- Keep the title short; it shows in the history bar.
- Use plain Mermaid: `graph`/`flowchart`, `sequenceDiagram`, `erDiagram`,
  `classDiagram`, `stateDiagram-v2` work best.
- Labels may use the user's language.
- In chat, say in one line what you sent ("Sent the order flow to Blueprint").
