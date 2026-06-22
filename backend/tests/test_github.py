import asyncio
import httpx
import pytest
from backend.services import github


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


def _patch_get(monkeypatch, response=None, exc=None):
    """Make github._get_client().get(...) return `response` (or raise `exc`)."""
    class _FakeClient:
        async def get(self, *args, **kwargs):
            if exc is not None:
                raise exc
            return response

    monkeypatch.setattr(github, "_get_client", lambda: _FakeClient())


# ─── list_repos ────────────────────────────────────────────────────────────────

def _repo(name, fork=False, archived=False, pushed_at="2024-01-01T00:00:00Z"):
    return {"name": name, "fork": fork, "archived": archived, "pushed_at": pushed_at}


def _list_repos(*args, **kwargs):
    return asyncio.run(github.list_repos(*args, **kwargs))


def test_list_repos_excludes_forks_and_counts(monkeypatch):
    raw = [_repo("own-1"), _repo("a-fork", fork=True), _repo("own-2", archived=True)]
    _patch_get(monkeypatch, _FakeResponse(json_data=raw))
    fetched = _list_repos("u")
    assert [r["name"] for r in fetched.repos] == ["own-1", "own-2"]  # fork dropped
    assert fetched.total_found == 3       # everything GitHub returned
    assert fetched.forks_excluded == 1
    assert fetched.archived_total == 1    # archived among the kept (non-fork) repos


def test_list_repos_sorts_by_pushed_at_desc(monkeypatch):
    raw = [
        _repo("older", pushed_at="2023-01-01T00:00:00Z"),
        _repo("newest", pushed_at="2025-06-01T00:00:00Z"),
        _repo("middle", pushed_at="2024-03-01T00:00:00Z"),
    ]
    _patch_get(monkeypatch, _FakeResponse(json_data=raw))
    assert [r["name"] for r in _list_repos("u").repos] == ["newest", "middle", "older"]


def test_list_repos_caps_at_max_repos(monkeypatch):
    monkeypatch.setattr(github.settings, "max_repos", 2)
    raw = [_repo(f"r{i}", pushed_at=f"2024-01-{i:02d}T00:00:00Z") for i in range(1, 6)]
    _patch_get(monkeypatch, _FakeResponse(json_data=raw))
    fetched = _list_repos("u")
    assert len(fetched.repos) == 2        # only the cap is kept
    assert fetched.total_found == 5       # but the true total is still reported


def test_list_repos_404_is_user_not_found(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(status_code=404))
    with pytest.raises(github.AnalyzeError) as exc:
        _list_repos("ghost")
    assert exc.value.code == "user_not_found"
    assert exc.value.http_status == 404


def test_list_repos_rate_limit(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(status_code=403, headers={"X-RateLimit-Remaining": "0"}))
    with pytest.raises(github.AnalyzeError) as exc:
        _list_repos("u")
    assert exc.value.code == "github_rate_limit"


def test_list_repos_other_status_is_upstream(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(status_code=500))
    with pytest.raises(github.AnalyzeError) as exc:
        _list_repos("u")
    assert exc.value.code == "upstream"


def test_list_repos_unreachable_is_upstream(monkeypatch):
    _patch_get(monkeypatch, exc=httpx.ConnectError("boom"))
    with pytest.raises(github.AnalyzeError) as exc:
        _list_repos("u")
    assert exc.value.code == "upstream"


def test_list_repos_non_list_payload_is_upstream(monkeypatch):
    # GitHub normally returns an array; an object here means something is wrong upstream.
    _patch_get(monkeypatch, _FakeResponse(json_data={"message": "weird"}))
    with pytest.raises(github.AnalyzeError) as exc:
        _list_repos("u")
    assert exc.value.code == "upstream"


def test_list_repos_unreadable_json_is_upstream(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(json_data=ValueError("not json")))
    with pytest.raises(github.AnalyzeError) as exc:
        _list_repos("u")
    assert exc.value.code == "upstream"


# ─── get_readme ────────────────────────────────────────────────────────────────

def _readme(*args, **kwargs):
    return asyncio.run(github.get_readme(*args, **kwargs))


def test_readme_returns_text(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(text="# Hello"))
    assert _readme("u", "r") == "# Hello"


def test_readme_404_is_empty_string(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(status_code=404))
    assert _readme("u", "r") == ""


def test_readme_unreachable_is_empty_string(monkeypatch):
    # A README we can't reach shouldn't be fatal — treat as no README.
    _patch_get(monkeypatch, exc=httpx.ConnectError("boom"))
    assert _readme("u", "r") == ""


def test_readme_is_truncated_to_max_chars(monkeypatch):
    monkeypatch.setattr(github.settings, "readme_max_chars", 10)
    _patch_get(monkeypatch, _FakeResponse(text="x" * 50))
    assert _readme("u", "r") == "x" * 10


def test_readme_rate_limit_surfaces(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(status_code=403, headers={"X-RateLimit-Remaining": "0"}))
    with pytest.raises(github.AnalyzeError) as exc:
        _readme("u", "r")
    assert exc.value.code == "github_rate_limit"


def test_readme_other_status_is_upstream(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(status_code=500))
    with pytest.raises(github.AnalyzeError) as exc:
        _readme("u", "r")
    assert exc.value.code == "upstream"
