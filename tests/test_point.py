"""Parts of what Blueprint shows: boxes in termaid's drawing, lines in the source."""

from dataclasses import replace

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.style import Style
from rich.text import Text
from termaid import render_rich

import herdr_blueprint.diagram  # noqa: F401  (themes and the wide-character fix, as in the app)
from herdr_blueprint import point


def draw(source, padding=(4, 2, 4)):
    return render_rich(source, theme="default", padding_x=padding[0], padding_y=padding[1], gap=padding[2])


def char_at(text, x, y):
    """The character at drawing column x (wide characters take two columns)."""
    col = 0
    for ch in text.plain.split("\n")[y]:
        if col == x:
            return ch
        col += cell_len(ch)
    return " "


def assert_on_corners(text, part):
    for box in part.boxes:
        assert char_at(text, box.x, box.y) not in " ", (part, "top-left")
        assert char_at(text, box.x + box.width - 1, box.y + box.height - 1) not in " ", (part, "bottom-right")


FLOW = "graph LR\n  A[브라우저] --> B[Tollgate]\n  B --> C[(Redis)]"


@pytest.mark.parametrize("padding", [(4, 2, 4), (2, 1, 3), (1, 0, 2)])
def test_node_boxes_sit_on_the_drawn_corners_in_every_layout(padding):
    text = draw(FLOW, padding)
    parts = point.diagram_parts(FLOW, padding, text)
    assert [(p.kind, p.id, p.label) for p in parts] == [
        ("node", "A", "브라우저"), ("node", "B", "Tollgate"), ("node", "C", "Redis"),
    ]
    for part in parts:
        assert_on_corners(text, part)
    assert char_at(text, parts[1].boxes[0].x, parts[1].boxes[0].y) == "┌"


