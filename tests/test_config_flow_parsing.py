"""Tests for parsing the pasted redirect URL.

The sign-in flow redirects to ``https://jwt.ms`` with ``response_mode=fragment``
— a real page every browser loads, whose address the user copies out of the
address bar. The parser therefore has to read the **fragment**, while still
accepting the query-mode URLs older builds produced and a bare code pasted on
its own.

`config_flow` imports Home Assistant's config-entries and selector machinery,
which is far more than `conftest.py` stubs, so the two module-level helpers are
loaded from source rather than by importing the module.
"""

from __future__ import annotations

import pathlib
import types

import pytest

_SOURCE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "marvin_connected_home"
    / "config_flow.py"
)


def _load_helpers() -> types.ModuleType:
    """Execute just the URL-parsing helpers, without the flow classes."""
    module = types.ModuleType("_parsing")
    module.__dict__["__builtins__"] = __builtins__
    source = _SOURCE.read_text()
    start = source.index("def _parameters(")
    # The only "untrusted input" here is this repo's own source file.
    exec("from urllib.parse import parse_qs, urlparse\n" + source[start:], module.__dict__)
    return module


_helpers = _load_helpers()
_extract_code = _helpers._extract_code
_extract_state = _helpers._extract_state

CODE = "eyJraWQiOiJjcGltY29yZV8wOTI1MjAxNSJ9.abc-DEF_123"
STATE = "Ab3xYz-0"


class TestExtractCode:
    def test_fragment_url(self) -> None:
        """What the flow now produces: jwt.ms plus a fragment."""
        assert _extract_code(f"https://jwt.ms#code={CODE}&state={STATE}") == CODE

    def test_fragment_url_with_other_parameters_first(self) -> None:
        assert _extract_code(f"https://jwt.ms#state={STATE}&code={CODE}") == CODE

    def test_query_url_still_works(self) -> None:
        """Older builds used query mode; a user may paste one either way."""
        assert _extract_code(f"https://jwt.ms/?code={CODE}&state={STATE}") == CODE

    def test_custom_scheme_url_still_works(self) -> None:
        """Anyone who completed the old aurora:// flow must not be stranded."""
        assert _extract_code(f"aurora://login/verify?code={CODE}") == CODE

    def test_bare_code(self) -> None:
        assert _extract_code(CODE) == CODE

    def test_bare_fragment(self) -> None:
        assert _extract_code(f"#code={CODE}") == CODE

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_empty_is_none(self, value: str | None) -> None:
        assert _extract_code(value) is None

    def test_url_without_parameters_is_not_a_code(self) -> None:
        """A truncated paste must report "no code found" rather than being
        taken for a bare code and rejected later as expired."""
        assert _extract_code("https://jwt.ms") is None

    def test_quotes_and_whitespace_are_stripped(self) -> None:
        assert _extract_code(f'  "https://jwt.ms#code={CODE}"  ') == CODE

    def test_error_redirect_has_no_code(self) -> None:
        assert _extract_code("https://jwt.ms#error=access_denied") is None


class TestExtractState:
    def test_from_fragment(self) -> None:
        assert _extract_state(f"https://jwt.ms#code={CODE}&state={STATE}") == STATE

    def test_from_query(self) -> None:
        assert _extract_state(f"https://jwt.ms/?code={CODE}&state={STATE}") == STATE

    def test_absent_state_is_none(self) -> None:
        """A bare code carries no state; that is accepted, since PKCE already
        binds the code to this flow."""
        assert _extract_state(CODE) is None
        assert _extract_state(f"https://jwt.ms#code={CODE}") is None
