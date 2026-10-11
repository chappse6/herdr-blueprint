import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Keep inbox and config files inside the test's temp folder."""
    monkeypatch.setattr("herdr_blueprint.sources.inbox.user_state_dir", lambda _app: str(tmp_path / "state"))
    monkeypatch.setattr("herdr_blueprint.config.user_config_dir", lambda _app: str(tmp_path / "config"))
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "test")
    # Tests never reach a real herdr: without a socket path herdr would use its default
    # one, which is the developer's own session (a test once sent prompts to a real agent).
    monkeypatch.setenv("HERDR_BIN_PATH", str(tmp_path / "no-herdr"))
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "no-herdr.sock"))
    # Without its own pane id, Blueprint never opens an editor pane in a real session.
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def fresh_parts_cache():
    """Each test works parts out itself (some patch termaid)."""
    from herdr_blueprint import point

    point._parts_cache_clear()
