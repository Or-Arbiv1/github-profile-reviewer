import asyncio
import anthropic
import pytest
from backend.errors import AnalyzeError
from backend.models import Level, RepoAssessment
from backend.services.ai import AnthropicProvider


class _Block:
    def __init__(self, type, input=None, text=None):
        self.type = type
        self.input = input
        self.text = text


class _Response:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    """Stand-in for client.messages — returns a canned response or raises a canned error."""
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


def _provider(messages):
    # Bypass __init__ (which would build a real SDK client) and inject our fake.
    p = AnthropicProvider.__new__(AnthropicProvider)
    p._client = type("C", (), {"messages": messages})()
    return p


# anthropic.AuthenticationError's constructor needs an httpx response/body; build a bare
# instance so tests can raise the exact type the provider catches.
def _auth_error():
    return anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)


# A bad model id makes messages.create raise NotFoundError (404). Same bypass trick as above.
def _not_found_error():
    return anthropic.NotFoundError.__new__(anthropic.NotFoundError)


# Saturated per-minute token/request limit -> RateLimitError (429). Same bypass trick.
def _rate_limit_error():
    return anthropic.RateLimitError.__new__(anthropic.RateLimitError)


REPO = {
    "name": "demo",
    "html_url": "https://github.com/u/demo",
    "language": "Python",
    "stargazers_count": 4,
    "archived": False,
}


def test_assess_repo_parses_tool_output(monkeypatch):
    block = _Block("tool_use", input={
        "summary": "Python demo project.", "level": "Advanced", "readme_clarity": "Clear",
        "complexity": "Non-trivial", "assessment": "Solid work.",
    })
    provider = _provider(_FakeMessages(response=_Response([block])))
    result = asyncio.run(provider.assess_repo(REPO, "a readme"))
    assert isinstance(result, RepoAssessment)
    assert result.level == Level.advanced
    assert result.name == "demo"
    assert result.language == "Python"


def test_assess_repo_no_tool_block_is_upstream():
    provider = _provider(_FakeMessages(response=_Response([_Block("text", text="hi")])))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.assess_repo(REPO, ""))
    assert exc.value.code == "upstream"


def test_assess_repo_malformed_output_is_upstream():
    # level outside the enum → schema validation fails → mapped to upstream.
    block = _Block("tool_use", input={
        "summary": "something", "level": "Wizard", "readme_clarity": "Clear",
        "complexity": "x", "assessment": "y",
    })
    provider = _provider(_FakeMessages(response=_Response([block])))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.assess_repo(REPO, ""))
    assert exc.value.code == "upstream"


def test_assess_repo_auth_error_is_ai_auth():
    provider = _provider(_FakeMessages(error=_auth_error()))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.assess_repo(REPO, ""))
    assert exc.value.code == "ai_auth"


def test_assess_repo_generic_error_is_upstream():
    provider = _provider(_FakeMessages(error=RuntimeError("network blip")))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.assess_repo(REPO, ""))
    assert exc.value.code == "upstream"


def test_assess_repo_not_found_is_ai_model():
    # A bad AI_MODEL → 404 → distinct, actionable 'ai_model' state, not a generic upstream blip.
    provider = _provider(_FakeMessages(error=_not_found_error()))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.assess_repo(REPO, ""))
    assert exc.value.code == "ai_model"


def test_assess_repo_rate_limit_is_ai_rate_limit():
    # 429 → distinct 'ai_rate_limit', not a generic upstream blip that degrades every card.
    provider = _provider(_FakeMessages(error=_rate_limit_error()))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.assess_repo(REPO, ""))
    assert exc.value.code == "ai_rate_limit"
    assert exc.value.http_status == 429


def test_synthesize_returns_assessment_field():
    # synthesize now forces a tool call; we render only the `assessment` field, never the
    # model's STEP 1/2 reasoning.
    block = _Block("tool_use", input={"verdict": "No", "assessment": "Overall: strong."})
    provider = _provider(_FakeMessages(response=_Response([block])))
    out = asyncio.run(provider.synthesize([
        RepoAssessment(name="demo", url="", language="Python", stars=0,
                       level=Level.advanced, readme_clarity="Clear",
                       complexity="x", assessment="y"),
    ]))
    assert out == "Overall: strong."


def test_synthesize_auth_error_is_ai_auth():
    provider = _provider(_FakeMessages(error=_auth_error()))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.synthesize([]))
    assert exc.value.code == "ai_auth"


def test_synthesize_malformed_output_is_upstream():
    # Forced tool call but missing required fields → schema validation fails → upstream.
    provider = _provider(_FakeMessages(response=_Response([_Block("tool_use", input={})])))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.synthesize([]))
    assert exc.value.code == "upstream"


def test_synthesize_no_tool_block_is_upstream():
    provider = _provider(_FakeMessages(response=_Response([_Block("text", text="hi")])))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.synthesize([]))
    assert exc.value.code == "upstream"


def test_synthesize_not_found_is_ai_model():
    provider = _provider(_FakeMessages(error=_not_found_error()))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.synthesize([]))
    assert exc.value.code == "ai_model"


def test_synthesize_rate_limit_is_ai_rate_limit():
    provider = _provider(_FakeMessages(error=_rate_limit_error()))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.synthesize([]))
    assert exc.value.code == "ai_rate_limit"
