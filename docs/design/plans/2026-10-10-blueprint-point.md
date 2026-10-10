# Point and ask Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `e` points at a part of what Blueprint shows (diagram node, sequence participant or message, document block); Enter asks the agent to change it; `e` again edits the text with a live preview in a pane below.

**Architecture:** A new Textual-free module `point.py` turns termaid's own layout (flowchart/state `compute_layout`, sequence `_compute_layout`) and Textual's Markdown block `source_range` into `Part`s with cell boxes and source lines. The app gains a point mode on top of the existing edit-mode send path (agent lookup, start offer, kept work), generalised from "an edit" to "a request". The text-editing step opens the editor in a herdr plugin pane (`editor` entry point running `edit-session`) and redraws from the copy on every save, falling back to `App.suspend()` outside herdr.

**Tech Stack:** Python 3.10+, Textual 8.2.8, termaid 0.9.0 (pinned; internals used), watchfiles, herdr 0.9.3 plugin panes.

**Spec:** `docs/design/specs/2026-10-10-blueprint-point-design.md` (extends `2026-10-10-blueprint-edit-design.md`).

## Global Constraints

- UI text in simple English; no new key outside point mode (`e` only).
- Blueprint never writes the shown file; the agent is the only writer.
- Never start an agent without asking; unsent work is kept per document.
- Nothing outlives the session but the theme (temp files removed; long bodies removed on close).
- Every failure is a header warning; nothing in these paths may close or freeze Blueprint (no exception may leave `App.suspend()`).
- termaid internals are pinned; a test checks part boxes against drawn box corners.
- macOS, Linux, Windows; the live editor pane is herdr-only, the suspend path stays.

## Review Focus

1. Diagrams drawn with the compact layout (Blueprint retried a smaller padding): parts must use that padding, or the highlight lands beside the box.
2. Wide diagrams scrolled sideways: moving to an off-screen part must scroll it into view; mouse clicks must account for the scroll offset.
3. A send replacing the screen while pointing or while the note line is open: point mode must end cleanly, and a typed note must be kept, not lost.
4. Diagrams termaid can't parse into parts (syntax it renders but its graph parser rejects, empty graphs): fall back to the whole diagram, never crash.
5. The live editor pane closed by hand or failing to open: no exit file arrives; Blueprint must not wait forever and must keep saved work.

Each of these gets a test in the task that owns the code.

---

### Task 1: Drawings report their layout

**Files:**
- Modify: `src/herdr_blueprint/diagram.py`
- Modify: `src/herdr_blueprint/document.py` (DiagramView keeps the drawing)
- Test: `tests/test_diagram.py`

**Interfaces:**
- Produces: `Drawing(text: Text, padding: tuple[int, int, int])`; `draw_diagram(source, palette, max_width) -> Drawing` (cached); `render_diagram()` unchanged (returns `draw_diagram(...).text`). `DiagramView.drawing: Drawing | None` set when drawn.

- [ ] **Step 1: failing test**

```python
def test_a_drawing_says_which_layout_it_used():
    wide = "graph LR\n" + "\n".join(f"  N{i}[Node number {i}] --> N{i+1}[Node number {i+1}]" for i in range(6))
    roomy = draw_diagram("graph LR\n  A --> B", get_palette("rose-pine"), 200)
    tight = draw_diagram(wide, get_palette("rose-pine"), 60)
    assert roomy.padding == (4, 2, 4)
    assert tight.padding != (4, 2, 4) and width_of(tight.text) <= 60 or tight.padding == (1, 0, 2)
```

- [ ] **Step 2:** run `uv run pytest tests/test_diagram.py -q` → FAIL (no `draw_diagram`).
- [ ] **Step 3:** in `_render`, return `Drawing(text, (padding_x, padding_y, gap))` for the layout that won; `render_diagram` returns `.text`. In `document.draw`, keep using `render_diagram`; add `DiagramView._draw` storing `self.drawing = draw_diagram(...)` before `update`.
- [ ] **Step 4:** tests pass, full suite green.
- [ ] **Step 5:** commit "Report which layout a drawing used".

### Task 2: Parts of a diagram

**Files:**
- Create: `src/herdr_blueprint/point.py`
- Test: `tests/test_point.py`

