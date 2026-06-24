import httpx
from dataclasses import dataclass
from backend.config import settings
from backend.errors import AnalyzeError

_BASE = "https://api.github.com"


@dataclass
class RepoFetch:
    """Result of one list_repos call — the repos we keep plus counts for the UI.

    The counts come from the single array we already fetched (no extra request):
    `total_found` is what GitHub returned, `forks_excluded` is how many we dropped.
    Archived repos are kept (they're the user's own work) and tagged downstream.
    """
    repos: list[dict]
    total_found: int      # every repo GitHub returned for this user (incl. forks)
    forks_excluded: int   # of those, how many were forks we dropped
    archived_total: int   # of the user's own (non-fork) repos, how many are archived


def _headers() -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        h["Authorization"] = f"Bearer {settings.github_token}"
    return h


# Generous but bounded: a hung GitHub connection must not pin a concurrency slot forever.
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# One shared client per process. A run fetches ~25 READMEs back-to-back; a fresh client
# per call would mean a fresh TCP+TLS handshake per call. Pooling/keep-alive across the
# fan-out is the whole latency win. Created lazily inside the running loop; closed on
# app shutdown via aclose_client() (see main.py lifespan).
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=_BASE, timeout=_TIMEOUT)
    return _client


async def aclose_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _raise_for_status(r: httpx.Response, context: str = "") -> None:
    """Map every non-200 we don't handle specially into an AnalyzeError. The caller handles 404
    first, because its meaning differs per endpoint (no such user vs. no README). `context` only
    tails the generic fallback message so the caller can say which call failed.

    Order matters: rate-limit and auth carry an actionable fix, so they must be matched before
    the generic fallback swallows them as a bland 'GitHub returned X'."""
    # 403 with no remaining quota = primary rate limit; 429 = secondary. Both surface as the
    # actionable 'add a GITHUB_TOKEN' state.
    is_primary = r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0"
    is_secondary = r.status_code == 429
    if is_primary or is_secondary:
        raise AnalyzeError(
            "github_rate_limit",
            429,
            "GitHub rate limit reached. Add GITHUB_TOKEN to .env to raise it to 5,000/hr.",
        )
    # 401 = the configured GITHUB_TOKEN is bad (invalid/expired/revoked). GitHub does NOT fall
    # back to anonymous access for a present-but-invalid token — every call 401s identically — so
    # this is systemic: surface the one fix, and don't confuse it with a rate limit.
    if r.status_code == 401:
        raise AnalyzeError(
            "github_auth",
            502,
            "GitHub token is invalid or expired. Check or remove GITHUB_TOKEN in .env.",
        )
    if r.status_code != 200:
        raise AnalyzeError("upstream", 502, f"GitHub returned {r.status_code}{context}.")


async def list_repos(username: str) -> RepoFetch:
    try:
        r = await _get_client().get(
            f"/users/{username}/repos",
            params={"per_page": 100, "sort": "pushed"},
            headers=_headers(),
        )
    except httpx.RequestError:
        # DNS failure, connection refused, timeout — GitHub is unreachable, not a user error.
        raise AnalyzeError("upstream", 502, "Could not reach GitHub. Try again.")
    if r.status_code == 404:
        raise AnalyzeError("user_not_found", 404, f"No GitHub user '{username}'.")
    _raise_for_status(r)
    try:
        raw = r.json()
    except ValueError:
        raise AnalyzeError("upstream", 502, "GitHub returned an unreadable response.")
    if not isinstance(raw, list):
        raise AnalyzeError("upstream", 502, "GitHub returned an unexpected response.")
    # Exclude forks (someone else's code). Keep archived — it's still the user's own work.
    repos = [repo for repo in raw if not repo.get("fork")]
    repos.sort(key=lambda repo: repo.get("pushed_at") or "", reverse=True)
    return RepoFetch(
        repos=repos[: settings.max_repos],
        total_found=len(raw),
        forks_excluded=sum(1 for repo in raw if repo.get("fork")),
        archived_total=sum(1 for repo in repos if repo.get("archived")),
    )


async def get_readme(username: str, repo: str) -> str:
    try:
        r = await _get_client().get(
            f"/repos/{username}/{repo}/readme",
            headers={**_headers(), "Accept": "application/vnd.github.raw+json"},
        )
    except httpx.RequestError:
        # A README fetch that can't reach GitHub shouldn't be fatal — treat as no README.
        return ""
    if r.status_code == 404:
        return ""
    _raise_for_status(r, " fetching readme")
    return r.text[: settings.readme_max_chars]
