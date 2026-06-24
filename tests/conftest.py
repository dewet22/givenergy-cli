"""Shared pytest fixtures for the givenergy-cli test suite."""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_givenergy_env(monkeypatch):
    """Strip GIVENERGY_* env vars for every test.

    The CLI reads --host/--port/etc. from GIVENERGY_* envvars, and a dev's
    .envrc sets them. Click's CliRunner overlays os.environ rather than
    replacing it, so without this those values leak into invocations and make
    env-sensitive assertions (e.g. "host is required") pass or fail by accident.
    """
    for key in [k for k in os.environ if k.startswith("GIVENERGY_")]:
        monkeypatch.delenv(key, raising=False)
