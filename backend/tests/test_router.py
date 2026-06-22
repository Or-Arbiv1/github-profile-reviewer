import pytest
from backend.routers.analyze import _normalize, _VALID_USERNAME


@pytest.mark.parametrize("raw, expected", [
    ("octocat", "octocat"),
    ("  octocat  ", "octocat"),                                   # surrounding whitespace
    ("@octocat", "octocat"),                                       # leading @
    ("octocat/", "octocat"),                                       # trailing slash
    ("https://github.com/octocat", "octocat"),                     # full profile URL
    ("http://github.com/octocat", "octocat"),                      # http scheme
    ("https://github.com/octocat?tab=repositories", "octocat"),    # URL with query
    ("https://github.com/octocat/repo", "octocat"),               # URL with path → owner only
])
def test_normalize(raw, expected):
    assert _normalize(raw) == expected


@pytest.mark.parametrize("name", ["octocat", "a", "a-b-c", "Or-Arbiv", "x" * 39])
def test_valid_usernames_accepted(name):
    assert _VALID_USERNAME.match(name)


@pytest.mark.parametrize("name", [
    "",                 # empty
    "-leading",         # leading hyphen
    "trailing-",        # trailing hyphen
    "has space",        # space
    "foo/bar",          # slash (path traversal shape)
    "..",               # dotted
    "x" * 40,           # too long (>39)
    "white@space",      # illegal char
])
def test_invalid_usernames_rejected(name):
    assert not _VALID_USERNAME.match(name)
