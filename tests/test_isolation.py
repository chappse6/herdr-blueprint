"""Tests must never reach the developer's own herdr session (a test once sent prompts
to a real agent): every herdr call goes to a program and socket that don't exist."""

import os
from pathlib import Path


def test_herdr_is_out_of_reach(tmp_path):
    program = Path(os.environ["HERDR_BIN_PATH"])
    socket = Path(os.environ["HERDR_SOCKET_PATH"])
    assert program.is_relative_to(tmp_path) and not program.exists()
    assert socket.is_relative_to(tmp_path) and not socket.exists()
    assert "HERDR_PANE_ID" not in os.environ