**Interfaces:**
- Consumes: `Drawing.padding`.
- Produces:
  - `Box(x, y, width, height)` in drawing cells; `Part(kind: str, id: str, label: str, boxes: tuple[Box, ...], lines: tuple[int, int] | None)`; kinds: `node`, `participant`, `message`, `diagram`, and the document kinds of Task 3.
  - `diagram_parts(source: str, padding: tuple[int,int,int]) -> list[Part]` (empty when not a flowchart/graph/stateDiagram/sequenceDiagram or when termaid's parser fails).
  - `whole_diagram(source) -> Part`.
  - `highlight(text: Text, part: Part, style: Style) -> Text` (copy; columns converted to character indexes with `cell_len`).
  - `nearest(parts, current: int, direction: str) -> int` (`left/right/up/down` by box centers; ties by distance; stays put when nothing lies that way).
  - `describe(part) -> str` ("the box \"Tollgate\"", "the participant \"Redis\"", "the message \"GET /authorize\" from Browser to Tollgate", "the whole diagram").
- Notes: flowchart/state via `termaid.parser.flowchart.parse_flowchart` / `termaid.parser.statediagram.parse_state_diagram` and `termaid.layout.grid.compute_layout(graph, px, py, gap)`; placements give `draw_x/draw_y/draw_width/draw_height`; skip synthetic state ids (`[*]_start_*`, `[*]_end_*`). Sequence via `parse_sequence_diagram`, `seq._flatten_events`, `seq._compute_layout(diagram, autonumber, flat, padding_x, 16)`; participant box `x = center - width // 2`, `y = 0`, height = header height; a message (flat event of class `Message`) spans columns between its two participants' centers, rows `row - 1` (label) and `row` (arrow). Source line of a node: first line matching `^\s*{id}\b` or `\b{id}\s*[\[\(\{>]`, else first line containing it as a word; participant: `participant|actor {id}` line, else first message using it; message: the n-th line containing `->>`, `-->>`, `->`, `-->`, `-x`, `--x`, `-)`, `--)`.

- [ ] **Step 1: failing tests** (each asserts real drawn characters, so a termaid upgrade that moves boxes fails here):

```python
def corners(text, box):
    rows = text.plain.split("\n")
    def at(x, y):  # character at drawing column x
        col = 0
        for ch in rows[y]:
            if col == x:
                return ch
            col += cell_len(ch)
        return " "
    return at(box.x, box.y), at(box.x + box.width - 1, box.y + box.height - 1)

@pytest.mark.parametrize("padding", [(4, 2, 4), (2, 1, 3), (1, 0, 2)])
def test_node_boxes_sit_on_the_drawn_corners(padding):
    src = "graph LR\n  A[브라우저] --> B[Tollgate]\n  B --> C[Redis]"
    text = render_rich(src, theme="default", padding_x=padding[0], padding_y=padding[1], gap=padding[2])
    parts = point.diagram_parts(src, padding)
    assert [p.label for p in parts] == ["브라우저", "Tollgate", "Redis"]
    for part in parts:
        top_left, bottom_right = corners(text, part.boxes[0])
        assert top_left in "┌╭" and bottom_right in "┘╯"

def test_sequence_participants_and_messages():
    src = "sequenceDiagram\n  participant B as Browser\n  participant T as Tollgate\n  B->>T: GET /authorize\n  T-->>B: 302"
    parts = point.diagram_parts(src, (4, 2, 4))
    assert [(p.kind, p.label) for p in parts] == [
        ("participant", "Browser"), ("participant", "Tollgate"),
        ("message", "GET /authorize"), ("message", "302"),
    ]
    assert parts[2].lines == (4, 4) and parts[1].lines == (3, 3)  # 1-based source lines

def test_state_diagram_nodes_skip_the_start_dot(): ...
def test_unparseable_or_other_diagrams_have_no_parts(): ...  # "pie title X\n  \"a\": 1" -> []
def test_nearest_moves_by_direction(): ...  # A B C in a row: right from A is B, left from A stays A
def test_highlight_reverses_only_the_box(): ...  # ANSI has 7 on B's cells only
def test_descriptions(): ...
```

- [ ] **Step 2:** run → FAIL (no module).
- [ ] **Step 3:** implement `point.py` as in the Notes.
- [ ] **Step 4:** pass; full suite green.
- [ ] **Step 5:** commit "Find the parts of a diagram from termaid's layout".

### Task 3: Parts of a document

**Files:**
- Modify: `src/herdr_blueprint/point.py`
- Test: `tests/test_point.py`

**Interfaces:**
- Produces: `document_part(token_type: str, source_range: tuple[int, int], source: str) -> Part` with kinds `heading`, `paragraph`, `list`, `table`, `quote`, `code`, `diagram` (a `mermaid` fence), `rule`; label = first non-empty source line of the block without Markdown markers, cut at 40 characters; `lines` = 1-based inclusive range; `describe` gives "the heading \"Login flow\"", "the paragraph \"Authorization code…\"", "the table under \"Steps\"" (nearest heading above, when known).

- [ ] Steps: failing tests from a sample document's real `DocumentView` block widgets (`block.source_range`, `block._token.type`); implement; pass; commit "Find the blocks of a document".

### Task 4: Point mode in the app

**Files:**
- Modify: `src/herdr_blueprint/app.py`, `src/herdr_blueprint/document.py`, `src/herdr_blueprint/app.tcss`
- Create: `tests/test_point_app.py`

**Interfaces:**
- Consumes: Tasks 1-3; the edit-mode send path.
- Produces: `BlueprintApp.pointing: bool`, `BlueprintApp.parts: list[Part]`, `BlueprintApp.selected: int`; actions `edit` (enter point mode / second `e` edits text), `point_move(direction)`, `point_ask`, `point_done`; `NoteLine(Input)` docked above the footer; `DiagramView.show(part | None)`; document blocks get class `pointed`.
- Generalise the send path: `_send_kept(KeptEdit)` becomes `_send_request(Request)` where `Request(key, describe: str, build: Callable[[Source], str], keep: KeptEdit | KeptNote)`; `_sent` uses `request.describe` for "Asked claude about …"; kept notes live in `self._kept` next to kept edits, keyed by `(doc_key, part.id)`.
- Contextual footer: in point mode `check_action` hides every viewer binding except `point_move`/`point_ask`/`edit`/`point_done`; outside it the point bindings are hidden.

- [ ] **Step 1: failing tests** (fakes as in `test_edit_app.py`):
  - `e` enters point mode on a drawn flowchart and selects the first node; `e` is hidden on welcome/start/plain view.
  - `l` then `j` move by direction; the highlight follows (DiagramView's rendered text has reverse video on the selected box only).
  - Enter opens the note; typing and Enter sends a prompt naming the part, its source line and the file path; Esc returns to pointing; Esc again leaves.
  - A send arriving while pointing ends point mode and keeps a typed note for that document; the next Enter on that part prefills it. (Review Focus 3)
  - A diagram drawn with the compact layout gets parts from that layout. (Review Focus 1)
  - Moving to an off-screen part scrolls it into view. (Review Focus 2)
  - An unparseable flowchart falls back to the whole diagram. (Review Focus 4)
  - Documents: `j`/`k` walk blocks; the prompt has the block's line range.
  - `e` while pointing opens the text editor with `+<line>` for vim-like editors.
  - Point mode is locked while asking; the footer shows only point keys.
- [ ] Steps 2-5: run (fail), implement, pass with full suite, commit "Point at a part and ask the agent to change it".

### Task 5: Live editor pane

**Files:**
- Modify: `herdr-plugin.toml` (`editor` pane), `src/herdr_blueprint/cli.py` (`edit-session`), `src/herdr_blueprint/edit.py` (`run_session`, `exit_file`), `src/herdr_blueprint/app.py`
- Test: `tests/test_edit.py`, `tests/test_edit_app.py`, `tests/test_manifest.py`

**Interfaces:**
- Produces: `edit.run_session(environ, run) -> int` (writes `<copy>.exit`); app `pane_editor: Callable[[list[str], Path], str | None]` (opens the plugin pane, returns its pane id or None to fall back); a watcher (watchfiles on the copy's folder) that redraws the preview on each save and finishes on the exit file; a 2-second pane check that treats a vanished pane as closed (Review Focus 5).

- [ ] **Step 1: failing tests:** `run_session` writes the exit code (and 127 when the editor can't start); manifest has the `editor` pane running `python -m herdr_blueprint edit-session`; app with a fake pane opener: a save redraws the preview with the copy's text; an exit file with 0 after a save sends; with 1 cancels; no save leaves nothing; the pane vanishing keeps saved work; sends during the edit are held, dropped when the edit was sent, shown otherwise; a second `e` focuses the pane; the opener returning None falls back to suspend.
- [ ] Steps 2-5: run, implement (move the spike code in), pass with full suite, commit "Edit with a live preview in a pane below Blueprint".

### Task 6: Docs and snapshots

**Files:** `README.md`, `skills/blueprint/SKILL.md`, `src/herdr_blueprint/app.py` (WELCOME), `tests/__snapshots__`

- [ ] README: "Point and ask" section and key table (`e` points; in point mode hjkl, Enter, `e`, Esc). SKILL: "When the user points at something in Blueprint" (what the prompt contains, change the part and what it describes, then show the result). WELCOME table row for `e` ("Point at a part and ask"). Update snapshots; check their text diffs; commit "Document point and ask".

### Task 7: Live check and review

- [ ] Isolated demo herdr: point at a node and a message, ask a real Claude Code, see the change; nvim in the pane below, watch `:w` redraw, `:wq` send, `:q!` nothing; fresh reviewer on the whole branch; fix Critical/Important with a failing test first; report deferred minors.
