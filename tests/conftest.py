"""Test setup.

Without Home Assistant installed, register the packages by hand so that
`custom_components.alfred_smart.{models,api,const}` import without running the
package `__init__` (which imports Home Assistant): the API client is then
tested with aiohttp alone. With Home Assistant and
pytest-homeassistant-custom-component installed, the real package is used and
`test_init.py` runs too.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if importlib.util.find_spec("homeassistant") is None:
    for name, path in (
        ("custom_components", ROOT / "custom_components"),
        ("custom_components.alfred_smart", ROOT / "custom_components" / "alfred_smart"),
    ):
        if name not in sys.modules:
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            sys.modules[name] = package
