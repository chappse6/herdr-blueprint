import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Keep inbox and config files inside the test's temp folder."""
    monkeypatch.setattr("herdr_blueprint.sources.inbox.user_state_dir", lambda _app: str(tmp_path / "state"))
    monkeypatch.setattr("herdr_blueprint.config.user_config_dir", lambda _app: str(tmp_path / "config"))
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "test")
    return tmp_path
