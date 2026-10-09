"""Make the 1280x640 GitHub social preview: logo, name and tagline beside a
screenshot of Blueprint. Upload the result in Settings > General > Social preview.

Run: uv run python scripts/social_preview.py SHOT.png [OUT.png]

SHOT.png is a framed Blueprint pane, for example from
`scripts/demo/render.py hero.json out/pane --title Blueprint --crop 96,1,104,44`.
"""

from __future__ import annotations

import base64
import struct
import sys
from pathlib import Path

import resvg_py

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1280, 640
TAGLINE = ["Your coding agent explains", "its work as diagrams,", "right next to the chat."]


def png_size(path: Path) -> tuple[int, int]:
    return struct.unpack(">II", path.read_bytes()[16:24])


def data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def svg(shot: Path) -> str:
    shot_w, shot_h = png_size(shot)
    height = HEIGHT - 80
    width = round(shot_w * height / shot_h)
    x = WIDTH - width - 40
    grid = "".join(
        f'<path d="M{i} 0V{HEIGHT}" /><path d="M0 {i}H{WIDTH}" />' for i in range(0, WIDTH, 32)
    )
    lines = "".join(
        f'<text x="72" y="{372 + n * 44}" class="tag">{line}</text>' for n, line in enumerate(TAGLINE)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
  <style>
    .name {{ font: 600 76px 'Avenir Next'; fill: #e0def4; }}
    .tag {{ font: 500 32px 'Avenir Next'; fill: #c4a7e7; }}
    .foot {{ font: 500 20px 'Avenir Next'; fill: #6e6a86; letter-spacing: 1px; }}
  </style>
  <rect width="{WIDTH}" height="{HEIGHT}" fill="#191724" />
  <g stroke="#1f1d2e" stroke-width="1">{grid}</g>
  <image href="{data_uri(ROOT / 'docs' / 'logo.png')}" x="64" y="72" width="140" height="140" />
  <text x="68" y="300" class="name">Blueprint</text>
  {lines}
  <text x="72" y="584" class="foot">A herdr plugin  ·  Claude Code  ·  Codex</text>
  <clipPath id="round"><rect x="{x}" y="40" width="{width}" height="{height}" rx="14" /></clipPath>
  <image href="{data_uri(shot)}" x="{x}" y="40" width="{width}" height="{height}" clip-path="url(#round)" />
  <rect x="{x}" y="40" width="{width}" height="{height}" rx="14" fill="none" stroke="#403d52" stroke-width="2" />
</svg>"""


def main() -> None:
    shot = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "social-preview.png"
    out.write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=svg(shot))))
    print(out)


if __name__ == "__main__":
    main()
