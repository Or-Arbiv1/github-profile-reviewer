import logging
import anthropic
from contextlib import contextmanager
from typing import Literal, Protocol
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


class _Synthesis(BaseModel):
    verdict: Literal["Yes", "Yes, with reservations", "No"]
    assessment: str


class AIProvider(Protocol):
    async def assess_repo(self, repo: dict, readme: str) -> RepoAssessment: ...
    async def synthesize(self, assessments: list[RepoAssessment]) -> str: ...


@contextmanager
def _map_ai_errors(label: str):
    """Translate Anthropic SDK exceptions into the app's AnalyzeError taxonomy in ONE place,
    so assess_repo and synthesize stay in lockstep. `label` only colours the server-side log
    for the unexpected case; the client never sees SDK internals. Systemic codes (ai_auth,
    ai_access, ai_model, ai_rate_limit) abort the whole run via _SYSTEMIC in analyzer.py."""
    try:
        yield
    except anthropic.AuthenticationError:
        raise AnalyzeError("ai_auth", 502, "AI API key is invalid or expired.")
    except anthropic.PermissionDeniedError:
        # 403: the key is valid but the request is refused — for a key provisioned for this
        # review, the overwhelmingly likely cause is an exhausted spend cap. Systemic.
        raise AnalyzeError(
            "ai_access", 502, "AI request denied — most likely the key's spend cap has been reached."
        )
    except anthropic.NotFoundError:
        # 404 from messages.create means the model id doesn't exist (bad AI_MODEL). Systemic:
        # it fails every repo identically, so surface the one fix, not N cards.
        raise AnalyzeError(
            "ai_model", 502, f"AI model '{settings.ai_model}' not found. Check AI_MODEL in .env."
        )
    except anthropic.RateLimitError:
        # 429: the org's per-minute token/request limit is saturated. Systemic — every
        # in-flight call hits it the same way, so abort fast with one actionable fix
        # instead of degrading all N cards into cryptic 'upstream' failures.
        raise AnalyzeError(
            "ai_rate_limit",
            429,
            "Anthropic rate limit reached — too many repositories analyzed at once. "
            "Wait a minute and retry, or lower CONCURRENCY / MAX_REPOS in .env.",
        )
    except AnalyzeError:
        raise  # already mapped — don't re-wrap as a generic upstream error
    except Exception:
        # Log the real detail server-side; never leak SDK internals to the client.
        logger.exception("AI call failed (%s)", label)
        raise AnalyzeError("upstream", 502, "The AI service failed to respond.")


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

        with _map_ai_errors(f"assess_repo for {repo.get('name')!r}"):
            response = await self._client.messages.create(
                model=settings.ai_model,
                max_tokens=1024,
                temperature=0,  # classification must be stable run-to-run, not sampled
                tools=[_tool],
                tool_choice={"type": "tool", "name": "submit_assessment"},
                messages=[{"role": "user", "content": prompt}],
            )

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

The question is ONE thing: is this developer a good fit for a junior FULL-STACK role, judged ONLY by the repos above? Answer it by a fixed procedure so the same evidence always yields the same verdict.

STEP 1 — From the evidence above, decide three yes/no facts. Judge only by what the repos actually show:
- BACKEND: does any repo show server-side code — an HTTP API or web server, routes/endpoints, request handling, or services that talk to a database or external API? A standalone script, a coding exercise or algorithm, a CLI utility, a desktop app's internal logic, or a game's internal logic does NOT count on its own.
- FRONTEND: does any repo build a real user interface using web or application UI technology — HTML/CSS/JS or a frontend framework (e.g. React, Vue), or a desktop GUI framework (e.g. WPF, WinForms, Qt) — with interface elements like pages, forms, navigation, data display, or components/state? Console/terminal text I/O, or a bare game render loop with none of those interface elements, does NOT count on its own.
- DATA: does any repo show stored, modelled data — a database, or CRUD over real data?
Work in an unrelated specialty (security, reverse engineering, low-level/systems, ML) does NOT by itself count as BACKEND or FRONTEND.

STEP 2 — Apply this rule exactly, in order, and stop at the first match:
1. If NO repo shows BACKEND, or NO repo shows FRONTEND → verdict "No". The reason is the missing side.
2. Else if BACKEND and FRONTEND appear only in small or basic projects → verdict "Yes, with reservations". Name the weaker side.
3. Else (both present, at least one shown with real depth) → verdict "Yes". If no repo shows DATA, note that as the single reservation but still answer "Yes".

Do STEP 1 and STEP 2 as silent reasoning — do NOT write them out. Then call submit_synthesis with two fields:
- verdict: the result of STEP 2 — exactly one of "Yes", "Yes, with reservations", or "No".
- assessment: the write-up only — maximum 80 words, flowing prose, no headings or lists, and do NOT walk through the repos one by one. Do NOT include the steps, the three facts, or the rule. In order: one sentence on the developer's overall level and strongest demonstrated skills; one sentence naming their single strongest project and the concrete capability that makes it stand out (cite it from that repo's verdict line, not generic praise); a final sentence that begins with exactly the verdict word above, says it is for a junior full-stack role, gives the one main reason, and states only the one reservation the rule identified (do not invent other gaps).

Two examples of the assessment field for tone and decisiveness only — fictional developers, do not copy their content:
"Solid intermediate work across a small CRUD web app and a few utilities. The standout pairs a REST API with a React frontend over a real database, showing the developer can build and wire a full stack end to end. Yes, a good fit for a junior full-stack role — both sides of the stack are clearly working together."
"Advanced, specialised work: the strongest repo shows deep low-level systems skill and unusually clear technical writing. But across every repo there is no web or desktop UI and no server or API. No, not a fit for a junior full-stack role — the portfolio shows no frontend or backend, however impressive the systems depth." """

        _tool = {
            "name": "submit_synthesis",
            "description": "Submit the overall full-stack verdict and assessment for a developer.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["Yes", "Yes, with reservations", "No"]},
                    "assessment": {"type": "string"},
                },
                "required": ["verdict", "assessment"],
            },
        }

        with _map_ai_errors("synthesize"):
            response = await self._client.messages.create(
                model=settings.ai_model,
                max_tokens=600,  # comfortable margin so a ~4-sentence verdict is never cut mid-sentence
                temperature=0,  # the verdict is a conclusion, not creative copy — keep it stable
                tools=[_tool],
                # Force the tool so the model returns ONLY the assessment field — its STEP 1/2
                # reasoning can't leak into the rendered text the way a free-form reply would.
                tool_choice={"type": "tool", "name": "submit_synthesis"},
                messages=[{"role": "user", "content": prompt}],
            )

        tool_block = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
        if tool_block is None:
            raise AnalyzeError("upstream", 502, "The AI service returned no synthesis.")
        try:
            synthesis = _Synthesis(**tool_block.input)
        except (ValidationError, TypeError):
            logger.warning("Malformed AI synthesis output: %s", tool_block.input)
            raise AnalyzeError("upstream", 502, "The AI service returned malformed output.")
        return synthesis.assessment


def get_provider() -> AIProvider:
    # The one place the provider is wired. The rest of the app depends only on the AIProvider interface, which is also
    # the seam the tests patch to inject a fake provider and run offline.
    return AnthropicProvider()
