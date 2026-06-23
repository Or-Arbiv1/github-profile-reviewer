# GitHub Profile Reviewer

Analyzes a public GitHub profile's repositories and produces an AI-written assessment.

## Setup

### Mac

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## Configure

Open `.env` and paste the two keys (`AI_API_KEY` and `GITHUB_TOKEN`) sent to you via the single-view link.

## Run

### Mac

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload
```

### Windows

```powershell
.venv\Scripts\activate
uvicorn backend.main:app --reload
```

Then open http://127.0.0.1:8000

Setup is only needed once. After that, just activate and run.

## How it works

- **Stack:** FastAPI backend (single `POST /analyze`) that also serves a static
  HTML + vanilla-JS frontend — one process, one command, no build step.
- **Flow:** fetches the user's public repos + READMEs (async; forks excluded,
  archived kept), caps at the 25 most recently active, runs one AI call per repo
  for a structured assessment (level, README clarity, complexity, summary), then a
  final call for an overall experience verdict.
- **AI:** Anthropic `claude-haiku-4-5`, structured output via a forced tool call,
  validated before render — off-schema output fails closed to a degraded card.
- **Errors:** every failure maps to a clean state (user-not-found, rate limit, bad
  key, upstream) — never a bare 500. One repo blipping degrades a single card;
  systemic errors abort fast.

## Approach

Built with Claude Code and Gemini for the design. The main challenge was latency —
~30 sequential AI calls were too slow, solved by fanning out the per-repo work with
bounded `asyncio` concurrency.
