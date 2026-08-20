"""Reading configuration out of the environment.

`env_file` in Compose is not a shell, so values arrive exactly as written. These
cover the two ways a correct token still fails to authenticate.
"""

import pytest

from app.config import _clean, _require


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("abc123", "abc123"),
        ("  abc123  ", "abc123"),
        ('"abc123"', "abc123"),       # quotes a shell would strip; Compose does not
        ("'abc123'", "abc123"),
        ('" abc123 "', "abc123"),
        ("abc123\n", "abc123"),       # a heredoc or an editor adding a newline
        ('"', '"'),                   # too short to be a quoted pair
        ("", ""),
        ('a"b', 'a"b'),               # quotes inside are part of the value
    ],
)
def test_clean(raw: str, expected: str) -> None:
    assert _clean(raw) == expected


def test_required_variable_is_named_when_missing(monkeypatch) -> None:
    # The message has to name the variable: a container that will not start is
    # only useful if it says what it wanted.
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    with pytest.raises(RuntimeError, match="NOT_SET_ANYWHERE"):
        _require("NOT_SET_ANYWHERE")


def test_whitespace_only_counts_as_missing(monkeypatch) -> None:
    monkeypatch.setenv("BLANK", "   ")
    with pytest.raises(RuntimeError):
        _require("BLANK")
