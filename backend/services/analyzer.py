import asyncio
import logging
from backend.config import settings
from backend.errors import AnalyzeError
from backend.models import AnalyzeResponse, RepoAssessment
from backend.services import github
from backend.services.ai import get_provider

logger = logging.getLogger("github_profile_reviewer")

# Codes that affect every repo identically: a bad key, a denied request (e.g. spend cap),
# a wrong model id, or an exhausted rate limit (GitHub's or the AI's) will fail all calls the
# same way. Surfacing the one actionable fix beats rendering N broken cards — so these abort
# instead of going per-repo.
_SYSTEMIC = {"ai_auth", "ai_access", "ai_model", "ai_rate_limit", "github_auth", "github_rate_limit"}


def _failed_card(repo: dict) -> RepoAssessment:
    """A repo whose AI call failed transiently. We still have its GitHub metadata, so we
    show the card with a clear message rather than dropping it. level=None -> 'Unrated'."""
    return RepoAssessment(
        name=repo["name"],
        url=repo.get("html_url", ""),
        language=repo.get("language"),
        stars=repo.get("stargazers_count", 0),
        archived=repo.get("archived", False),
        level=None,
        readme_clarity="—",
        complexity="—",
        assessment="Couldn't assess this repository — the AI request failed. Try again.",
    )


async def analyze_user(username: str) -> AnalyzeResponse:
    fetched = await github.list_repos(username)
    provider = get_provider()
    sem = asyncio.Semaphore(settings.concurrency)

    async def one(repo: dict):
        async with sem:
            readme = await github.get_readme(username, repo["name"])
            return await provider.assess_repo(repo, readme)

    # return_exceptions=True so one failing repo can't tear down the whole gather.
    results = await asyncio.gather(*(one(r) for r in fetched.repos), return_exceptions=True)

    # Preserve the original (pushed_at) order; a transient failure becomes a degraded card
    # rather than vanishing. Successfully-rated cards also feed the synthesis below.
    cards: list[RepoAssessment] = []
    rated: list[RepoAssessment] = []
    for repo, result in zip(fetched.repos, results):
        if isinstance(result, RepoAssessment):
            cards.append(result)
            rated.append(result)
        elif isinstance(result, AnalyzeError) and result.code in _SYSTEMIC:
            raise result  # systemic: fail fast with the one clear fix
        else:
            # Transient, single-repo failure — show a degraded card, keep the rest.
            logger.warning("Could not assess repo %r: %s", repo.get("name"), result)
            cards.append(_failed_card(repo))

    # Nothing assessed successfully: that's a real upstream problem, not an empty profile.
    # (Degraded cards alone, with no synthesis, would be a confusing thing to render.)
    if fetched.repos and not rated:
        raise AnalyzeError("upstream", 502, "Could not assess any repositories. Try again.")

    # Synthesis judges level, so it only sees the repos we actually rated.
    synthesis = await provider.synthesize(rated) if rated else ""
    return AnalyzeResponse(
        username=username,
        repo_count=len(rated),               # successfully assessed
        failed_count=len(cards) - len(rated),  # attempted but degraded
        total_found=fetched.total_found,
        forks_excluded=fetched.forks_excluded,
        archived_total=fetched.archived_total,
        synthesis=synthesis,
        repos=cards,
    )
