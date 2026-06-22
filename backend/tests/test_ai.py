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


def test_synthesize_returns_text():
    provider = _provider(_FakeMessages(response=_Response([_Block("text", text="Overall: strong.")])))
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


def test_synthesize_no_text_block_is_upstream():
    provider = _provider(_FakeMessages(response=_Response([_Block("tool_use", input={})])))
    with pytest.raises(AnalyzeError) as exc:
        asyncio.run(provider.synthesize([]))
    assert exc.value.code == "upstream"
