# GitHub Profile Reviewer — Decisions (for planning)

## What it is
A tool that takes a GitHub username (or profile URL), fetches the user's public repos + READMEs, sends each to an AI for an assessment (level, README clarity, complexity, summary), and displays a short readable summary per repo.

## Product shape
- **Minimal local web app** — not a CLI, not a bare script. Opened in the browser, runs with one command. Chosen for the full-stack signal the role wants; kept deliberately small per the brief's "keep it simple."

## Stack
- **Backend:** Python + FastAPI. One `POST /analyze` endpoint; also serves the frontend page (one process, one command).
- **Frontend:** Static HTML + Tailwind (CDN) + vanilla JS. No React, no Node, no build step. UI designed via the `ui-ux-pro-max` skill.
- **Run path:** `cp .env.example .env` → paste key → `pip install -r requirements.txt` → `uvicorn` → open localhost.
- **TODO — run scripts:** ship a one-command setup+run script for both platforms so the reviewer doesn't assemble the steps manually: `run.ps1` (Windows/PowerShell) and `run.sh` (macOS/Linux). Each should: create the venv, install `requirements.txt`, copy `.env.example` → `.env` if missing (then stop and tell the user to paste the two keys), and launch `uvicorn`. The keys themselves are still pasted into `.env` by hand — the script never contains secrets. Document both in the README quick-start.

## GitHub data
- Fetch repos via `GET /users/{username}/repos`, then READMEs per repo, using **httpx, async**.
- **Filter out forks** (someone else's code, not the user's own work). **Keep archived repos** — they *are* the user's own work, just frozen/read-only, so they're analyzed like any repo and tagged "Archived" in the UI.
- The response carries a full inventory derived from the same list call (no extra request): `total_found`, `forks_excluded`, and `archived_total`. The UI shows it as one line — e.g. *"43 public repos · 23 archived · 3 forks — analyzed the 25 most recently updated"* — and the empty state reuses it as *"Found N repos, all forks — no original work to analyze"*.
- **Cap at ~20–30 repos**, sorted by `pushed_at` descending (most recent activity first).
- **`GITHUB_TOKEN` support is a nice-to-have / optional:** if the env var is present, add an `Authorization` header; otherwise run unauthenticated. This is ~3 lines, not an auth system. Without it: 60 req/hr; with it: 5,000 req/hr. Documented in README as recommended.

## Card display order (frontend)
- **Selection vs. display are separate concerns.** The backend *picks* which repos to analyze by `pushed_at` desc (most recent activity). The frontend *orders the cards* differently — recency isn't what a reviewer scans for.
- **Sort cards by stars desc, with level (complexity) as the tiebreaker.** Stars are the clearest social-proof signal, so the most-starred work leads; within an equal star count, the stronger project ranks higher (Advanced → Intermediate → Basic). This means every starred repo sorts above the zero-star ones, and the zero-star repos fall through to a clean Advanced→Intermediate→Basic order among themselves.
- **Level is the proxy for "complexity" in the sort.** The `complexity` field is free-text (no inherent order), so the ordinal `level` enum is what's actually compared. Unrated/degraded cards (`level=None`) sink to the bottom.
- Implemented client-side in `app.js` (`byStarsThenComplexity`); JS sort is stable, so ties beyond both keys keep the backend's recency order.

