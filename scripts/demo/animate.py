"""Turn recorded frames into a short GIF and MP4 of the moment the diagram appears.

Usage: python animate.py FRAMES_DIR ENTER_TIME OUT_STEM

FRAMES_DIR holds terminal.py frames (<milliseconds>.json). ENTER_TIME is the
Unix time the prompt was sent. The run is retimed: the typing plays at real
speed, the agent's work is squeezed into a few seconds, and the finished
screen holds at the end. Needs ffmpeg.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from png import write_png  # noqa: E402
from render import to_svg  # noqa: E402

FPS = 10
WORK_SECONDS = 4.0     # the agent reading code and writing, however long it took
SETTLE_SECONDS = 3.0   # drawing and the agent's closing words
HOLD_SECONDS = 4.0     # the finished screen
MAX_TYPING_SECONDS = 6.0
BLUEPRINT_COLUMN = 96  # where the Blueprint pane starts in the demo layout


def header(rows: list[list[list]]) -> str:
    return "".join(cell[0] for cell in rows[2][BLUEPRINT_COLUMN:])


def retime(times: list[float], enter: float, arrive: float) -> list[float]:
    """Output time of each frame."""
    start, end = times[0], times[-1]
    typing = enter - start
    typing_scale = min(1.0, MAX_TYPING_SECONDS / typing) if typing > 0 else 1.0
    work_scale = WORK_SECONDS / max(arrive - enter, 1e-6)
    settle_scale = min(1.0, SETTLE_SECONDS / max(end - arrive, 1e-6))
    out = []
    for t in times:
        if t <= enter:
            out.append((t - start) * typing_scale)
        elif t <= arrive:
            out.append(typing * typing_scale + (t - enter) * work_scale)
        else:
            out.append(typing * typing_scale + WORK_SECONDS + (t - arrive) * settle_scale)
    return out


def main() -> None:
    frames_dir, enter, out = Path(sys.argv[1]), float(sys.argv[2]), Path(sys.argv[3])
    paths = sorted(frames_dir.glob("*.json"), key=lambda p: int(p.stem))
    times = [int(p.stem) / 1000 for p in paths]
    arrive = next(
        t for t, p in zip(times, paths)
        if t > enter and "Start" not in header(json.loads(p.read_text()))
    )
    stamps = retime(times, enter, arrive)

    # One frame per tick: the newest frame at that moment.
    ticks = int((stamps[-1] + HOLD_SECONDS) * FPS)
    chosen, i = [], 0
    for tick in range(ticks):
        while i + 1 < len(stamps) and stamps[i + 1] <= tick / FPS:
            i += 1
        if chosen and chosen[-1][0] == i:
            chosen[-1][1] += 1 / FPS
        else:
            chosen.append([i, 1 / FPS])

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        lines = []
        for n, (index, duration) in enumerate(chosen):
            png = folder / f"{n:04d}.png"
            write_png(to_svg(json.loads(paths[index].read_text())), png, zoom=1)
            lines += [f"file '{png}'", f"duration {duration:.3f}"]
        lines.append(f"file '{folder / f'{len(chosen) - 1:04d}.png'}'")  # concat needs the last one twice
        (folder / "list.txt").write_text("\n".join(lines))
        concat = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(folder / "list.txt")]
        mp4 = out.with_suffix(".mp4")
        subprocess.run(concat + [
            "-vf", "fps=30,scale=1920:-2:flags=lanczos,format=yuv420p",
            "-c:v", "libx264", "-crf", "20", "-movflags", "+faststart", str(mp4),
        ], check=True)
    # The GIF comes from the MP4: its slightly softened frames palettize into a
    # file about 30 times smaller than one made from the PNGs directly.
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4),
        "-vf", "fps=10,scale=1400:-1:flags=lanczos,split[a][b];"
               "[a]palettegen=stats_mode=diff:max_colors=64[p];[b][p]paletteuse=dither=none:diff_mode=rectangle",
        "-loop", "0", str(out.with_suffix(".gif")),
    ], check=True)
    print(f"{len(chosen)} frames, {stamps[-1] + HOLD_SECONDS:.1f}s -> {out.with_suffix('.gif')}, {out.with_suffix('.mp4')}")


if __name__ == "__main__":
    main()