@pytest.mark.parametrize("direction", ["TD", "BT", "RL"])
def test_flipped_and_vertical_graphs_line_up(direction):
    src = f"graph {direction}\n  A[Start] --> B[Middle]\n  B --> C[End]"
    text = draw(src)
    parts = point.diagram_parts(src, (4, 2, 4), text)
    assert [p.label for p in parts] == ["Start", "Middle", "End"]
    for part in parts:
        assert_on_corners(text, part)
        row = text.plain.split("\n")[part.boxes[0].y + part.boxes[0].height // 2]
        # termaid 0.9.0 mirrors the labels too when it flips an RL graph.
        assert part.label in row or part.label[::-1] in row


def test_state_diagram_nodes_skip_the_start_and_end_dots():
    src = "stateDiagram-v2\n  [*] --> Active\n  Active --> Done: finish\n  Done --> [*]"
    text = draw(src)
    parts = point.diagram_parts(src, (4, 2, 4), text)
    assert [p.label for p in parts] == ["Active", "Done"]
    for part in parts:
        assert_on_corners(text, part)


SEQ = (
    "sequenceDiagram\n"
    "  participant B as Browser\n"
    "  participant T as Tollgate\n"
    "  B->>T: GET /authorize\n"
    "  Note over T: thinks\n"
    "  T-->>B: 302 /login\n"
)


def test_sequence_participants_and_messages():
    text = draw(SEQ)
    parts = point.diagram_parts(SEQ, (4, 2, 4), text)
    assert [(p.kind, p.label) for p in parts] == [
        ("participant", "Browser"), ("participant", "Tollgate"),
        ("message", "GET /authorize"), ("message", "302 /login"),
    ]
    for participant in parts[:2]:
        assert_on_corners(text, participant)
    rows = text.plain.split("\n")
    for message in parts[2:]:
        label_row, arrow_row = message.boxes[0].y, message.boxes[0].y + 1
        assert message.label in rows[label_row]
        assert any(ch in rows[arrow_row] for ch in "►◄")
    assert parts[2].detail == "from Browser to Tollgate"


def test_source_lines_of_nodes_participants_and_messages():
    flow = "graph TD\n  A[Start] --> B{Choice}\n  B -->|yes| C[Done]\n"
    parts = point.diagram_parts(flow, (4, 2, 4), draw(flow))
    assert [(p.id, p.lines) for p in parts] == [("A", (2, 2)), ("B", (2, 2)), ("C", (3, 3))]
    seq = point.diagram_parts(SEQ, (4, 2, 4), draw(SEQ))
    assert [p.lines for p in seq] == [(2, 2), (3, 3), (4, 4), (6, 6)]
    implicit = "sequenceDiagram\n  A->>B: hi\n  B-->>A: bye\n"
    parts = point.diagram_parts(implicit, (4, 2, 4), draw(implicit))
    assert [p.lines for p in parts] == [(2, 2), (2, 2), (2, 2), (3, 3)]


def test_diagrams_without_parts():
    pie = 'pie title Pets\n  "Dogs": 3\n  "Cats": 2'
    assert point.diagram_parts(pie, (4, 2, 4), draw(pie)) == []
    assert point.diagram_parts("not mermaid at all", (4, 2, 4), draw("graph LR\n  A --> B")) == []
    # Parts that don't line up with the drawing are not offered.
    assert point.diagram_parts(FLOW, (4, 2, 4), draw("graph LR\n  X --> Y")) == []
    whole = point.whole_diagram("graph LR\n  A --> B\n")
    assert (whole.kind, whole.lines, whole.boxes) == ("diagram", (1, 2), ())


def test_nearest_moves_by_direction():
    text = draw(FLOW)
    parts = point.diagram_parts(FLOW, (4, 2, 4), text)
    assert point.nearest(parts, 0, "right") == 1
    assert point.nearest(parts, 1, "right") == 2
    assert point.nearest(parts, 2, "left") == 1
    assert point.nearest(parts, 0, "left") == 0  # nothing that way: stay
    down = "graph TD\n  A[Top] --> B[Low]"
    vertical = point.diagram_parts(down, (4, 2, 4), draw(down))
    assert point.nearest(vertical, 0, "down") == 1 and point.nearest(vertical, 1, "up") == 0


def test_highlight_reverses_only_the_selected_box():
    text = draw(FLOW)
    parts = point.diagram_parts(FLOW, (4, 2, 4), text)
    lit = point.highlight(text, parts[1], Style(reverse=True))
    console = Console()
    box = parts[1].boxes[0]
    row = lit.split("\n")[box.y + 2]
    def style_at(x):
        col = 0
        for index, ch in enumerate(row.plain):
            if col == x:
                return row.get_style_at_offset(console, index)
            col += cell_len(ch)
    assert style_at(box.x).reverse and style_at(box.x + box.width - 1).reverse
    assert not style_at(parts[0].boxes[0].x).reverse
    assert not text.split("\n")[box.y + 2].get_style_at_offset(console, 20).reverse  # original untouched


def test_descriptions():
    text = draw(SEQ)
    seq = point.diagram_parts(SEQ, (4, 2, 4), text)
    assert point.describe(seq[0]) == 'the participant "Browser"'
    assert point.describe(seq[2]) == 'the message "GET /authorize" from Browser to Tollgate'
    flow = point.diagram_parts(FLOW, (4, 2, 4), draw(FLOW))
    assert point.describe(flow[1]) == 'the box "Tollgate"'
    assert point.describe(point.whole_diagram("graph LR\n  A --> B")) == "the whole diagram"


def test_a_quoted_line_with_backticks_stays_one_span():
    text = point.point_prompt("the diagram block", "x", lines=(7, 17), line_text="```mermaid", file_label="a.md")
    assert "lines 7-17: `` ```mermaid ``." in text


def test_parts_of_a_diagram_in_a_document_carry_their_place():
    block = point.Part("diagram", "block-6", "mermaid", (), (16, 19), "Steps")
    inner = point.diagram_parts(FLOW, (4, 2, 4), draw(FLOW))
    placed = point.fence_parts(block, inner)
    assert [p.id for p in placed] == ["block-6/A", "block-6/B", "block-6/C"]
    assert placed[1].lines == (18, 18)  # line 2 of the diagram; the fence opens on line 16
    assert placed[1].boxes == inner[1].boxes
    assert point.describe(placed[1]) == 'the box "Tollgate" in the diagram under "Steps"'
    unplaced = point.fence_parts(replace(block, detail=""), [replace(inner[0], lines=None)])[0]
    assert unplaced.lines == (16, 16) and point.describe(unplaced) == 'the box "브라우저" in the diagram'


LINKED = (
    "graph LR\n  A[POST /token] --> B[verifyPkce]\n"
    "  %% code: A src/routes/token.ts:42\n  %% code: B src/auth/pkce.ts:10\n"
)


def test_code_lines_tie_boxes_to_code():
    parts = point.diagram_parts(LINKED, (4, 2, 4), draw(LINKED))
    assert [(p.label, p.code) for p in parts] == [
        ("POST /token", "src/routes/token.ts:42"), ("verifyPkce", "src/auth/pkce.ts:10"),
    ]
    block = point.Part("diagram", "block-2", "mermaid", (), (5, 9), "Token")
    assert point.fence_parts(block, parts)[1].code == "src/auth/pkce.ts:10"


def test_a_part_tied_to_code_asks_for_a_code_change():
    asked = point.point_prompt(
        'the box "verifyPkce"', "401 for expired codes", lines=(3, 3), line_text="B[verifyPkce]",
        file_label="docs/token.mmd", code="src/auth/pkce.ts:10",
    )
    assert "It stands for `src/auth/pkce.ts:10`." in asked
    assert "Make this change in the code" in asked and "run the tests" in asked
    sent = point.point_prompt(
        'the box "verifyPkce"', "401", lines=(3, 3), line_text="", title="Token", source=LINKED,
        code="src/auth/pkce.ts:10",
    )
    assert "Make this change in the code" in sent and "send the updated diagram to Blueprint" in sent
    plain = point.point_prompt('the box "A"', "x", lines=None, line_text="", file_label="a.mmd")
    assert "Change it and anything it describes" in plain and "stands for" not in plain


# --- documents --------------------------------------------------------------------

from textual.app import App  # noqa: E402

from herdr_blueprint.document import DocumentView  # noqa: E402

DOC = """# Login flow

Authorization code with PKCE. The app never sees the password, and the code it gets is single-use.

## Steps

| Step | Code |
|---|---|
| authorize | src/routes/authorize.ts:20 |

- one
- two

> Careful here.

```mermaid
graph LR
  A --> B
```

```ts
const x = 1;
```

---
"""


async def blocks_of(markdown: str):
    class Host(App):
        def compose(self):
            yield DocumentView(markdown)

    app = Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(DocumentView)
        return [(b._token.type, b.source_range, getattr(b._token, "info", "")) for b in view.children
                if hasattr(b, "source_range")]


async def test_document_blocks_become_parts_with_their_lines():
    parts = point.document_parts(await blocks_of(DOC), DOC)
    assert [(p.kind, p.label, p.lines) for p in parts] == [
        ("heading", "Login flow", (1, 1)),
        ("paragraph", "Authorization code with PKCE. The app ne…", (3, 3)),
        ("heading", "Steps", (5, 5)),
        ("table", "Step | Code", (7, 9)),
        ("list", "one", (11, 12)),
        ("quote", "Careful here.", (14, 14)),
        ("diagram", "mermaid", (16, 19)),
        ("code", "ts", (21, 23)),
        ("rule", "", (25, 25)),
    ]


async def test_document_part_descriptions():
    parts = point.document_parts(await blocks_of(DOC), DOC)
    described = [point.describe(p) for p in parts]
    assert described[0] == 'the heading "Login flow"'
    assert described[1] == 'the paragraph "Authorization code with PKCE. The app ne…"'
    assert described[3] == 'the table under "Steps"'
    assert described[6] == "the diagram block under \"Steps\""
    assert described[8] == "the horizontal rule"


def test_parts_off_the_drawing_by_one_cell_are_refused():
    # A termaid layout change must fail loudly, not be nudged into place.
    text = draw(FLOW)
    shifted = Text("\n".join(" " + row for row in text.plain.split("\n")))
    assert point.diagram_parts(FLOW, (4, 2, 4), text) and point.diagram_parts(FLOW, (4, 2, 4), shifted) == []


def test_an_empty_graph_has_no_parts():
    assert point.diagram_parts("graph LR\n", (4, 2, 4), draw("graph LR\n")) == []


def test_a_graph_termaid_cannot_split_has_no_parts(monkeypatch):
    def unparseable(source):
        raise ValueError("unexpected token")

    monkeypatch.setattr("termaid.parser.flowchart.parse_flowchart", unparseable)
    assert point.diagram_parts(FLOW, (4, 2, 4), draw(FLOW)) == []
