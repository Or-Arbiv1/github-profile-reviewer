// ─── State ───────────────────────────────────────────────────────────────────
const state = { status: 'idle', username: '', data: null, error: null };

// ─── Safe DOM builder ────────────────────────────────────────────────────────
// Strings become text nodes — never innerHTML — so user/AI content can't inject HTML.
function createEl(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'className') el.className = v;
    else el.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    if (typeof child === 'string') el.appendChild(document.createTextNode(child));
    else el.appendChild(child);
  }
  return el;
}

// ─── Network ─────────────────────────────────────────────────────────────────
async function analyze(username) {
  state.status = 'loading';
  state.username = username;
  state.data = null;
  state.error = null;
  render();

  try {
    const res = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username }),
    });
    const json = await res.json();

    if (!res.ok) {
      state.status = 'error';
      state.error = json.error ?? { code: 'upstream', message: 'Something went wrong.' };
    } else if (json.repos.length === 0) {
      state.status = 'empty';
      state.data = json;
    } else {
      state.status = 'success';
      state.data = json;
    }
  } catch {
    state.status = 'error';
    state.error = { code: 'upstream', message: 'Something went wrong reaching an upstream service. Try again.' };
  }

  render();
}

// ─── Render helpers ──────────────────────────────────────────────────────────
// Level is the card's rating, shown as a single-hue ramp: slate (Basic) → emerald (Advanced).
// `accent` colors the verdict's left border so the opinion is tied to the rating.
const LEVEL_STYLE = {
  Basic:        { chip: 'bg-slate-500/10 text-slate-400',     accent: 'border-slate-600' },
  Intermediate: { chip: 'bg-emerald-500/10 text-emerald-300', accent: 'border-emerald-500/30' },
  Advanced:     { chip: 'bg-emerald-500/20 text-emerald-200', accent: 'border-emerald-500/60' },
};
// No level == the AI call for this repo failed; show a muted "Unrated" reading.
const UNRATED_STYLE = { chip: 'bg-slate-700/50 text-slate-400', accent: 'border-slate-700' };

// Display order: most stars first (social proof leads), level as the tiebreaker among
// equal-star repos (unrated sinks last). The backend selects repos by recency — this is
// purely presentation.
const LEVEL_RANK = { Advanced: 3, Intermediate: 2, Basic: 1 };
function byStarsThenLevel(a, b) {
  if (a.stars !== b.stars) return b.stars - a.stars;
  return (LEVEL_RANK[b.level] ?? 0) - (LEVEL_RANK[a.level] ?? 0);
}

function levelChip(level) {
  const s = LEVEL_STYLE[level] ?? UNRATED_STYLE;
  return createEl('span', {
    className: `inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold uppercase tracking-wide ${s.chip}`,
  }, level ?? 'Unrated');
}

// Vertical card, top → bottom: title · level · language·complexity · summary · verdict.
// Structure reads through typography (size + brightness), not dividers or nested boxes.
function renderCard(repo) {
  const accent = (LEVEL_STYLE[repo.level] ?? UNRATED_STYLE).accent;

  const card = createEl('div', {
    className: 'bg-slate-800/60 border border-slate-700/80 rounded-xl p-5 flex flex-col gap-3 '
      + 'hover:border-slate-600 transition-colors duration-200'
      + (repo.archived ? ' opacity-75' : ''),  // frozen work visibly recedes without hiding it
  });

  // Title row: repo name (left, brightest — the headline and the link, truncates) + stars (right).
  card.appendChild(createEl('div', { className: 'flex items-center gap-3' },
    createEl('a', {
      href: repo.url, target: '_blank', rel: 'noopener noreferrer',
      title: repo.name,  // truncated long names are still readable on hover + to assistive tech
      className: 'font-code font-semibold text-slate-50 hover:text-emerald-400 transition-colors duration-150 truncate min-w-0 flex-1',
    }, repo.name),
    createEl('span', { className: 'text-amber-400/80 text-sm shrink-0' }, `★ ${repo.stars}`),
  ));

  // Level: the rating, under the title. Archived sits alongside it when present.
  const meta = createEl('div', { className: 'flex items-center gap-2 flex-wrap' }, levelChip(repo.level));
  if (repo.archived) {
    meta.appendChild(createEl('span', {
      className: 'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-500/15 text-amber-300',
    }, 'Archived'));
  }
  card.appendChild(meta);

  // Language · complexity — plain monospace text, no chips. The two facts the verdict doesn't
  // already carry. "—" placeholders (degraded cards) are skipped.
  const facts = [];
  if (repo.language) facts.push(repo.language);
  if (repo.complexity && repo.complexity !== '—') facts.push(repo.complexity);
  if (facts.length) {
    card.appendChild(createEl('p', { className: 'font-code text-xs text-slate-400' }, facts.join('  ·  ')));
  }

  // summary = neutral "what it is", quiet and clamped. Null on degraded cards, so skipped.
  if (repo.summary) {
    card.appendChild(createEl('p', { className: 'text-slate-400 text-sm leading-snug line-clamp-2' }, repo.summary));
  }

  // assessment = the verdict ("is it good, for a junior?"). Loudest body text, with a thin
  // accent bar in the level's color. mt-auto pins it to the bottom so the grid stays aligned.
  card.appendChild(createEl('p', {
    className: `text-sm text-slate-200 leading-relaxed border-l-2 ${accent} pl-3 mt-auto`,
  }, repo.assessment));

  return card;
}

