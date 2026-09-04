"""Every translation key the code raises or files must exist in strings.json.

A missing key does not fail loudly: Home Assistant shows the raw key to the
user. This is the cheapest place to catch it, and it needs no Home Assistant.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "marvin_connected_home"
STRINGS = json.loads((COMPONENT / "strings.json").read_text())
SOURCES = {path.name: path.read_text() for path in COMPONENT.glob("*.py")}

_ERROR_KEY = re.compile(r'_error\(\s*"([a-z_]+)"')
_TRANSLATION_KEY = re.compile(r'translation_key="([a-z_]+)"')


def _keys_in(source: str) -> set[str]:
    return set(_ERROR_KEY.findall(source)) | set(_TRANSLATION_KEY.findall(source))


def test_exception_keys_exist() -> None:
    raised = set()
    for name, source in SOURCES.items():
        # Entity description translation keys are checked separately below;
        # here only the ones passed to HomeAssistantError / _error().
        for match in re.finditer(r"HomeAssistantError\((.*?)\)\s*(?:from|\n)", source, re.S):
            raised |= set(_TRANSLATION_KEY.findall(match.group(1)))
        raised |= set(_ERROR_KEY.findall(source))
    # Button error keys live in descriptions.
    raised |= set(re.findall(r'error_key="([a-z_]+)"', SOURCES["button.py"]))
    missing = raised - set(STRINGS["exceptions"])
    assert not missing, f"exception keys without a message: {sorted(missing)}"


def test_issue_keys_exist() -> None:
    keys = set(re.findall(r'"(using_dry_contacts[a-z_]*)"', SOURCES["cover.py"]))
    assert keys, "no repair issue keys found in cover.py"
    assert keys <= set(STRINGS["issues"])


def test_abort_and_error_reasons_exist() -> None:
    source = SOURCES["config_flow.py"]
    aborts = set(re.findall(r'async_abort\(reason="([a-z_]+)"\)', source))
    errors = set(re.findall(r'errors\["base"\] = "([a-z_]+)"', source))
    errors |= set(re.findall(r'return None, "([a-z_]+)"', source))
    known_aborts = set(STRINGS["config"]["abort"]) | set(STRINGS["options"]["abort"])
    assert aborts <= known_aborts, aborts - known_aborts
    # `async_update_reload_and_abort` aborts with this default reason.
    assert "reauth_successful" in STRINGS["config"]["abort"]
    assert errors <= set(STRINGS["config"]["error"]), errors - set(STRINGS["config"]["error"])


def test_en_matches_strings() -> None:
    en = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert en == STRINGS
