---
name: blueprint
description: Use when running inside a herdr pane (HERDR_ENV=1) with the Blueprint plugin, after finishing a unit of coding work (to redraw the code you are building while Blueprint is open), when a prompt says "In Blueprint I pointed at" or "I edited ... in Blueprint", and when a diagram or document would explain things better than chat text — architecture, request flows, sequences, data models, state machines — or when the user asks to "draw", "show a diagram", "visualize", "explain this in Blueprint", "open this in Blueprint", or "refresh Blueprint".
---

# Blueprint

Blueprint is a viewer in a herdr side pane. It shows one thing: the latest file
or diagram you sent. Each send replaces the screen. Send a diagram instead of
drawing ASCII art in chat.

## Commands

If `HERDR_ENV` is not `1`, skip this skill.

```bash
# A diagram you write now: --draw makes Blueprint draw it
herdr-blueprint send --draw --title "Order flow" <<'EOF2'
sequenceDiagram
  Client->>API: POST /orders
  API->>DB: INSERT order
  API-->>Client: 201 Created
EOF2

# Markdown works too: notes, tables, code, with Mermaid fences drawn by --draw
herdr-blueprint send --title "Plan" <<'EOF2'
# Plan
- step one
- step two
EOF2

# A file (.md, .markdown, .mmd, .mermaid); add --draw if it holds Mermaid to draw
herdr-blueprint show docs/design.md

# You changed the file on screen: redraw it in place
herdr-blueprint refresh --draw

# Is a Blueprint viewer open? Prints open or closed (exit 0 when open)
herdr-blueprint status
```

`herdr-blueprint` is in `~/.local/bin` once the skill is installed. If the shell
says it is not found, define it from the plugin folder in the same shell
command as each call (shell functions don't carry over between tool calls):

```bash
root=$(herdr plugin list --plugin seeun.blueprint --json | uv run --no-project --quiet python -c 'import json, sys; p = json.load(sys.stdin)["result"]["plugins"]; print(p[0]["plugin_root"] if p else "")')
herdr-blueprint() { uv run --project "$root" --no-sync python -m herdr_blueprint "$@"; }
```

Empty `$root` means the plugin is not installed: tell the user to run
`herdr plugin install chappse6/herdr-blueprint`.

## When to use `--draw`

- Use it when the point of the send is the diagram.
- Leave it off for plain documents or when the user only wants the text; Mermaid
  then shows as code, which is faster.
- The flag belongs to each send or refresh.

## When the user pointed at something in Blueprint

The prompt starts with "In Blueprint I pointed at ..." and names the part (a
box, branch, participant, message, heading, table...), where it is (file and
line, or the source of something you sent) and what the user wants.

- If it says "It stands for `path:line`", it is a work order on that code: read
  the code there, make the change the note asks for and anything that depends
  on it, and run the tests. Then redraw the design so it shows the change, with
  its `%% code:` lines updated, and send it.
- Otherwise change that part and anything it describes, then show the result in
  Blueprint again: `show` the file, or `send` the new version.

Use `--draw` when the point is the picture.

## When the user edited something in Blueprint

The prompt starts with "I edited ... in Blueprint" and holds a diff (for a
file) or the new version (for something you sent). A long one is in a file the
prompt names; read it right away, it is deleted when Blueprint closes. Apply it: change the file
and anything that depends on it. If the prompt says the file changed on disk,
read the file first instead of applying the diff blindly. If the prompt
mentions `%% code:` lines, the diagram is a design tied to code: make the change
in that code, run the tests, then send the updated design. Then show the result
in Blueprint again: `show` the file, or `send` the new version, with `--draw`
when the point of it is the picture.

## When asked what you are working on

The user pressed `a` in Blueprint. A bare diagram doesn't say what is going on,
so send Markdown with `--draw`, in the user's language:

1. A `#` heading with the task.
2. Two to four short lines: the goal, what is done, what you are doing now.
   Progress goes here, not in the diagram.
3. A `mermaid` fence with the code you are building: how it flows, where it
   branches (a `{choice}` with labelled arms), or how its parts fit. Not the
   steps of your task: the user points at this picture to ask for changes to
   the code, so every box should be a piece of it.
4. A `%% code:` line for every box (see "Tie boxes to code").

## Keep the picture current

When you finish a unit of work (a feature piece, a fix, a change to the flow)
and before you report it in chat:

1. Run `herdr-blueprint status`. If it prints `closed`, stop here: no drawing.
2. Send the same note as "what you are working on", updated. Start from your
   last diagram and change only what changed.

Once per unit of work, not per edit. Skip it when the flow didn't change
(typos, comments, tests only).

## Tie boxes to code

Add a Mermaid comment for each box: `%% code: <node id> <path>:<line>`, with a
path relative to the repo. It doesn't show in the drawing; when the user points
at the box, Blueprint sends that location with the request.

Put them at the end of the diagram (termaid reads the diagram type from the
first line), under one comment in the user's language that says what they are,
for someone who opens the source in their editor:
`%% Blueprint: the code each box stands for (not drawn)`, or in Korean
`%% Blueprint: 상자별 코드 위치 (그림에는 안 나옴)`.

```
flowchart TD
  T[POST /token] --> V{PKCE valid}
  V -->|no| E[400 invalid_grant]
  V -->|yes| I[Issue tokens]
  %% Blueprint: the code each box stands for (not drawn)
  %% code: T src/routes/token.ts:20
  %% code: V src/auth/pkce.ts:10
  %% code: I src/auth/tokens.ts:31
```

- A labelled arrow is a branch the user can point at; without its own line it
  stands for its branch node's code. Give it one (`%% code: V->E path:line`)
  when that branch lives somewhere else. In a `sequenceDiagram`, messages are
  `message-1`, `message-2`... in order.
- Only locations you have seen. Leave a box without a line rather than guess.

## When asked to explain a feature

Read the code first: explain what it does, not what it probably does. Then send
Markdown with `--draw`, in the user's language:

1. A `#` heading naming the feature.
2. Two or three lines: what it does and the one idea that makes it work.
3. A `mermaid` fence: `sequenceDiagram` for a flow between parts, `flowchart`
   for structure or decisions, with `%% code:` lines for its boxes.
4. A table of the steps: `file:line` and the key point of each.

## Writing diagrams

- Keep the title short; it shows in the header.
- Plain Mermaid works best: `graph`/`flowchart`, `sequenceDiagram`, `erDiagram`,
  `classDiagram`, `stateDiagram-v2`. Keep diagrams under about 100 nodes.
- Use `TD` or `LR`: the drawing mirrors the text of `RL` diagrams and flips
  arrows in `BT` ones.
- Labels are plain text: no `<br/>` or other HTML, which breaks the node.
- The side pane is narrow, about 100 columns. Keep a `sequenceDiagram` to four
  participants and every message or edge label to about 20 characters
  (`POST /token`, not `POST /token with code_verifier and client_id`); put the
  detail in the text or table instead. Wide diagrams scroll out of view.
- Labels may use the user's language.
- In chat, say in one line what you sent ("Sent the order flow to Blueprint").
