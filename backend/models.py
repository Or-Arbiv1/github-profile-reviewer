from enum import Enum
from pydantic import BaseModel


class Level(str, Enum):
    basic = "Basic"
    intermediate = "Intermediate"
    advanced = "Advanced"


class AnalyzeRequest(BaseModel):
    username: str


class RepoAssessment(BaseModel):
    name: str
    url: str
    language: str | None
    stars: int
    archived: bool = False  # the repo is the user's work but frozen (read-only)
    summary: str | None = None  # neutral description of what the repo is; None == degraded card
    level: Level | None = None  # None == we couldn't assess this repo (AI call failed)
    readme_clarity: str   # e.g. "Clear", "Sparse", "Missing"
    complexity: str       # e.g. "Simple CRUD", "Non-trivial"
    assessment: str       # the verdict: is this a good portfolio piece for a junior, + main takeaway


class AnalyzeResponse(BaseModel):
    username: str
    repo_count: int          # repos successfully assessed (rated; excludes failed cards)
    failed_count: int = 0    # repos we attempted but couldn't assess (degraded cards)
    total_found: int = 0     # repos GitHub returned for this user (incl. forks)
    forks_excluded: int = 0  # how many of those were forks we skipped
    archived_total: int = 0  # archived repos among the user's own (non-fork) work
    synthesis: str           # 2–3 sentence overall level summary
    repos: list[RepoAssessment]
