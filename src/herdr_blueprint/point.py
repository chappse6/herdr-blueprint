"""The parts of what Blueprint shows, for pointing at one: boxes in the drawing
and lines in the source. No Textual in here.

Diagram parts come from termaid's own layout, so they match the drawing. That
layout is termaid internals (the version is pinned); every box is also checked
against the drawn text, so a layout that doesn't line up is never offered.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text

# `%% code: <id> <where>`: a Mermaid comment tying a box to the code it stands for.
_CODE_LINE = re.compile(r"^[ \t]*%%[ \t]*code:[ \t]*(\S+)[ \t]+(\S.*?)[ \t]*$", re.MULTILINE)
# Message arrows in a sequence diagram source line.
_ARROW = re.compile(r"^\s*[^%].*?(-{1,2}>>|-{1,2}>|-{1,2}x|-{1,2}\))")


@dataclass(frozen=True)
class Box:
    """Cells in the drawing: columns x.. and rows y.. (wide characters take two columns)."""

    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(frozen=True)
class Part:
    kind: str  # node, participant, message, diagram, or a document block kind
    id: str
    label: str
    boxes: tuple[Box, ...] = ()
    lines: tuple[int, int] | None = None  # 1-based, inclusive
    detail: str = ""
    where: str = ""  # the diagram it sits in, inside a document
    code: str = ""  # the code it stands for, from a `%% code:` line
    anchor: tuple[float, float] | None = None  # where moving measures from, if not the first box
    joined: frozenset[str] = frozenset()  # ids an arrow joins it to (a box's neighbours, a message's participants)


# --- diagrams ----------------------------------------------------------------------


def code_links(source: str) -> dict[str, str]:
    """Box id -> the code it stands for, from the diagram's `%% code:` lines."""
    return {found.group(1): found.group(2) for found in _CODE_LINE.finditer(source)}


def _strip(source: str) -> str:
    from termaid import _strip_frontmatter

    return _strip_frontmatter(source.strip())


def _kind(text: str) -> str:
    for line in text.splitlines():
        words = line.split()
        if words and not words[0].startswith("%%"):
            return words[0]
    return ""


def diagram_parts(source: str, padding: tuple[int, int, int], text: Text) -> list[Part]:
    """Nodes, or participants and messages, as drawn in `text` with `padding`.

    Empty for other diagrams, for sources termaid can't parse, and when the
    parts don't line up with the drawing.
    """
    stripped = _strip(source)
    kind = _kind(stripped)
    try:
        if kind in ("graph", "flowchart"):
            from termaid.parser.flowchart import parse_flowchart

            parts = _graph_parts(parse_flowchart(stripped), padding, source)
        elif kind.startswith("stateDiagram"):
            from termaid.parser.statediagram import parse_state_diagram

            parts = _graph_parts(parse_state_diagram(stripped), padding, source)
        elif kind == "sequenceDiagram":
            parts = _sequence_parts(stripped, padding, source)
        else:
            return []
    except Exception:
        return []  # termaid could draw it, but not split it into parts
    links = code_links(source)

    def code_of(part: Part) -> str:
        # A branch without its own line stands for the code at its branch point.
        return links.get(part.id) or (links.get(part.id.split("->")[0], "") if part.kind == "branch" else "")

    return [replace(part, code=code_of(part)) if code_of(part) else part for part in _with_labels(_aligned(parts, text), text)]


def whole_diagram(source: str) -> Part:
    return Part("diagram", "", "", (), (1, max(1, len(source.rstrip("\n").split("\n")))))


