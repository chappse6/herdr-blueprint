import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Keep inbox and config files inside the test's temp folder."""
    monkeypatch.setattr("herdr_blueprint.sources.inbox.user_state_dir", lambda _app: str(tmp_path / "state"))
    monkeypatch.setattr("herdr_blueprint.config.user_config_dir", lambda _app: str(tmp_path / "config"))
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "test")
    # Tests decide whether they run inside a herdr session.
    monkeypatch.delenv("HERDR_SOCKET_PATH", raising=False)
    # Without its own pane id, Blueprint never opens an editor pane in a real session.
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def fresh_parts_cache():
    """Each test works parts out itself (some patch termaid)."""
    from herdr_blueprint import point

    point._parts_cache_clear()
