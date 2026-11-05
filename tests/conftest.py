"""Test setup: makes `custom_components` importable the way HA's config dir does.

    python3.14 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
    .venv/bin/pytest
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
