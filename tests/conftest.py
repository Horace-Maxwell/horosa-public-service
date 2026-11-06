"""Shared test configuration.

The developer shell exports local Horosa settings (and may export a cloud-decision API key). The public
package must never see them, so every test starts from an environment with no ``HOROSA_*`` keys; values
are removed, never printed.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def isolate_horosa_environment(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("HOROSA_"):
            monkeypatch.delenv(key)