## Concurrency (the latency fix)
- Run per-repo work (README fetch + AI call) in parallel via `asyncio.gather` wrapped in an `asyncio.Semaphore(6)` to avoid hammering rate limits.
- **Single blocking `POST /analyze` that returns all results at once** — parallelism brings ~30 repos down to ~15s, which is fine behind one good loading state ("Analyzing repos…" — the count isn't known client-side until the single blocking response returns). **No progressive/streaming frontend** — it was polish, not substance, and the single endpoint is simpler.

## AI
- **Anthropic SDK**, model `claude-haiku-4-5` (cheap + fast, well-suited to "read a README, judge it"). Pin to a dated ID and make it configurable via env so it can be bumped.
- **Structured JSON output** with fixed fields: `level` (Basic / Intermediate / Advanced enum), `readme_clarity`, `complexity`, `assessment`. Renders cleanly as badges/cards and is the core "smart use of AI" signal.
- **`readme_clarity` is a 5-value enum** (not free-form): `Missing` (no README), `Trivial` (auto-generated/GitHub template only), `Sparse` (some real content but very thin), `Adequate` (covers basics but not polished), `Clear` (well-written and informative). Chosen to give the AI clear, distinct buckets rather than a vague scale.
- **`complexity` is capped at 3 words** — free-form to allow descriptive variety, but word-limited to keep card UI consistent. Leads with a complexity descriptor and always ends in "project" so it reads on its own (e.g. "Simple CRUD project", "Complex full-stack project").
- **`assessment` is capped at 30 words** — enough for a verdict on the repo as a junior's portfolio piece (how impressive/routine, a note on README clarity, the main takeaway) without overflowing the card.
- **`summary` field added** — AI generates an 8-word-max description of what the project actually *is* (e.g. "REST API for tracking personal expenses"), separate from `assessment` which is about what it reveals about the developer. Displayed directly under the repo name on the card.
- **One AI call per repo.**
- **Feed repo metadata alongside the README** — `language`, `description`, `topics`, `stargazers_count`, `size`, `pushed_at` (all already returned by the repos-list call, zero extra requests). Benefits: (a) the no-README case still yields a real assessment instead of a dead card — it collapses into the normal flow as just an empty README field; (b) less noisy level judgments.
- **Truncate README input to ~12KB** before the prompt. Front-loaded content (title, setup, usage) is preserved; only oversized appendices/changelogs get clipped. A cap must exist so one pathological README can't blow the token budget. Bump higher later if needed.
- **Verify at build time** (not now): the exact pinned model ID and the current Anthropic SDK structured-output API shape — check against the `claude-api` reference rather than from memory.
- **Structured output is a forced tool call:** `assess_repo` pins `tool_choice` to a single
  `submit_assessment` tool whose `input_schema` is the four fields, then validates the returned
  `.input` through a private pydantic model before building the `RepoAssessment` — off-schema
  output fails closed to a degraded card rather than a malformed response.
- **Temporary dev scaffolding — `MOCK_AI` + `MockProvider`:** so the app runs end-to-end with
  no key while the real path is unverified, `get_provider()` returns a `MockProvider` (randomized
  canned assessments) when `MOCK_AI=true`. `ai_api_key` is correspondingly `str | None` for now.
  Both are flagged for removal once the live API is wired (see `TODO.md`); not a permanent decision.

## Error handling (explicitly graded — designed, not afterthought)
Each maps to a clean state, never a crash:
- **User not found (404)** → clean "user not found" message.
- **Invalid username / profile URL** → normalized (strip `@`, trailing `/`, a full
  `https://github.com/<name>` URL) then validated against GitHub's username rule before any API
  call. Anything impossible collapses into the same "user not found" state — and never reaches
  the GitHub path (no injection / SSRF via the URL).
- **GitHub rate limit (403 + `X-RateLimit-Remaining: 0`)** → distinct state: "GitHub rate limit reached — add a GITHUB_TOKEN (see README)." Separate from user-not-found.
- **GitHub unreachable (DNS / connection / timeout on the repos list)** → clean `upstream` state, never an unhandled 500.
- **Repo with no README** → handled via the metadata-augmented prompt; produces an assessment, doesn't throw.
- **Missing / invalid `AI_API_KEY` (401/auth error)** → clear in-app state: "API key invalid or expired — set a valid AI_API_KEY in .env (see README)."
- **Last-resort safety net** → a catch-all handler maps anything unmapped to the same uniform
  `upstream` error body (detail logged server-side). No path returns a bare 500 / stack trace.

### Partial failure: degraded cards vs. systemic abort (added during build)
The fan-out runs with `return_exceptions=True`, then splits failures two ways:
- **Per-repo, transient** (one repo's AI call blips) → that repo becomes a **degraded card**:
  we keep its GitHub metadata, render it "Unrated" (`level=None`) with a "couldn't assess" note,
  and keep going. It stays in order; it doesn't sink the request.
- **Systemic** (`ai_auth`, `github_rate_limit`) → these would hit *every* repo identically, so
  one actionable message beats N broken cards: they abort the whole request fast.
- **Every repo failed (none rated)** → surfaced as a real `upstream` error, not a friendly
  empty state (an empty grid with no synthesis would be misleading).

The response reports this honestly: `repo_count` = repos successfully rated, `failed_count` =
degraded ones. Only rated repos feed the synthesis call. (Note: a rate-limit *during synthesis*
still errors the whole page — generic-by-design; revisit only if seen in real runs.)

## Secrets / key handling
- Key never in the repo. Code reads `AI_API_KEY` from `.env` (gitignored); repo ships only `.env.example` with blank names.
- **I supply a working key**, delivered out of band via a onetimesecret.com single-view link, with an email note that it's single-view and I'll resend if it expires. Spend cap set with headroom for several full runs; key rotated/disabled after review.

## Tests
- Small, targeted set (one file, `pytest`) focused on the graded error paths, **not** a full suite. Mock the GitHub and Anthropic HTTP calls (no network, no key burn):
  - user not found → clean 404 state
  - repo with no README → produces assessment, doesn't throw
  - GitHub rate limit (403) → distinct state
  - missing/invalid `AI_API_KEY` → clear failure, not a 500
- Don't heavily test the happy-path AI output shape (model's job, will drift). Test *our* error handling, which is what's graded.

## Optional extra (do last, only if time allows)
After all per-repo cards resolve, one final AI call takes the collection of levels + one-line summaries (not full READMEs — cheap) and returns a **overall synthesis** rendered as a top banner. Directly answers the brief's "what level of experience it reflects." ~1 call, ~20 lines. Treat as the final touch after the core flow works end-to-end.
- **Synthesis structure:** 2–3 sentences on overall level, strongest skills, and notable gaps — followed by a final sentence with a clear verdict on whether the developer is a good candidate for a junior full-stack role and why. Chosen because the tool is meant to help assess developers, so a hiring signal is the natural output.
- **The candidate verdict is mandatory, not optional.** The prompt phrases it as a hard requirement ("you MUST end with a clear verdict sentence … Always give the verdict — never omit it") rather than a soft "add one final sentence," so the synthesis reliably ends with the hiring signal instead of trailing off after the gaps.
- **`max_tokens=400` for synthesis** (up from 256). Realistic output is ~4 sentences (~150–210 tokens); 400 is ~2x headroom so the verdict — the most important part, and the last thing generated — can never be truncated mid-sentence. The cap only guards against clipping; output length stays ~4 sentences because the prompt, not the token budget, governs it.

## Deliverables
- GitHub repo with code + README (run instructions, the "Decisions" section, and a short "how I approached it / which AI tools I used / main challenge" note).
