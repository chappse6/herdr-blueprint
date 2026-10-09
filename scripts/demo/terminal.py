"""Run a command in a virtual terminal and keep a snapshot of its screen.

Usage: python terminal.py OUT_DIR COLS ROWS -- command...

Every half second OUT_DIR/screen.json (cells with colors) and screen.txt are
rewritten. While OUT_DIR/record exists, every change is also kept as
OUT_DIR/frames/<milliseconds>.json, for animations. Create OUT_DIR/stop to end.
Used to capture herdr for the README.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import select
import struct
import sys
import termios
import time
from pathlib import Path

import pyte


# Keyboard protocol switches (CSI > 1 u, CSI < u, ...): pyte doesn't know them
# and would print their tail as text.
KEYBOARD_MODES = re.compile(rb"\x1b\[[<>=?][0-9;]*u")


class Screen(pyte.Screen):
    # herdr asks for private device status (CSI ? 6 n); pyte only knows the plain one.
    def report_device_status(self, *args, **kwargs):
        if not kwargs.get("private"):
            super().report_device_status(*args)


def snapshot(screen: pyte.Screen) -> list[list[list]]:
    rows = []
    for y in range(screen.lines):
        line = screen.buffer[y]
        rows.append([
            [c.data, c.fg, c.bg, c.bold, c.italics, c.underscore, c.reverse]
            for c in (line[x] for x in range(screen.columns))
        ])
    return rows


def main() -> None:
    out, cols, rows = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    command = sys.argv[sys.argv.index("--") + 1:]
    out.mkdir(parents=True, exist_ok=True)
    (out / "stop").unlink(missing_ok=True)

    screen = Screen(cols, rows)
    stream = pyte.ByteStream(screen)
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(command[0], command)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    screen.write_process_input = lambda data: os.write(fd, data.encode())

    frames = out / "frames"
    last = last_frame = 0.0
    while not (out / "stop").exists():
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            try:
                stream.feed(KEYBOARD_MODES.sub(b"", os.read(fd, 65536)))
            except OSError:
                break
        now = time.time()
        # Up to ten frames a second, only when something on screen changed.
        if screen.dirty and now - last_frame >= 0.1 and (out / "record").exists():
            frames.mkdir(exist_ok=True)
            (frames / f"{int(now * 1000)}.json").write_text(json.dumps(snapshot(screen)))
            screen.dirty.clear()
            last_frame = now
        if now - last > 0.5:
            (out / "screen.json").write_text(json.dumps(snapshot(screen)))
            (out / "screen.txt").write_text("\n".join(line.rstrip() for line in screen.display))
            last = now
    os.kill(pid, 15)


if __name__ == "__main__":
    main()
