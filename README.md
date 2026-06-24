# GitHub Profile Reviewer

Give it a GitHub username (or full profile URL); it fetches the user's public
repos, reads each README, and asks an AI to assess every project — level (Basic /
Intermediate / Advanced), README clarity, and complexity — then writes a
one-paragraph verdict on the developer's overall experience. Everything renders as
a grid of cards behind a single page.

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

You'll need **Python 3.12.6** and internet connection. 

```bash
git clone https://github.com/Or-Arbiv1/github-profile-reviewer.git
cd github-profile-reviewer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your keys (see below) and save
uvicorn backend.main:app --reload
```

Then open **http://127.0.0.1:8000** and enter a username.

### Configure `.env`

- **`AI_API_KEY`** *(required)* — your Anthropic key. → `AI_API_KEY=sk-ant-...`
- **`GITHUB_TOKEN`** *(recommended)* — a GitHub token, or you may hit the 60
  requests/hr anonymous limit. → `GITHUB_TOKEN=ghp_...`

> ⚠️ **Save the file after editing — the app won't start without `AI_API_KEY`.**

`AI_MODEL`, `MAX_REPOS`, `README_MAX_CHARS`, and `CONCURRENCY` have sensible
defaults; leave them unless you want to tune behaviour. Setup is one-time — after
that, activate the venv and run `uvicorn` again.

---

## How I used AI

Two parts: **AI inside the product** (the design that matters) and **AI to build it**.

### AI inside the product

The whole tool is one AI judgement per repo plus a final synthesis. Making that
*reliable*, not just "call a model and print the text," is where the work went:

- **Structured output via a forced tool call.** `assess_repo` pins `tool_choice`
  to a single `submit_assessment` tool whose schema *is* the card fields, then
  validates the result through a Pydantic model — off-schema output fails closed to
  a "degraded card" instead of corrupting the response. (`backend/services/ai.py`)
- **`temperature=0` on both calls.** These are classifications, not creative copy;
  without it the same profile flipped repos between levels and reworded the verdict
  between runs. The synthesis verdict is the headline hire signal — it must not wander.
- **Bucketed fields with explicit rubrics, not vague scales.** `level` and
  `readme_clarity` are enums with a defined rubric per value; `summary`,
  `complexity`, and `assessment` are word-capped — so cards stay consistent and the
  model can't inflate for stars or a pretty README.
- **Metadata-augmented prompt.** Each call also gets the repo's language,
  description, topics, stars, size, and push date (already fetched, zero extra
  requests), so a repo with **no README** still gets a real assessment. READMEs are
  truncated to ~12 KB so one giant file can't blow the token budget.
- **Two-tier design, deterministic verdict.** After the per-repo cards resolve, one
  synthesis call sees only the *rated* repos (no READMEs) and answers one question —
  junior full-stack fit? — by a fixed rule over the evidence (backend? frontend?
  data?), returned through a forced tool call so the model's reasoning can't leak
  into the output. Same evidence in, same verdict out, run to run.

### AI to build it

- **Planned before coding:** `DECISIONS.md` → `PLAN.md` → code → tests, with a
  `TODO.md` tracking follow-ups. Decisions were settled first, then implemented
  against the spec — with **Claude Code**.

---

## Architecture

One process, one command: **FastAPI** serves a single `POST /analyze` endpoint
**and** the static frontend. A request fans out — `github.list_repos()` (forks
filtered, archived kept, sorted by recency, capped at 25), then a bounded
`asyncio.gather` of `get_readme()` + `assess_repo()` per repo, then one
`synthesize()` over the rated repos.

| Layer | File | Responsibility |
|-------|------|----------------|
| HTTP edge | `backend/routers/analyze.py` | Normalize + validate the username, delegate. HTTP only. |
| Orchestration | `backend/services/analyzer.py` | The pipeline: fan-out, degraded cards, synthesis. |
| GitHub client | `backend/services/github.py` | Async `httpx`, one pooled client per process. |
| AI provider | `backend/services/ai.py` | `AIProvider` interface + Anthropic implementation. |
| Contracts | `backend/models.py`, `errors.py` | Pydantic shapes + the typed error taxonomy. |
| Frontend | `frontend/index.html`, `app.js` | One `state` object, pure render functions, no framework. |

The provider is wired in one place (`get_provider()`); the rest of the app depends
only on the `AIProvider` interface. That single seam is what lets the test suite
inject a fake provider and run fully offline, no key spent.

---

## Key decisions

- **A local web app, not a CLI.** The role is full-stack, so a small end-to-end app
  shows more than a script — while staying minimal per the brief's "simple that works."
- **Forks excluded, archived kept.** A fork is someone else's code; archived repos
  are still the user's own work, just frozen — analyzed normally and tagged in the UI.
