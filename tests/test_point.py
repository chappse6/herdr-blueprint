"""Parts of what Blueprint shows: boxes in termaid's drawing, lines in the source."""

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.style import Style
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
