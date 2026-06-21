import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.models import RepoAssessment, Level

FAKE_REPO = {
    "name": "test-repo",
    "html_url": "https://github.com/testuser/test-repo",
    "language": "Python",
    "stargazers_count": 5,
    "fork": False,
    "archived": False,
    "pushed_at": "2024-01-01T00:00:00Z",
    "description": "A test repository",
    "topics": [],
    "size": 100,
}

FAKE_ASSESSMENT = RepoAssessment(
    name="test-repo",
    url="https://github.com/testuser/test-repo",
    language="Python",
    stars=5,
    level=Level.intermediate,
    readme_clarity="Clear",
    complexity="Simple CRUD",
    assessment="A well-structured test repository demonstrating solid fundamentals.",
)


class FakeProvider:
    async def assess_repo(self, repo: dict, readme: str) -> RepoAssessment:
        return FAKE_ASSESSMENT

    async def synthesize(self, assessments: list[RepoAssessment]) -> str:
        return "An intermediate developer with solid fundamentals across several projects."


@pytest.fixture
def client():
    return TestClient(app)