- **Selection by recency, display by stars.** The backend selects the 25
  most-recently-pushed repos (current ability); the frontend orders cards by stars
  (social proof), level as tiebreaker.
- **Single blocking endpoint** behind one loading state — no streaming UI, which was
  polish, not substance.
- **Bounded concurrency** to keep the GitHub and AI fan-out in check (a prolific
  profile can still reach the AI per-minute limit — see Known limitations).

---

## Error handling

Errors are designed states, not stack traces. Every failure maps to a typed
`AnalyzeError` → a uniform JSON body → a specific frontend message.

| Failure | Result |
|---------|--------|
| User doesn't exist (GitHub 404) | `user_not_found` → "check the spelling" |
| Malformed username / profile URL | Normalized + validated, never reaches the API |
| GitHub rate limit (403/429) | `github_rate_limit` → "add a GITHUB_TOKEN" |
| GitHub bad/expired token (401) | `github_auth` → "fix or remove GITHUB_TOKEN in .env" |
| Repo with no README | Handled by the metadata prompt — a normal card |
| **One** repo's AI call blips | **Degraded card** (`level=None`, "Unrated") — the rest render |
| **Every** repo fails | `upstream` 502 — not a misleading empty state |
| Invalid AI key / spend cap / bad model id | `ai_auth` / `ai_access` / `ai_model` |
| Anthropic rate limit (429) | `ai_rate_limit` → "retry, or lower CONCURRENCY / MAX_REPOS" |

The core choice is **degraded cards vs. systemic abort**: one repo's transient
failure degrades just that card and the run continues, while a systemic failure
(bad key, exhausted limit, wrong model) hits every repo identically — so it aborts
fast with the one message that fixes it, rather than rendering 25 broken cards.

---

## Security

- **No SSRF / path injection.** The username is validated against GitHub's username
  rule *before* it touches any API path. (`backend/routers/analyze.py`)
- **No XSS.** The frontend builds every node with `createTextNode` / `textContent`,
  never `innerHTML`, so untrusted repo names and AI text can't inject markup. (`frontend/app.js`)
- **Secrets stay out of the repo.** Keys load from `.env` (gitignored); only
  `.env.example` with blank fields ships, and the key is never logged.

---

## Known limitations & tradeoffs

Called out deliberately, so they read as choices, not oversights:

- **Tailwind + Google Fonts load from CDNs at runtime** — a no-build tradeoff to keep
  the app one command. Needs internet at runtime; a production build would vendor both.
- **Repo count caps at 100.** The list fetches one 100-item page, so the *displayed*
  total tops out at 100 — but the analyzed set is still the 25 most recent, so the
  assessment is unaffected. Pagination was scoped out.
- **No auth or rate limiting on `/analyze`.** One request fans out to ~26 AI calls —
  intentional for a local single-user tool; a hosted version would need a cost cap.
- **A prolific profile can hit the Anthropic per-minute limit on low API tiers**
  (tier 1 = 50k input tokens/min). The tool aborts cleanly with `ai_rate_limit`; to
  stay under it, lower the `.env` knobs — e.g. `CONCURRENCY=2`, `README_MAX_CHARS=6000`
  (and, if needed, `MAX_REPOS=10`).

---

## Run the tests

`python -m pytest` — 64 tests across `backend/tests/`, fully offline. They target
**our error handling**, not the model's output.

### Prompt consistency check

The model's *output* is tested separately, by hand — because the biggest risk in an
LLM-judged tool is the same input getting different answers on different runs. To
measure it, I spawned **30 independent agents** on **Claude Haiku 4.5** (the default
`AI_MODEL`) — **3 GitHub usernames × 10 isolated runs each**, every run blind to the
others (no shared context, so they can't copy one another) and fed identical frozen
repo data. The usernames spanned the decision space: a security/reverse-engineering
profile (clear "No"), a borderline full-stack profile, and a reports-plus-basic-games
profile.

What it showed:

- **The synthesis verdict — the headline hire signal — is stable.** It was
  unanimous on the clear-cut profile and held the majority answer on the borderline
  ones;
- **`level` started as the least consistent field**, with the noise concentrated on
  the **Basic ↔ Intermediate** boundary.
  That drove a sharpened rubric: Basic vs. Intermediate now turns on a single
  discriminator — *separation of responsibilities across multiple components* — with
  an explicit "when torn, pick the lower level" tie-break. With that in place, **every
  single-project repo now scores 100% `level` agreement across all 10 runs** —
  including the exact repos that used to flip. The only repo that still varies is one
  that *bundles many separate assignments*, where "separation of responsibilities" has
  no single answer — expected ambiguity, not noise.

