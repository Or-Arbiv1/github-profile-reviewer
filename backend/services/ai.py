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
  - "Basic": scripts, tutorial follow-alongs, or single-file exercises; little structure and no real architecture
  - "Intermediate": a single working application with sensible structure (e.g. one full-stack app, a CRUD API, a CLI with real features); demonstrates competence but stays within one well-trodden pattern
  - "Advanced": integrates multiple services or non-trivial concerns — concurrency control, structured AI/tool calling, production handling (error paths, retries, rate limits), or thoughtful architecture beyond a single straightforward app
  - Judge the strongest evidence in the repo; do NOT inflate for stars or a polished README alone
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
                temperature=0,  # classification must be stable run-to-run, not sampled
                tools=[_tool],
                tool_choice={"type": "tool", "name": "submit_assessment"},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            raise AnalyzeError("ai_auth", 502, "AI API key is invalid or expired.")
        except anthropic.PermissionDeniedError:
            # 403: the key is valid but the request is refused — for a key provisioned for this
            # review, the overwhelmingly likely cause is an exhausted spend cap. Systemic.
            raise AnalyzeError(
                "ai_access", 502, "AI request denied — most likely the key's spend cap has been reached."
            )
        except anthropic.NotFoundError:
            # 404 from messages.create means the model id doesn't exist (bad AI_MODEL).
            # Systemic: it fails every repo identically, so surface the one fix, not N cards.
            raise AnalyzeError(
                "ai_model", 502, f"AI model '{settings.ai_model}' not found. Check AI_MODEL in .env."
            )
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
            f"- {a.name} — {a.language or 'unknown language'}, "
            f"level: {a.level.value if a.level else 'Unrated'}, "
            f"README: {a.readme_clarity}, {a.complexity}\n"
            f"  What it is: {a.summary or 'n/a'}\n"
            f"  Verdict: {a.assessment}"
            for a in assessments
        )
        prompt = f"""You have assessed {len(assessments)} GitHub repositories for a developer. The list below is the ONLY evidence you have about this developer — every claim you make must be supported by it.

{lines}

Write a SHORT overall assessment — maximum 80 words, flowing prose. No headings, no bullet lists, and do NOT walk through the repos one by one. Cover, in order:
- The developer's overall level and strongest demonstrated skills, in general terms. Name repos sparingly — only when one genuinely illustrates a point — never as a roster.
- Their single strongest project: name it and say what makes it stand out, citing the concrete capabilities from that repo's verdict line (e.g. "async concurrency, structured tool calling, latency optimization") rather than generic praise like "production-grade thinking".
- A clear closing verdict. It MUST begin with "Yes" or "No" (or "Yes, with reservations"), state plainly whether this developer is a good candidate for a junior full-stack developer role, and give the single main reason. Always give the verdict, do not bury it behind a condition, and never omit it.

You may note at most one gap, and only if the evidence above genuinely shows it. Before naming any gap, scan every repo line above: if ANY repo's verdict demonstrates that skill, you may NOT name it as a gap (e.g. do not call testing a gap if any repo shows test automation). Do NOT list generic junior-developer weaknesses such as testing, databases, deployment, or CI unless you can point to a specific repo line as evidence; if you can't, omit the gap entirely. Keep every claim supported by the evidence above. Be specific, direct, and stay within 80 words.

Good example of the tone, decisiveness, and grounding to aim for (do not copy its content — it describes a different developer):
"This developer shows solid intermediate competence across desktop apps, game logic, and test automation. Their standout project is data-pipeline-cli, which demonstrates genuine depth: async concurrency, structured tool calling, production error handling, and latency optimization well beyond typical junior work. Yes, a strong candidate for junior full-stack roles — the mix of architectural thinking, problem-solving across domains, and one genuinely sophisticated project signals readiness for real-world development." """

        try:
            response = await self._client.messages.create(
                model=settings.ai_model,
                max_tokens=600,  # comfortable margin so a ~4-sentence verdict is never cut mid-sentence
                temperature=0,  # the verdict is a conclusion, not creative copy — keep it stable
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            raise AnalyzeError("ai_auth", 502, "AI API key is invalid or expired.")
        except anthropic.PermissionDeniedError:
            raise AnalyzeError(
                "ai_access", 502, "AI request denied — most likely the key's spend cap has been reached."
            )
        except anthropic.NotFoundError:
            raise AnalyzeError(
                "ai_model", 502, f"AI model '{settings.ai_model}' not found. Check AI_MODEL in .env."
            )
        except Exception:
            logger.exception("AI synthesize call failed")
            raise AnalyzeError("upstream", 502, "The AI service failed to respond.")

        block = next((b for b in response.content if getattr(b, "type", None) == "text"), None)
        if block is None:
            raise AnalyzeError("upstream", 502, "The AI service returned no synthesis.")
        return block.text


def get_provider() -> AIProvider:
    # The one place the provider is wired. The rest of the app depends only on the AIProvider interface, which is also
    # the seam the tests patch to inject a fake provider and run offline.
    return AnthropicProvider()
