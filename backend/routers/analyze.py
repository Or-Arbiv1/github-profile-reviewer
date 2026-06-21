import re
from fastapi import APIRouter
from backend.errors import AnalyzeError
from backend.models import AnalyzeRequest, AnalyzeResponse
from backend.services import analyzer

router = APIRouter()

_GITHUB_URL = re.compile(r"https?://github\.com/([^/?#]+)")
# GitHub's own rule: 1–39 chars, alphanumeric or single hyphens, no leading/trailing hyphen.
# Validating against this keeps anything weird out of the API path (no injection / SSRF).
_VALID_USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


def _normalize(username: str) -> str:
    username = username.strip().lstrip("@").rstrip("/")
    m = _GITHUB_URL.match(username)
    return m.group(1) if m else username


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    username = _normalize(req.username)
    if not _VALID_USERNAME.match(username):
        # Not a possible GitHub username — surface the same clean state as "doesn't exist",
        # and never let the malformed value reach the GitHub API path.
        raise AnalyzeError("user_not_found", 404, f"No GitHub user '{username}'.")
    return await analyzer.analyze_user(username)
