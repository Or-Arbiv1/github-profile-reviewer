import httpx
from unittest.mock import AsyncMock
from backend.errors import AnalyzeError
from backend.models import Level, RepoAssessment
from backend.services.github import RepoFetch
from backend.tests.conftest import FAKE_REPO, FakeProvider


def _fetch(repos, total_found=None, forks_excluded=0, archived_total=0):
    return RepoFetch(
        repos=repos,
        total_found=len(repos) if total_found is None else total_found,
        forks_excluded=forks_excluded,
        archived_total=archived_total,
    )


def test_user_not_found(client, monkeypatch):
    async def _raise(username):
        raise AnalyzeError("user_not_found", 404, f"No GitHub user '{username}'.")

    monkeypatch.setattr("backend.services.github.list_repos", _raise)
    resp = client.post("/analyze", json={"username": "nobody-xyz-404"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "user_not_found"


def test_malformed_username_is_rejected_before_github(client, monkeypatch):
    # A value that can't be a GitHub username must never reach the API path: we short-circuit
    # to user_not_found and list_repos is never called.
    called = AsyncMock()
    monkeypatch.setattr("backend.services.github.list_repos", called)

    resp = client.post("/analyze", json={"username": "foo/bar/../etc"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "user_not_found"
    called.assert_not_called()


def test_profile_url_is_normalized_and_accepted(client, monkeypatch):
    # A full profile URL (with query string) collapses to the bare username and passes validation.
    seen = {}

    async def _capture(username):
        seen["username"] = username
        return _fetch([FAKE_REPO])

    monkeypatch.setattr("backend.services.github.list_repos", _capture)
    monkeypatch.setattr("backend.services.github.get_readme", AsyncMock(return_value=""))
    monkeypatch.setattr("backend.services.analyzer.get_provider", lambda: FakeProvider())

    resp = client.post("/analyze", json={"username": "https://github.com/octocat?tab=repositories"})
    assert resp.status_code == 200
    assert seen["username"] == "octocat"


def test_github_rate_limit(client, monkeypatch):
    async def _raise(username):
        raise AnalyzeError("github_rate_limit", 429, "Rate limit reached.")

    monkeypatch.setattr("backend.services.github.list_repos", _raise)
    resp = client.post("/analyze", json={"username": "testuser"})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "github_rate_limit"


def test_invalid_ai_key(client, monkeypatch):
    class _BadProvider:
        async def assess_repo(self, repo, readme):
            raise AnalyzeError("ai_auth", 502, "AI key invalid.")

        async def synthesize(self, assessments):
            raise AnalyzeError("ai_auth", 502, "AI key invalid.")

    monkeypatch.setattr("backend.services.github.list_repos", AsyncMock(return_value=_fetch([FAKE_REPO])))
    monkeypatch.setattr("backend.services.github.get_readme", AsyncMock(return_value="Some readme"))
    monkeypatch.setattr("backend.services.analyzer.get_provider", lambda: _BadProvider())

    resp = client.post("/analyze", json={"username": "testuser"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "ai_auth"


def test_invalid_ai_model_aborts_with_ai_model(client, monkeypatch):
    # A wrong AI_MODEL fails every repo identically, so it must abort fast with the one fix —
    # not degrade to N "Unrated" cards and then masquerade as a generic 'upstream' error.
    class _BadModelProvider:
        async def assess_repo(self, repo, readme):
            raise AnalyzeError("ai_model", 502, "AI model 'nope' not found. Check AI_MODEL in .env.")

        async def synthesize(self, assessments):
            raise AnalyzeError("ai_model", 502, "AI model 'nope' not found. Check AI_MODEL in .env.")

    good = dict(FAKE_REPO, name="good-repo")
    bad = dict(FAKE_REPO, name="bad-repo")
    monkeypatch.setattr(
        "backend.services.github.list_repos",
        AsyncMock(return_value=_fetch([good, bad], total_found=2)),
    )
    monkeypatch.setattr("backend.services.github.get_readme", AsyncMock(return_value="Some readme"))
    monkeypatch.setattr("backend.services.analyzer.get_provider", lambda: _BadModelProvider())

    resp = client.post("/analyze", json={"username": "testuser"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "ai_model"  # aborted, not degraded


def test_no_readme_still_produces_assessment(client, monkeypatch):
    monkeypatch.setattr("backend.services.github.list_repos", AsyncMock(return_value=_fetch([FAKE_REPO])))
    monkeypatch.setattr("backend.services.github.get_readme", AsyncMock(return_value=""))
    monkeypatch.setattr("backend.services.analyzer.get_provider", lambda: FakeProvider())

    resp = client.post("/analyze", json={"username": "testuser"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo_count"] == 1
    assert data["repos"][0]["name"] == "test-repo"


def test_all_forks_reports_empty_with_counts(client, monkeypatch):
    # GitHub returned 3 repos but all were forks -> 0 kept, surfaced for the UI message.
    monkeypatch.setattr(
        "backend.services.github.list_repos",
        AsyncMock(return_value=_fetch([], total_found=3, forks_excluded=3)),
    )
    monkeypatch.setattr("backend.services.analyzer.get_provider", lambda: FakeProvider())

    resp = client.post("/analyze", json={"username": "testuser"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo_count"] == 0
    assert data["total_found"] == 3
    assert data["forks_excluded"] == 3
    assert data["repos"] == []


def test_github_unreachable_maps_to_upstream(client, monkeypatch):
    # GitHub is unreachable (DNS/connection/timeout): the real list_repos must turn
    # httpx.RequestError into a clean 'upstream' state, never an unhandled 500.
    class _RaisingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("backend.services.github.httpx.AsyncClient", _RaisingClient)

    resp = client.post("/analyze", json={"username": "testuser"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream"


def test_one_repo_failure_becomes_degraded_card(client, monkeypatch):
    # A transient, single-repo AI failure must not tear down the whole batch. The good
    # repo is rated; the bad one still appears as an "Unrated" card (level=None), in order.
    good = dict(FAKE_REPO, name="good-repo")
    bad = dict(FAKE_REPO, name="bad-repo")
    monkeypatch.setattr(
        "backend.services.github.list_repos",
        AsyncMock(return_value=_fetch([good, bad], total_found=2)),
    )
    monkeypatch.setattr("backend.services.github.get_readme", AsyncMock(return_value=""))

    class _FlakyProvider:
        async def assess_repo(self, repo, readme):
            if repo["name"] == "bad-repo":
                raise AnalyzeError("upstream", 502, "transient blip")
            return RepoAssessment(
                name=repo["name"], url="", language="Python", stars=0,
                level=Level.basic, readme_clarity="Clear",
                complexity="Simple", assessment="ok",
            )

        async def synthesize(self, assessments):
            # synthesis must only see the repos we actually rated
            assert [a.name for a in assessments] == ["good-repo"]
            return "synthesis over the survivors"

    monkeypatch.setattr("backend.services.analyzer.get_provider", lambda: _FlakyProvider())

    resp = client.post("/analyze", json={"username": "testuser"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo_count"] == 1     # only the rated repo counts as analyzed
    assert data["failed_count"] == 1   # the degraded one is reported separately
    assert [r["name"] for r in data["repos"]] == ["good-repo", "bad-repo"]  # both still shown

    bad_card = next(r for r in data["repos"] if r["name"] == "bad-repo")
    assert bad_card["level"] is None
    assert "Couldn't assess" in bad_card["assessment"]


def test_all_repos_fail_transiently_is_upstream(client, monkeypatch):
    # If every repo fails for a non-systemic reason, surface a real 'upstream' error
    # rather than masquerading as the friendly empty state.
    monkeypatch.setattr(
        "backend.services.github.list_repos",
        AsyncMock(return_value=_fetch([FAKE_REPO], total_found=1)),
    )
    monkeypatch.setattr("backend.services.github.get_readme", AsyncMock(return_value=""))

    class _AllFailProvider:
        async def assess_repo(self, repo, readme):
            raise AnalyzeError("upstream", 502, "transient blip")

        async def synthesize(self, assessments):
            return ""

    monkeypatch.setattr("backend.services.analyzer.get_provider", lambda: _AllFailProvider())

    resp = client.post("/analyze", json={"username": "testuser"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream"


def test_happy_path(client, monkeypatch):
    monkeypatch.setattr("backend.services.github.list_repos", AsyncMock(return_value=_fetch([FAKE_REPO])))
    monkeypatch.setattr(
        "backend.services.github.get_readme", AsyncMock(return_value="# Test\n\nA test repo.")
    )
    monkeypatch.setattr("backend.services.analyzer.get_provider", lambda: FakeProvider())

    resp = client.post("/analyze", json={"username": "testuser"})
    assert resp.status_code == 200

    data = resp.json()
    assert data["username"] == "testuser"
    assert data["repo_count"] == 1
    assert data["synthesis"]

    repo = data["repos"][0]
    assert repo["name"] == "test-repo"
    assert repo["level"] in ("Basic", "Intermediate", "Advanced")
    assert repo["readme_clarity"]
    assert repo["complexity"]
    assert repo["assessment"]
