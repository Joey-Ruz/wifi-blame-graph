"""Point the code at a settings file that doesn't exist, so every test starts
from the default settings instead of whatever is in the real config.json."""

import os
import tempfile
from pathlib import Path

os.environ["WIFIBLAME_CONFIG"] = str(Path(tempfile.gettempdir()) / "wifiblame-tests-no-config.json")

import pytest  # noqa: E402

from wifiblame import config  # noqa: E402


@pytest.fixture(autouse=True)
def default_settings():
    config.current.cache_clear()
    yield
    config.current.cache_clear()


@pytest.fixture
def use_settings(monkeypatch):
    """Run a test with particular settings layered over the defaults."""
    def apply(**changes):
        settings = dict(config.DEFAULT_SETTINGS)
        settings.update(changes)
        monkeypatch.setattr(config, "current", lambda: settings)
        return settings
    return apply
