from herdr_blueprint.diagram import render_diagram
from herdr_blueprint.themes import DEFAULT_PALETTE, PALETTES, get_palette, palette_for_theme


def test_every_palette_makes_a_textual_theme():
    for palette in PALETTES.values():
        theme = palette.textual_theme()
        assert theme.name == f"bp-{palette.name}"
        assert theme.background == palette.background


def test_every_palette_draws_a_diagram():
    for palette in PALETTES.values():
        assert "A" in render_diagram("graph LR\n  A --> B", palette).plain


def test_unknown_names_fall_back_to_default():
    assert get_palette("nope").name == DEFAULT_PALETTE
    assert get_palette(None).name == DEFAULT_PALETTE


def test_palette_for_theme_strips_prefix():
    assert palette_for_theme("bp-nord").name == "nord"
