"""Color palettes. Each palette styles both the Textual UI and termaid diagrams."""

from __future__ import annotations

from dataclasses import dataclass

from textual.theme import Theme


@dataclass(frozen=True)
class Palette:
    name: str
    label: str
    background: str
    surface: str
    panel: str
    foreground: str
    muted: str
    primary: str
    secondary: str
    accent: str
    warning: str
    error: str
    success: str
    # Diagram roles
    node: str
    edge: str
    arrow: str
    edge_label: str
    dark: bool = True

    @property
    def theme_name(self) -> str:
        """Name used for both the Textual and the termaid theme."""
        return f"bp-{self.name}"

    def textual_theme(self) -> Theme:
        return Theme(
            name=self.theme_name,
            primary=self.primary,
            secondary=self.secondary,
            accent=self.accent,
            warning=self.warning,
            error=self.error,
            success=self.success,
            foreground=self.foreground,
            background=self.background,
            surface=self.surface,
            panel=self.panel,
            dark=self.dark,
            variables={"text-muted": self.muted},
        )


PALETTES: dict[str, Palette] = {
    p.name: p
    for p in [
        Palette(
            name="rose-pine", label="Rosé Pine",
            background="#191724", surface="#1f1d2e", panel="#26233a",
            foreground="#e0def4", muted="#6e6a86",
            primary="#c4a7e7", secondary="#9ccfd8", accent="#ebbcba",
            warning="#f6c177", error="#eb6f92", success="#31748f",
            node="bold #ebbcba", edge="#6e6a86", arrow="bold #f6c177", edge_label="#908caa",
        ),
        Palette(
            name="blueprint", label="Blueprint",
            background="#0b2a4a", surface="#0f3460", panel="#123a6b",
            foreground="#e8f1ff", muted="#7fa3c9",
            primary="#7cc4ff", secondary="#4fa3e0", accent="#ffffff",
            warning="#ffd166", error="#ff6b6b", success="#7ee0b5",
            node="bold #ffffff", edge="#7fa3c9", arrow="bold #7cc4ff", edge_label="#b8d4f0",
        ),
        Palette(
            name="catppuccin-mocha", label="Catppuccin Mocha",
            background="#1e1e2e", surface="#181825", panel="#313244",
            foreground="#cdd6f4", muted="#7f849c",
            primary="#cba6f7", secondary="#89b4fa", accent="#f5c2e7",
            warning="#f9e2af", error="#f38ba8", success="#a6e3a1",
            node="bold #f5c2e7", edge="#7f849c", arrow="bold #fab387", edge_label="#a6adc8",
        ),
        Palette(
            name="tokyo-night", label="Tokyo Night",
            background="#1a1b26", surface="#24283b", panel="#292e42",
            foreground="#c0caf5", muted="#565f89",
            primary="#7aa2f7", secondary="#7dcfff", accent="#bb9af7",
            warning="#e0af68", error="#f7768e", success="#9ece6a",
            node="bold #7dcfff", edge="#565f89", arrow="bold #ff9e64", edge_label="#a9b1d6",
        ),
        Palette(
            name="nord", label="Nord",
            background="#2e3440", surface="#3b4252", panel="#434c5e",
            foreground="#eceff4", muted="#616e88",
            primary="#88c0d0", secondary="#81a1c1", accent="#b48ead",
            warning="#ebcb8b", error="#bf616a", success="#a3be8c",
            node="bold #88c0d0", edge="#616e88", arrow="bold #ebcb8b", edge_label="#d8dee9",
        ),
        Palette(
            name="gruvbox", label="Gruvbox",
            background="#282828", surface="#3c3836", panel="#504945",
            foreground="#ebdbb2", muted="#928374",
            primary="#fabd2f", secondary="#83a598", accent="#fe8019",
            warning="#fabd2f", error="#fb4934", success="#b8bb26",
            node="bold #fe8019", edge="#928374", arrow="bold #fabd2f", edge_label="#bdae93",
        ),
        Palette(
            name="dracula", label="Dracula",
            background="#282a36", surface="#343746", panel="#44475a",
            foreground="#f8f8f2", muted="#6272a4",
            primary="#bd93f9", secondary="#8be9fd", accent="#ff79c6",
            warning="#f1fa8c", error="#ff5555", success="#50fa7b",
            node="bold #ff79c6", edge="#6272a4", arrow="bold #8be9fd", edge_label="#bfbfbf",
        ),
    ]
}

DEFAULT_PALETTE = "rose-pine"


def get_palette(name: str | None) -> Palette:
    """Palette by name, or the default one for unknown names."""
    return PALETTES.get(name or "", PALETTES[DEFAULT_PALETTE])


def palette_for_theme(theme_name: str) -> Palette:
    """Palette behind a registered Textual theme name such as `bp-nord`."""
    return get_palette(theme_name.removeprefix("bp-"))