function renderSkeletons(n) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < n; i++) {
    const card = createEl('div', { className: 'bg-slate-800 border border-slate-700 rounded-xl p-5 flex flex-col gap-3 animate-pulse' });
    card.appendChild(createEl('div', { className: 'h-4 bg-slate-700 rounded w-3/4' }));
    card.appendChild(createEl('div', { className: 'h-3 bg-slate-700 rounded w-1/4' }));
    card.appendChild(createEl('div', { className: 'h-3 bg-slate-700 rounded w-1/2' }));
    card.appendChild(createEl('div', { className: 'h-3 bg-slate-700 rounded w-2/3' }));
    card.appendChild(createEl('div', { className: 'h-10 bg-slate-700 rounded' }));
    frag.appendChild(card);
  }
  return frag;
}

// One line summarizing the whole inventory, all derived from the single repos call:
// "43 public repos · 23 archived · 3 forks — analyzed the 25 most recently updated"
function repoSummary(d) {
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const ownRepos = d.total_found - d.forks_excluded;   // non-fork repos = the user's own work
  const attempted = d.repo_count + (d.failed_count || 0);  // cards shown = rated + degraded

  const parts = [plural(d.total_found, 'public repo')];
  if (d.archived_total) parts.push(`${d.archived_total} archived`);  // adjective, no plural 's'
  if (d.forks_excluded) parts.push(plural(d.forks_excluded, 'fork'));
  const inventory = parts.join(' · ');

  let analyzed;
  if (d.failed_count) {
    // Some repos were attempted but couldn't be assessed — report rated vs attempted honestly.
    analyzed = `assessed ${d.repo_count} of the ${attempted} most recently updated `
             + `· ${d.failed_count} couldn't be assessed`;
  } else {
    analyzed = attempted < ownRepos
      ? `analyzed the ${attempted} most recently updated repos`
      : `analyzed all ${attempted} repos`;
  }

  return `${inventory} — ${analyzed}`;
}

function renderBanner(d) {
  const banner = createEl('div', { className: 'bg-slate-800 border border-slate-700 rounded-xl p-5 mb-4' });
  banner.appendChild(createEl('p', { className: 'text-slate-400 text-xs uppercase tracking-wider mb-2' }, 'Overall Assessment'));
  banner.appendChild(createEl('p', { className: 'text-slate-100 leading-relaxed' }, d.synthesis));
  return banner;
}

function renderError(err) {
  const card = createEl('div', { className: 'bg-red-500/10 border border-red-500/30 rounded-xl p-5' });
  card.appendChild(createEl('p', { className: 'text-red-400 text-xs uppercase tracking-wider font-medium mb-2' }, 'Error'));

  const errorCode = err?.code;
  if (errorCode === 'user_not_found') {
    const p = document.createElement('p');
    p.className = 'text-slate-200';
    p.appendChild(document.createTextNode('No GitHub user named '));
    const b = document.createElement('strong');
    b.textContent = state.username;
    p.appendChild(b);
    p.appendChild(document.createTextNode('. Check the spelling.'));
    card.appendChild(p);
  } else if (errorCode === 'github_rate_limit') {
    card.appendChild(createEl('p', { className: 'text-slate-200' },
      'GitHub rate limit reached. Add a GITHUB_TOKEN to .env (see README) to raise it to 5,000/hr.'
    ));
  } else if (errorCode === 'ai_auth') {
    card.appendChild(createEl('p', { className: 'text-slate-200' },
      'AI API key invalid or expired. Set a valid AI_API_KEY in .env (see README).'
    ));
  } else if (errorCode === 'ai_model') {
    // Backend message names the offending model id; surface it as-is, then point at the README.
    card.appendChild(createEl('p', { className: 'text-slate-200' },
      `${err?.message ?? 'AI model not found.'} (see README)`
    ));
  } else if (errorCode === 'ai_access') {
    card.appendChild(createEl('p', { className: 'text-slate-200' },
      err?.message ?? 'AI request denied — most likely the spend cap has been reached.'
    ));
  } else {
    card.appendChild(createEl('p', { className: 'text-slate-200' },
      err?.message ?? 'Something went wrong reaching an upstream service. Try again.'
    ));
  }

  return card;
}

