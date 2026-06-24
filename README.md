# GitHub Profile Reviewer

Give it a GitHub username (or full profile URL) and it fetches that user's public
repositories, reads each README, and asks an AI to assess every project — its
level (Basic, Intermediate, or Advanced), README clarity, and complexity — then
writes a one-paragraph verdict on the developer's overall experience level. Everything renders as a clean grid of
cards behind a single page.

```
Repo: project-name                                       ★ 14
Level: Advanced  ·  Python  ·  Complex concurrent project
What it is: Async CLI for batch-processing CSV exports
README: Clear
Verdict: Impressive for a junior portfolio — async concurrency and structured
error handling go well beyond tutorial work; the README is clear with real
setup steps. Reflects genuine comfort building non-trivial tools.

────────────────────────────────────────────────────────────────────────────
Overall: Solid intermediate developer with one genuinely advanced project…
Yes — a strong candidate for a junior full-stack role, mainly because the
breadth and one sophisticated project signal real readiness.
```

---

## Setup and run

You'll need **Python 3.12.6** to run this project.

```bash
git clone https://github.com/Or-Arbiv1/github-profile-reviewer.git
cd github-profile-reviewer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your keys from the OneTimeSecret link, and save (see configure section below)
uvicorn backend.main:app --reload
```

Then open **http://127.0.0.1:8000** and enter a username.

### Configure `.env`

Open `.env` and fill in two values:

- **`AI_API_KEY`** *(required)* — paste the Anthropic key from your OneTimeSecret
  link. → `AI_API_KEY=sk-ant-...`
- **`GITHUB_TOKEN`** *(recommended)* — paste a GitHub token, or you may hit the
  60 requests/hr limit. → `GITHUB_TOKEN=ghp_...` 

> ⚠️ **Save the file after editing — the app won't work without `AI_API_KEY`.**

The other settings (`AI_MODEL`, `MAX_REPOS`, `README_MAX_CHARS`, `CONCURRENCY`)
have sensible defaults — leave them unless you want to tune behaviour.

Setup is one-time. After that, just activate the venv and run `uvicorn` again.

---

## How I used AI

This splits into two parts: **AI inside the product** (the design that matters
most) and **AI to build it**.

### AI inside the product — the design decisions

The whole point of the tool is one AI judgement per repo plus a final synthesis.
Making that *reliable* and *useful* — rather than just "call a model and print
the text" — is where the real work went.

- **Structured output via a forced tool call, not free-form text.** `assess_repo`
  pins `tool_choice` to a single `submit_assessment` tool whose schema *is* the
  four card fields, so the model is forced to return the exact JSON shape. The
  result is then validated through a private Pydantic model (`_AssessedFields`)
  before anything renders — **if the model ever returns off-schema output (e.g. a
  level outside the enum), the repo fails closed to a "degraded card" instead of
  corrupting the response.** (`backend/services/ai.py`)

- **`temperature=0` on both calls — for a concrete reason.** The same profile run
  twice was flipping a repo between *Advanced* and *Intermediate* and rewording the
  synthesis. These are classifications, not creative copy, so both calls are pinned
  to `0`. The synthesis verdict is the headline hire/no-hire signal — it must not
  wander between runs.

- **Constrained, bucketed fields instead of vague scales.** `level` is a 3-value
  enum (Basic / Intermediate / Advanced) with an **explicit rubric** for each
  bucket — temperature alone wasn't enough, the model needed defined boundaries to
  lock onto, plus an instruction not to inflate for stars or a pretty README.
  `readme_clarity` is a 5-value enum (Missing → Trivial → Sparse → Adequate →
  Clear). `summary` (≤8 words, *what it is*), `complexity` (≤3 words), and
  `assessment` (≤30 words, *the verdict*) are word-capped so cards stay consistent.

- **Metadata-augmented prompt so the no-README case still works.** Each call gets
  the repo's language, description, topics, star count, size, and push date
  alongside the (truncated) README — all already returned by the repos-list call,
  zero extra requests. A repo with no README collapses into the normal flow as an
  empty field and **still gets a real assessment** instead of a dead card.

- **README truncated to ~12 KB** before the prompt — front-loaded content (title,
  setup, usage) is kept; only oversized appendices get clipped, so one pathological
  README can't blow the token budget.

- **Two-tier design: one call per repo, then one synthesis call.** After all cards
  resolve, a final call sees only the *rated* repos (name + level + one-line
  verdict — cheap, no READMEs) and produces the overall banner. Its prompt forces a
  closing verdict that begins with "Yes"/"No" so the hiring signal is always there,
  never trailing off after a list of gaps.

### AI to build it

- **Careful planning before code — the most important part.** The workflow was
  `DECISIONS.md` → `PLAN.md` → writing the code → writing the tests: product
  decisions were settled first, turned into a detailed build spec, then
  implemented against it. A `TODO.md` tracked later tasks so nothing got forgotten.
- **Implemented with Claude Code**, against that plan.

---

## Architecture

One process, one command. **FastAPI** serves a single `POST /analyze` endpoint
**and** the static frontend.