def _graph_parts(graph, padding: tuple[int, int, int], source: str) -> list[Part]:
    from termaid.layout.grid import compute_layout
    from termaid.routing.router import route_edges

    if not graph.node_order:
        return []
    # termaid lays BT and RL out as TB and LR, then flips the canvas.
    direction = graph.direction
    flip_v, flip_h = direction.name == "BT", direction.name == "RL"
    if flip_v:
        graph.direction = type(direction).TB
    elif flip_h:
        graph.direction = type(direction).LR
    padding_x, padding_y, gap = padding
    layout = compute_layout(graph, padding_x, padding_y, gap)
    width, height = layout.canvas_width + 4, layout.canvas_height + 4
    routes = route_edges(graph, layout)
    for routed in routes:
        for x, y in routed.draw_path:
            width, height = max(width, x + 2), max(height, y + 2)

    def flipped(x: int, y: int) -> tuple[int, int]:
        return (width - 1 - x if flip_h else x), (height - 1 - y if flip_v else y)

    lines = source.split("\n")
    neighbours: dict[str, set[str]] = {}
    for routed in routes:
        neighbours.setdefault(routed.edge.source, set()).add(routed.edge.target)
        neighbours.setdefault(routed.edge.target, set()).add(routed.edge.source)
    parts = []
    for node_id in graph.node_order:
        if node_id.startswith("[*]"):
            continue  # a state diagram's start or end dot
        placement = layout.placements[node_id]
        box = Box(placement.draw_x, placement.draw_y, placement.draw_width, placement.draw_height)
        if flip_h:
            box = replace(box, x=width - box.x - box.width)
        if flip_v:
            box = replace(box, y=height - box.y - box.height)
        label = getattr(graph.nodes[node_id], "label", "") or node_id
        line = _node_line(lines, node_id)
        parts.append(Part(
            "node", node_id, label, (box,), (line, line) if line else None,
            joined=frozenset(neighbours.get(node_id, ())),
        ))
    # Branches: labelled arrows, and every arrow out of a node with more than one.
    names = {p.id: p.label for p in parts}
    outgoing = Counter(routed.edge.source for routed in routes)
    seen: Counter = Counter()
    for routed in routes:
        source_id, target_id = routed.edge.source, routed.edge.target
        label = (routed.edge.label or "").strip()
        if not (label or outgoing[source_id] > 1) or source_id not in names or target_id not in names:
            continue
        seen[(source_id, target_id)] += 1
        part_id = f"{source_id}->{target_id}" + (f"#{seen[(source_id, target_id)]}" if seen[(source_id, target_id)] > 1 else "")
        line = _edge_line(lines, source_id, target_id)
        detail = f'from "{names[source_id]}" to "{names[target_id]}"'
        path = _segments([flipped(x, y) for x, y in routed.draw_path])
        # Measured from its last run into the target: the only stretch no other branch shares,
        # and a label can sit off to the side of the arrow.
        own = path[-1].center if path else None
        parts.append(Part("branch", part_id, label, tuple(path), (line, line) if line else None, detail, anchor=own))
    return parts


