import logging
import anthropic
from typing import Protocol
from pydantic import BaseModel, ValidationError
from backend.config import settings
from backend.errors import AnalyzeError
from backend.models import Level, RepoAssessment

logger = logging.getLogger("github_profile_reviewer")


class _AssessedFields(BaseModel):
    summary: str
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
- summary: maximum 8 words neutrally describing what the project IS (e.g. "REST API for tracking personal expenses", "CLI tool for converting markdown files"). Description only — no judgement.
- level: exactly one of "Basic", "Intermediate", "Advanced"
- readme_clarity: exactly one of "Clear", "Adequate", "Sparse", "Trivial", "Missing"
  - "Missing": no README file exists
  - "Trivial": README exists but contains only auto-generated content (repo name or GitHub's default template, no real writing)
  - "Sparse": has some real content but very thin — missing setup instructions, purpose, or usage
  - "Adequate": covers the basics (what the project does, how to run it) but not comprehensive or polished
  - "Clear": well-written, informative, and easy to follow
- complexity: a short phrase, maximum 3 words, describing HOW complex the project is (its difficulty, not its type), and ALWAYS ending in the word "project" so it reads on its own. Lead with a complexity descriptor (e.g. "Simple CRUD project", "Non-trivial project", "Complex full-stack project", "Trivial starter project"). Never a bare adjective like "Non-trivial", and don't describe only the type (e.g. not "Boilerplate project").
- assessment: maximum 30 words. Your VERDICT on this repo as a portfolio piece for a junior developer: how impressive or routine it is for that level, a brief note on the README's clarity, and the main takeaway about the experience it reflects. A judgement, not a description — do NOT restate the summary (e.g. "Solid intermediate work with clean structure; the README is clear with setup steps. Reflects real comfort with REST APIs, though there's no testing.")."""

        _tool = {
            "name": "submit_assessment",
            "description": "Submit a structured assessment of a GitHub repository.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "level": {"type": "string", "enum": ["Basic", "Intermediate", "Advanced"]},
                    "readme_clarity": {"type": "string", "enum": ["Clear", "Adequate", "Sparse", "Trivial", "Missing"]},
                    "complexity": {"type": "string"},
                    "assessment": {"type": "string"},
                },
                "required": ["summary", "level", "readme_clarity", "complexity", "assessment"],
            },
        }

        try:
            response = await self._client.messages.create(
                model=settings.ai_model,
                max_tokens=1024,
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
            summary=assessed.summary,
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

Write 2-3 sentences synthesizing their overall level, strongest demonstrated skills, and notable gaps. Then you MUST end with a clear verdict sentence that explicitly states whether this developer is a good candidate for a junior full-stack developer role and the main reason why. Always give the verdict — never omit it. Be specific and direct."""

        try:
            response = await self._client.messages.create(
                model=settings.ai_model,
                max_tokens=400,
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
    # Dev-only fake; deleted at submission (see TODO cleanup). Emits MAX-bounded text
    # (summary 8 words, complexity 3, assessment 30, ~4-sentence synthesis) so the card
    # and banner layout can be stress-tested against worst-case lengths without a real key.
    # Levels/clarity stay randomized so every badge variant still shows up.
    async def assess_repo(self, repo: dict, readme: str) -> RepoAssessment:
        import random
        level = random.choice(list(Level))
        clarity = "Missing" if not readme else random.choice(["Clear", "Adequate", "Sparse", "Trivial"])
        return RepoAssessment(
            name=repo["name"],
            url=repo.get("html_url", ""),
            language=repo.get("language"),
            stars=repo.get("stargazers_count", 0),
            archived=repo.get("archived", False),
            summary="A comprehensive full-stack web application managing complex inventory",  # 8 words
            level=level,
            readme_clarity=clarity,
            complexity="Complex full-stack project",  # 3 words
            assessment=(  # 30 words
                "Genuinely strong intermediate work with clean modular structure and thoughtful "
                "abstractions; the README is clear with thorough setup steps and examples, "
                "reflecting real comfort building production applications end to end"
            ),
        )

    async def synthesize(self, assessments: list[RepoAssessment]) -> str:
        return (
            "This developer demonstrates solid intermediate ability across a varied portfolio, "
            "with consistent project structure and a clear grasp of full-stack fundamentals. "
            "Their strongest work shows comfort with REST APIs, data modelling, and clean "
            "separation of concerns, though automated testing and CI are notably thin throughout. "
            "Overall they are a good candidate for a junior full-stack role, mainly because the "
            "breadth and consistency of working, well-documented projects outweigh the gaps."
        )


def get_provider() -> AIProvider:
    if settings.mock_ai:
        return MockProvider()
    return AnthropicProvider()
