import pytest
from rich.text import Text

from herdr_blueprint.diagram import DiagramError, render_diagram, width_of
from herdr_blueprint.themes import get_palette

PALETTE = get_palette("rose-pine")
WIDE = "graph LR\n  A[Agent writes] --> B[Blueprint follows] --> C[You read it here]"


def test_draws_with_palette_colors():
    text = render_diagram("graph LR\n  A --> B", PALETTE)
    styles = {str(span.style) for span in text.spans}
    assert PALETTE.node in styles


def test_compacts_to_fit_width():
    roomy = render_diagram(WIDE, PALETTE)
    fitted = render_diagram(WIDE, PALETTE, max_width=width_of(roomy) - 1)
    assert width_of(fitted) < width_of(roomy)


def test_wide_characters_count_as_two_columns():
    assert width_of(Text("주문\nab")) == 4


def test_empty_output_is_an_error():
    with pytest.raises(DiagramError):
        render_diagram("   ", PALETTE)


def test_large_diagrams_are_refused_with_a_message():
    from herdr_blueprint.diagram import MAX_DIAGRAM_CHARS

    source = "graph TD\n" + "".join(f"  N{i} --> N{i + 1}\n" for i in range(MAX_DIAGRAM_CHARS // 10))
    with pytest.raises(DiagramError, match="too large"):
        render_diagram(source, PALETTE)


def test_repeated_draws_are_cached():
    first = render_diagram("graph LR\n  Cache --> Hit", PALETTE, max_width=60)
    second = render_diagram("graph LR\n  Cache --> Hit", PALETTE, max_width=60)
    assert first is second


def test_wide_characters_do_not_shift_styles():
    # termaid adds an empty cell after each wide (CJK) character; styles must stay on their letters.
    source = (
        "sequenceDiagram\n"
        "  participant C as cli.py\n  participant I as 수신함 폴더\n  participant A as app.py\n"
        "  C->>I: JSON 저장 (임시파일 후 rename)\n"
        "  I-->>A: watchfiles 감지, rename으로 가져감\n"
        "  A->>A: 화면 교체, 이전 것 버림\n"
    )
    text = render_diagram(source, PALETTE)
    plain = text.plain
    styled = [False] * len(plain)
    for span in text.spans:
        for i in range(span.start, span.end):
            styled[i] = True
    unstyled = "".join(ch for ch, has in zip(plain, styled) if ch.strip() and not has)
    assert unstyled == ""


def test_wide_characters_in_flowcharts_keep_their_styles():
    text = render_diagram("graph LR\n  A[주문 요청] -->|저장| B[(오라클 DB)]", PALETTE)
    plain = text.plain
    styled = [False] * len(plain)
    for span in text.spans:
        for i in range(span.start, span.end):
            styled[i] = True
    assert "".join(ch for ch, has in zip(plain, styled) if ch.strip() and not has) == ""