def _segments(points: list[tuple[int, int]]) -> list[Box]:
    """An arrow's path as boxes, one per straight run, without the cells on the boxes it joins."""
    if len(points) < 2:
        return []

    def toward(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
        return a[0] + (b[0] > a[0]) - (b[0] < a[0]), a[1] + (b[1] > a[1]) - (b[1] < a[1])

    points = [toward(points[0], points[1]), *points[1:-1], toward(points[-1], points[-2])]
    return [
        Box(min(x1, x2), min(y1, y2), abs(x2 - x1) + 1, abs(y2 - y1) + 1)
        for (x1, y1), (x2, y2) in zip(points, points[1:])
    ]


def _with_labels(parts: list[Part], text: Text) -> list[Part]:
    """Branches with the drawn label in front: it is what the selection starts from.

    Every place a label is drawn is a candidate; whole words beat parts of longer
    text, then the nearest to the branch's path wins, and no place goes to two
    branches ("no" and "on" mirrored, say).
    """
    rows = text.plain.split("\n")
    nodes = [box for part in parts if part.kind == "node" for box in part.boxes]
    choices = []  # (partial, distance, part index, box)
    for index, part in enumerate(parts):
        if part.kind != "branch" or not part.label or not part.boxes:
            continue
        path = [(x, y) for box in part.boxes for y in range(box.y, box.y + box.height) for x in range(box.x, box.x + box.width)]
        for y, row in enumerate(rows):
            for candidate in {part.label, part.label[::-1]}:  # termaid 0.9.0 mirrors RL labels
                start = row.find(candidate)
                while start != -1:
                    end = start + len(candidate)
                    box = Box(cell_len(row[:start]), y, cell_len(candidate), 1)
                    if not any(node.contains(box.x, box.y) for node in nodes):
                        partial = (start > 0 and row[start - 1].isalnum()) or (end < len(row) and row[end].isalnum())
                        distance = min(max(box.x - x, 0, x - (box.x + box.width - 1)) + abs(box.y - y) for x, y in path)
                        choices.append((partial, distance, index, box))
                    start = row.find(candidate, start + 1)
    placed, taken = list(parts), set()
    for _partial, _distance, index, box in sorted(choices, key=lambda c: (c[0], c[1], c[2])):
        if box in taken or placed[index] is not parts[index]:
            continue
        taken.add(box)
        placed[index] = replace(parts[index], boxes=(box, *parts[index].boxes))
    return placed


def _sequence_parts(stripped: str, padding: tuple[int, int, int], source: str) -> list[Part]:
    from termaid.parser.sequence import parse_sequence_diagram
    from termaid.renderer import sequence as seq

    diagram = parse_sequence_diagram(stripped)
    flat = seq._flatten_events(diagram.events)
    padding_x, _padding_y, gap = padding
    # render_rich passes gap to the sequence renderer only when it isn't 4; its own default is 16.
    centers, widths, _w, _h, header, rows, _blocks = seq._compute_layout(
        diagram, bool(getattr(diagram, "autonumber", False)), flat, padding_x, 16 if gap == 4 else gap,
    )
    lines = source.split("\n")
    index = {p.id: i for i, p in enumerate(diagram.participants)}
    label_of = {p.id: p.label or p.id for p in diagram.participants}
    parts = []
    for i, participant in enumerate(diagram.participants):
        box = Box(centers[i] - widths[i] // 2, 0, widths[i], header)
        line = _participant_line(lines, participant.id)
        parts.append(Part("participant", participant.id, label_of[participant.id], (box,), (line, line) if line else None))
    message_lines = [n for n, text in enumerate(lines, 1) if _ARROW.match(text)]
    count = 0
    for event, row in zip(flat, rows):
        if type(event).__name__ != "Message":
            continue
        left, right = sorted((centers[index[event.source]], centers[index[event.target]]))
        box = Box(left, row - 1, max(right - left, 6) + 1, 2)
        line = message_lines[count] if count < len(message_lines) else None
        count += 1
        detail = f"from {label_of[event.source]} to {label_of[event.target]}"
        parts.append(Part(
            "message", f"message-{count}", event.label, (box,), (line, line) if line else None, detail,
            joined=frozenset((event.source, event.target)),
        ))
    return parts


def _node_line(lines: list[str], node_id: str) -> int | None:
    word = re.escape(node_id)
    patterns = (
        rf"(^|[\s;&>|-]){word}\s*(\[|\(|\{{|>)",  # A[Label], A((x)), A{{x}}, A>x]
        rf"^\s*state\s+{word}\b|^\s*{word}\s*:",  # state diagrams
        rf"(^|\W){word}(\W|$)",  # used anywhere
    )
    for pattern in patterns:
        for number, text in enumerate(lines[1:], 2):
            if re.search(pattern, text):
                return number
    return None


def _edge_line(lines: list[str], source_id: str, target_id: str) -> int | None:
    """The first line with an arrow from `source_id` to `target_id` (also inside a chain)."""
    pattern = re.compile(rf"(^|\W){re.escape(source_id)}\b.*?(--|==|-\.).*?(^|\W){re.escape(target_id)}(\W|$)")
    for number, text in enumerate(lines[1:], 2):
        if not text.lstrip().startswith("%%") and pattern.search(text):
            return number
    return None


def _participant_line(lines: list[str], participant_id: str) -> int | None:
    word = re.escape(participant_id)
    for pattern in (rf"^\s*(participant|actor)\s+{word}\b", rf"(^|\W){word}(\W|$)"):
        for number, text in enumerate(lines[1:], 2):
            if re.search(pattern, text):
                return number
    return None


def _char_at(rows: list[str], x: int, y: int) -> str:
    if not 0 <= y < len(rows) or x < 0:
        return " "
    column = 0
    for ch in rows[y]:
        if column == x:
            return ch
        column += cell_len(ch)
        if column > x:
            return ch  # inside a wide character
    return " "


def _aligned(parts: list[Part], text: Text) -> list[Part]:
    """`parts`, or [] if their boxes don't sit on the drawing's corners.

    No nudging: a termaid layout change must show up as no parts (and failing
    tests), not as boxes moved onto whatever is nearby.
    """
    rows = text.plain.split("\n")
    boxed = [box for part in parts if part.kind in ("node", "participant") for box in part.boxes]
    fits = all(
        _char_at(rows, b.x, b.y) != " " and _char_at(rows, b.x + b.width - 1, b.y + b.height - 1) != " "
        for b in boxed
    )
    return parts if fits else []


# --- documents ------------------------------------------------------------------------

_BLOCK_KINDS = {
    "heading_open": "heading", "paragraph_open": "paragraph", "bullet_list_open": "list",
    "ordered_list_open": "list", "table_open": "table", "blockquote_open": "quote",
    "fence": "code", "code_block": "code", "hr": "rule",
}
_MARKERS = re.compile(r"^\s*(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)+")


def _cut(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def document_parts(blocks: list[tuple[str, tuple[int, int], str]], source: str) -> list[Part]:
    """Top-level Markdown blocks (token type, 0-based [start, end) lines, fence info) as parts."""
    lines = source.split("\n")
    parts, heading = [], ""
    for number, (token_type, (start, end), info) in enumerate(blocks):
        kind = _BLOCK_KINDS.get(token_type, "block")
        if kind == "code" and info.strip().split()[:1] == ["mermaid"]:
            kind = "diagram"
        while end > start + 1 and end <= len(lines) and not lines[end - 1].strip():
            end -= 1  # the parser counts a trailing blank line into lists
        body = [line for line in lines[start:end] if line.strip()]
        if kind in ("code", "diagram"):
            label = info.strip().split()[0] if info.strip() else _cut(body[1].strip() if len(body) > 1 else "")
        elif kind == "table" and body:
            label = _cut(" | ".join(cell.strip() for cell in body[0].strip().strip("|").split("|")))
        elif kind == "rule":
            label = ""
        else:
            label = _cut(_MARKERS.sub("", body[0]).strip() if body else "")
        parts.append(Part(kind, f"block-{number}", label, (), (start + 1, max(start + 1, end)), heading))
        if kind == "heading":
            heading = label
    return parts


def fence_parts(block: Part, parts: list[Part]) -> list[Part]:
    """The parts of a diagram drawn in a document's Mermaid block, placed in that document."""
    where = f'the diagram under "{block.detail}"' if block.detail else "the diagram"
    start = block.lines[0] if block.lines else 0  # the fence line; the diagram's line 1 is the next one
    return [
        replace(
            part, id=f"{block.id}/{part.id}", where=where,
            lines=(part.lines[0] + start, part.lines[1] + start) if part.lines else (start, start) if block.lines else None,
        )
        for part in parts
    ]


# --- moving and showing --------------------------------------------------------------


def anchor(part: Part) -> tuple[float, float]:
    """Where moving the selection measures from: a branch's path, else the first box."""
    return part.anchor if part.anchor is not None else part.boxes[0].center


def _ends(part_id: str) -> set[str]:
    """A branch id's two boxes (`P->S`, also inside a document: `block-3/P->S#2`), or the box itself."""
    base = part_id.rsplit("/", 1)[-1].split("#")[0]
    return set(base.split("->"))


def _link(a: Part, b: Part) -> str | None:
    """How an arrow joins two parts: "arrow" for neighbouring boxes, a box and its own
    branch, a message and its participants; "sibling" for two branches of one box."""
    ends_a, ends_b = _ends(a.id), _ends(b.id)
    if a.kind == "branch" and b.kind == "branch":
        return "sibling" if ends_a & ends_b else None
    if "branch" in (a.kind, b.kind):
        return "arrow" if ends_a & ends_b else None  # only its own two boxes
    return "arrow" if ends_a & b.joined or ends_b & a.joined else None


def _span(part: Part, sideways: bool) -> tuple[int, int]:
    """The rows (moving sideways) or columns (moving up or down) a part takes. A branch
    takes its label and its last run, not the trunk it may share."""
    boxes = [part.boxes[-1], part.boxes[0]] if part.kind == "branch" and part.label else [part.boxes[-1]]
    if sideways:
        return min(b.y for b in boxes), max(b.y + b.height - 1 for b in boxes)
    return min(b.x for b in boxes), max(b.x + b.width - 1 for b in boxes)


def nearest(parts: list[Part], current: int, direction: str) -> int:
    """The part closest to `current` in `direction` (left, right, up, down), or `current`.

    Moving sideways only reaches parts on the same rows, up or down only parts in the
    same columns, so nothing that way means no move. Parts an arrow joins to `current`
    count wherever they are (sibling branches within 45 degrees), and being off to the
    side costs them less.
    """
    if not parts or not parts[current].boxes:
        return current
    cx, cy = anchor(parts[current])
    sideways = direction in ("left", "right")
    low, high = _span(parts[current], sideways)
    best, best_score = current, float("inf")
    for i, part in enumerate(parts):
        if i == current or not part.boxes:
            continue
        x, y = anchor(part)
        dx, dy = x - cx, y - cy
        ahead, aside = {"right": (dx, dy), "left": (-dx, dy), "down": (dy, dx), "up": (-dy, dx)}[direction]
        link = _link(parts[current], part)
        start, end = _span(part, sideways)
        if ahead <= 0.5 or not (link or (start <= high and low <= end)):
            continue
        if link == "sibling" and abs(aside) > ahead:
            continue  # branches of one box sit side by side: reached by moving across
        # Off to the side costs less along an arrow: it may bend, a neighbour on the row doesn't.
        score = ahead + (0.5 if link else 2) * abs(aside)
        if score < best_score:
            best, best_score = i, score
    return best


def highlight(text: Text, part: Part, style: Style) -> Text:
    """A copy of `text` with `style` over the part's boxes."""
    lines = text.split("\n")
    for box in part.boxes:
        for y in range(box.y, box.y + box.height):
            if not 0 <= y < len(lines):
                continue
            line, column, start, end = lines[y], 0, None, None
            for index, ch in enumerate(line.plain):
                if start is None and column + cell_len(ch) > box.x:
                    start = index
                if column >= box.x + box.width:
                    end = index
                    break
                column += cell_len(ch)
            if start is not None:
                line.stylize(style, start, end if end is not None else len(line.plain))
    return Text("\n").join(lines)


def describe(part: Part) -> str:
    if part.where:
        return f"{describe(replace(part, where=''))} in {part.where}"
    if part.kind == "node":
        return f'the box "{part.label}"'
    if part.kind == "participant":
        return f'the participant "{part.label}"'
    if part.kind == "branch":
        return f'the branch "{part.label}" {part.detail}' if part.label else f"the arrow {part.detail}"
    if part.kind == "message":
        return f'the message "{part.label}" {part.detail}'.rstrip()
    if part.kind == "diagram":
        if part.id.startswith("block-"):
            return f'the diagram block under "{part.detail}"' if part.detail else "the diagram block"
        return "the whole diagram"
    if part.kind == "rule":
        return "the horizontal rule"
    if part.kind == "table" and part.detail:
        return f'the table under "{part.detail}"'
    if part.kind == "code":
        return f'the {part.label} code block' if part.label else "the code block"
    return f'the {part.kind} "{part.label}"'


# --- the request -----------------------------------------------------------------------


def _where(lines: tuple[int, int] | None, line_text: str) -> str:
    if not lines:
        return ""
    span = f"line {lines[0]}" if lines[0] == lines[1] else f"lines {lines[0]}-{lines[1]}"
    text = line_text.strip()
    if not text:
        return f", {span}"
    return f", {span}: `` {text} ``" if "`" in text else f", {span}: `{text}`"


def point_prompt(
    described: str,
    note: str,
    *,
    lines: tuple[int, int] | None,
    line_text: str,
    file_label: str | None = None,
    title: str = "",
    diagram: bool = True,
    source: str | None = None,
    attachment=None,
    code: str = "",
) -> str:
    """What Enter sends: the part, where it is, the person's note, and what to do.

    A part tied to code (`%% code:`) is a work order: change that code, test,
    and redraw. Anything else asks to change the part and what it describes.
    """
    from .edit import fenced

    where = _where(lines, line_text)
    stands = f" It stands for `{code}`." if code else ""
    work = "Make this change in the code there and anything that depends on it, run the tests, then "
    if file_label is not None:
        todo = f"{work}update the diagram to match and show it in Blueprint." if code else (
            "Change it and anything it describes, then show the result in Blueprint."
        )
        return f"In Blueprint I pointed at {described} in `{file_label}`{where}.{stands}\n{note}\n{todo}\n"
    what = "diagram" if diagram else "note"
    body = f"Its source is in `{attachment}`." if attachment else fenced(source or "", "mermaid" if diagram else "markdown")
    todo = f"{work}send the updated {what} to Blueprint." if code else (
        "Change it and anything it describes, then send the new version to Blueprint."
    )
    return f"In Blueprint I pointed at {described} in the {what} you sent ('{title}'){where}.{stands}\n{note}\n{todo}\n{body}\n"
