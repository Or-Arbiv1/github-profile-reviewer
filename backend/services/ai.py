import logging
import anthropic
from typing import Protocol
from pydantic import BaseModel, ValidationError
from backend.config import settings
from backend.errors import AnalyzeError
from backend.models import Level, RepoAssessment

logger = logging.getLogger("github_profile_reviewer")


class _AssessedFields(BaseModel):
    level: Level
    readme_clarity: str
    complexity: str
    assessment: str


class AIProvider(Protocol):
    async def assess_repo(self, repo: dict, readme: str) -> RepoAssessment: ...
    async def synthesize(self, assessments: list[RepoAssessment]) -> str: ...


class AnthropicProvider:
    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.ai_api_key)

    async def assess_repo(self, repo: dict, readme: str) -> RepoAssessment:
        prompt = f"""Assess this GitHub repository and return a structured JSON analysis.

Name: {repo['name']}
Language: {repo.get('language') or 'unknown'}
Stars: {repo.get('stargazers_count', 0)}
Description: {repo.get('description') or 'none'}
Topics: {', '.join(repo.get('topics', [])) or 'none'}
Size (KB): {repo.get('size', 0)}
Last pushed: {repo.get('pushed_at', 'unknown')}

README (may be truncated):
{readme or '(no README)'}

Return JSON only. Field constraints:
- level: exactly one of "Basic", "Intermediate", "Advanced"
- readme_clarity: one of "Clear", "Sparse", "Missing"
- complexity: a short phrase (e.g. "Simple CRUD", "Non-trivial algorithm", "Boilerplate")
- assessment: 1-2 sentences describing what this repo reveals about the developer"""

        _tool = {
            "name": "submit_assessment",
            "description": "Submit a structured assessment of a GitHub repository.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "enum": ["Basic", "Intermediate", "Advanced"]},
                    "readme_clarity": {"type": "string", "enum": ["Clear", "Sparse", "Missing"]},
                    "complexity": {"type": "string"},
                    "assessment": {"type": "string"},
                },
                "required": ["level", "readme_clarity", "complexity", "assessment"],
            },
        }

        try:
            response = await self._client.messages.create(
                model=settings.ai_model,
                max_tokens=512,
                tools=[_tool],
                tool_choice={"type": "tool", "name": "submit_assessment"},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            raise AnalyzeError("ai_auth", 502, "AI API key is invalid or expired.")
        except Exception:
            # Log the real detail server-side; never leak SDK internals to the client.
            logger.exception("AI assess_repo call failed for %r", repo.get("name"))
            raise AnalyzeError("upstream", 502, "The AI service failed to respond.")

        tool_block = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_block is None:
            raise AnalyzeError("upstream", 502, "The AI service returned no assessment.")
        try:
            assessed = _AssessedFields(**tool_block.input)
        except (ValidationError, TypeError):
            # Model returned off-schema output (e.g. a level outside the enum).
            logger.warning("Malformed AI output for %r: %s", repo.get("name"), tool_block.input)
            raise AnalyzeError("upstream", 502, "The AI service returned malformed output.")
        return RepoAssessment(
            name=repo["name"],
            url=repo.get("html_url", ""),
            language=repo.get("language"),
            stars=repo.get("stargazers_count", 0),
            archived=repo.get("archived", False),
            level=assessed.level,
            readme_clarity=assessed.readme_clarity,
            complexity=assessed.complexity,
            assessment=assessed.assessment,
        )

    async def synthesize(self, assessments: list[RepoAssessment]) -> str:
        lines = "\n".join(
            f"- {a.name} ({a.level.value if a.level else 'Unrated'}): {a.assessment}"
            for a in assessments
        )
        prompt = f"""You have assessed {len(assessments)} GitHub repositories for a developer:

{lines}

Write 2-3 sentences synthesizing their overall level, strongest demonstrated skills, and notable gaps. Be specific and direct."""

        try:
            response = await self._client.messages.create(
                model=settings.ai_model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            raise AnalyzeError("ai_auth", 502, "AI API key is invalid or expired.")
        except Exception:
            logger.exception("AI synthesize call failed")
            raise AnalyzeError("upstream", 502, "The AI service failed to respond.")

        block = next((b for b in response.content if getattr(b, "type", None) == "text"), None)
        if block is None:
            raise AnalyzeError("upstream", 502, "The AI service returned no synthesis.")
        return block.text


class MockProvider:
    # Dev-only fake; deleted at submission (see TODO cleanup).
    async def assess_repo(self, repo: dict, readme: str) -> RepoAssessment:
        import random
        level = random.choice(list(Level))
        clarity = "Missing" if not readme else random.choice(["Clear", "Sparse"])
        return RepoAssessment(
            name=repo["name"],
            url=repo.get("html_url", ""),
            language=repo.get("language"),
            stars=repo.get("stargazers_count", 0),
            archived=repo.get("archived", False),
            level=level,
            readme_clarity=clarity,
            complexity=random.choice(["Simple CRUD", "Non-trivial", "Boilerplate", "Library"]),
            assessment=f"Mock assessment for {repo['name']}. Demonstrates {level.value.lower()} skill.",
        )

    async def synthesize(self, assessments: list[RepoAssessment]) -> str:
        return (
            f"Mock synthesis across {len(assessments)} repos. "
            "Developer shows a range of Basic to Advanced projects with varied complexity."
        )


def get_provider() -> AIProvider:
    if settings.mock_ai:
        return MockProvider()
    return AnthropicProvider()