// ─── Render ──────────────────────────────────────────────────────────────────
function render() {
  const banner  = document.getElementById('banner');
  const results = document.getElementById('results');
  const btn     = document.getElementById('analyze-btn');
  const input   = document.getElementById('username-input');
  const spinner = document.getElementById('btn-spinner');
  const btnText = document.getElementById('btn-text');

  banner.replaceChildren();
  results.replaceChildren();

  const isLoading = state.status === 'loading';
  btn.disabled   = isLoading || input.value.trim() === '';
  input.disabled = isLoading;

  if (isLoading) {
    btnText.classList.add('hidden');
    spinner.classList.remove('hidden');

    // Static label: the repo count isn't known client-side until the single blocking
    // /analyze response returns, so the loader is intentionally count-less.
    banner.appendChild(createEl('p', { className: 'text-slate-400 text-sm mb-3' }, 'Analyzing repos…'));

    const sb = createEl('div', { className: 'bg-slate-800 border border-slate-700 rounded-xl p-5 mb-4 animate-pulse' });
    sb.appendChild(createEl('div', { className: 'h-3 bg-slate-700 rounded w-1/4 mb-3' }));
    sb.appendChild(createEl('div', { className: 'h-4 bg-slate-700 rounded w-full mb-2' }));
    sb.appendChild(createEl('div', { className: 'h-4 bg-slate-700 rounded w-3/4' }));
    banner.appendChild(sb);

    const grid = createEl('div', { className: 'grid gap-4 sm:grid-cols-2 lg:grid-cols-3' });
    grid.appendChild(renderSkeletons(6));
    results.appendChild(grid);
    return;
  }

  btnText.classList.remove('hidden');
  spinner.classList.add('hidden');

  if (state.status === 'error') {
    banner.appendChild(renderError(state.error));
    return;
  }

  if (state.status === 'empty') {
    const d = state.data;
    const card = createEl('div', { className: 'bg-slate-800 border border-slate-700 rounded-xl p-5' });
    card.appendChild(createEl('p', { className: 'text-slate-400 text-xs uppercase tracking-wider mb-2' }, 'Nothing to analyze'));
    let msg;
    if (!d || d.total_found === 0) {
      msg = `${state.username} has no public repositories.`;
    } else {
      const n = d.total_found, f = d.forks_excluded, own = n - f;
      const repoWord = n === 1 ? 'repo' : 'repos';
      msg = f === n
        ? `Found ${n} ${repoWord}, but ${n === 1 ? 'it is a fork' : 'all are forks'} — no original work to analyze.`
        : `Found ${n} ${repoWord} (${own} non-fork), but none produced anything to show.`;
    }
    card.appendChild(createEl('p', { className: 'text-slate-200' }, msg));
    banner.appendChild(card);
    return;
  }

  if (state.status === 'success') {
    const d = state.data;
    banner.appendChild(renderBanner(d));
    results.appendChild(createEl('p', { className: 'text-slate-400 text-sm mb-4' }, repoSummary(d)));
    const grid = createEl('div', { className: 'grid gap-4 sm:grid-cols-2 lg:grid-cols-3' });
    for (const repo of [...d.repos].sort(byStarsThenLevel)) grid.appendChild(renderCard(repo));
    results.appendChild(grid);
  }
}

// ─── Wire form ───────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('username-input');
  const btn   = document.getElementById('analyze-btn');

  function submit() {
    const username = input.value.trim();
    if (!username || state.status === 'loading') return;
    analyze(username);
  }

  btn.addEventListener('click', submit);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
  input.addEventListener('input', () => {
    btn.disabled = state.status === 'loading' || input.value.trim() === '';
  });

  render();
});
