"""Shared pytest fixtures for alph tests."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect ALPH_CONFIG_DIR to a per-test temp directory.

    Prevents CLI tests from reading or writing the real
    ~/.config/alph/config.yaml during test runs.
    """
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(tmp_path / "alph-config"))