```
Browser ──POST /analyze {username}──► FastAPI ──► analyzer.analyze_user()
                                                    │
                                  github.list_repos()   (1 call: repos + counts)
                                  filter forks · keep archived · sort by recency · cap 25
                                                    │
                                  asyncio.gather(  ──► per repo: get_readme() + ai.assess_repo()
                                    Semaphore(6) )       (bounded fan-out — the latency fix)
                                                    │
                                  ai.synthesize(rated repos)   (1 final call)
                                                    │
                                  ◄── { synthesis, repos[], inventory counts }
```

| Layer | File | Responsibility |
|-------|------|----------------|
| HTTP edge | `backend/routers/analyze.py` | Normalize + validate the username, delegate. HTTP only. |
| Orchestration | `backend/services/analyzer.py` | The whole pipeline: fan-out, degraded cards, synthesis. |
| GitHub client | `backend/services/github.py` | Async `httpx`, one pooled client per process. |
| AI provider | `backend/services/ai.py` | `AIProvider` interface + Anthropic implementation. |
| Contracts | `backend/models.py`, `errors.py` | Pydantic shapes + the typed error taxonomy. |
| Frontend | `frontend/index.html`, `app.js` | One `state` object, pure render functions, no framework. |

**The provider is wired in one place** (`get_provider()`), and the rest of the app
depends only on the `AIProvider` interface — never a concrete class. Switching to a
different Anthropic model is just the `AI_MODEL` env var. That same single seam is
what lets the test suite inject a fake provider and run fully offline, no key spent.

---

## Key decisions

- **A local web app, not a CLI or script.** The role is full-stack, so a small
  end-to-end web app shows more than a script would — while staying deliberately
  minimal per the brief's "simple that works."
- **Forks excluded, archived kept.** A fork is someone else's code; archived repos
  are still the user's own work, just frozen — so they're analyzed like any repo
  and tagged "Archived" in the UI.
- **Selection by recency, display by stars.** The backend *selects* the 25 most
  recently pushed repos (most representative of current ability). The frontend
  *orders the cards* by stars (clearest social-proof signal), with level as the
  tiebreaker. Two different concerns, two different sorts — by design.
- **Single blocking endpoint.** `/analyze` returns everything at once behind one
  loading state — no streaming UI, which was polish, not substance. (The fan-out
  that makes that wait short is in the diagram above.)
- **Concurrency is bounded** so a large profile can't hammer the GitHub or
  Anthropic rate limits.

---

## Error handling

Errors are designed states, not stack traces. Every failure maps to a typed
`AnalyzeError` → a uniform JSON body → a specific frontend message.

| Failure | Result |
|---------|--------|
| User doesn't exist (GitHub 404) | `user_not_found` → "check the spelling" |
| Malformed username / profile URL | Normalized + validated → same clean state, never reaches the API |
| GitHub rate limit (403/429) | `github_rate_limit` → "add a GITHUB_TOKEN" |
| Repo with no README | Handled by the metadata prompt — a normal card, no error |
| **One** repo's AI call blips | **Degraded card** (`level=None`, "Unrated") — the rest still render |
| **Every** repo fails | `upstream` 502 — not a misleading empty state |
| Invalid AI key / spend cap / bad model id | `ai_auth` / `ai_access` / `ai_model` — one actionable fix |

The key design choice here is **degraded cards vs. systemic abort.** A single
repo's transient failure degrades just that one card and the request continues. A
*systemic* failure (bad key, exhausted rate limit, wrong model) would hit every
repo identically, so it aborts fast with the one message that fixes it — rather
than rendering 25 broken cards.

---

## Security

For a tool that takes untrusted input and renders AI output, the basics are
handled deliberately:

- **No SSRF / path injection.** The username is normalized and validated against
  GitHub's own username rule *before* it touches any API path. Anything that can't
  be a real username collapses to `user_not_found` and never reaches GitHub.
  (`backend/routers/analyze.py`)
- **No XSS.** The frontend builds every node with `createTextNode` / `textContent`
  — never `innerHTML`. Repo names and AI-generated text are untrusted and cannot
  inject markup. (`frontend/app.js`)
- **Secrets stay out of the repo.** Keys are read from `.env` (gitignored); only
  `.env.example` with blank fields ships. The key is never logged.

---

## Known limitations & tradeoffs

Called out deliberately so they read as choices, not oversights:

- **Tailwind + Google Fonts load from CDNs at runtime** — a deliberate no-build
  tradeoff to keep the app one command with no toolchain. It needs internet at
  runtime, and the Tailwind CDN prints a "not for production" console warning. Fine
  for a local single-user tool; a production build would vendor both.
- **Repo count caps at 100 for prolific users.** The repos list fetches a single
  100-item page, so the displayed total tops out at 100. The *analyzed* set is
  still the genuinely 25 most-recently-pushed, so the assessment is unaffected —
  only the headline count is capped. Pagination was scoped out as unnecessary
  complexity for the brief.
- **No auth or rate limiting on `/analyze`.** One request fans out to ~26 AI calls.
  That's intentional for a local, single-user tool; a hosted version would need a
  rate limit and a cost cap.

---

## Run the tests

`python -m pytest` — 58 tests across `backend/tests/`, fully offline. They target
**our error handling**, not the model's output.

