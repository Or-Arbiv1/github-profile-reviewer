// ─── State ───────────────────────────────────────────────────────────────────
const state = { status: 'idle', username: '', data: null, error: null };

// ─── Safe DOM builder ────────────────────────────────────────────────────────
// Strings become text nodes — never innerHTML — so user/AI content can't inject HTML.
function h(tag, attrs, ...children) {
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
function levelBadge(level) {
  const classes = {
    Basic:        'bg-slate-700 text-slate-200',
    Intermediate: 'bg-sky-500/15 text-sky-300 ring-1 ring-sky-500/30',
    Advanced:     'bg-green-500/15 text-green-300 ring-1 ring-green-500/30',
  };
  // No level == the AI call for this repo failed; render a neutral "Unrated" badge.
  const label = level ?? 'Unrated';
  const cls = classes[level] ?? 'bg-slate-800 text-slate-500 ring-1 ring-slate-700';
  return h('span', {
    className: `inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}`,
  }, label);
}

function renderCard(repo) {
  const card = h('div', {
    className: 'bg-slate-800 border border-slate-700 rounded-xl p-5 flex flex-col gap-3 hover:border-slate-600 transition-colors duration-200',
  });

  // name + stars
  const nameLink = h('a', {
    href: repo.url, target: '_blank', rel: 'noopener noreferrer',
    className: 'font-code font-semibold text-slate-50 hover:text-green-400 transition-colors duration-150 truncate',
  }, repo.name);
  const stars = h('span', { className: 'text-slate-400 text-sm shrink-0' }, `★ ${repo.stars}`);
  card.appendChild(h('div', { className: 'flex items-start justify-between gap-2' }, nameLink, stars));

  // level badge + (archived tag) + language
  const meta = h('div', { className: 'flex items-center gap-2 flex-wrap' }, levelBadge(repo.level));
  if (repo.archived) {
    meta.appendChild(h('span', {
      className: 'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30',
    }, 'Archived'));
  }
  if (repo.language) meta.appendChild(h('span', { className: 'text-slate-400 text-xs' }, repo.language));
  card.appendChild(meta);

  // label / value rows
  function row(label, value) {
    return h('div', { className: 'flex gap-1.5 text-sm' },
      h('span', { className: 'text-slate-500 shrink-0' }, label + ':'),
      h('span', { className: 'text-slate-300' }, value),
    );
  }
  card.appendChild(row('README', repo.readme_clarity));
  card.appendChild(row('Complexity', repo.complexity));

  // assessment
  card.appendChild(h('p', { className: 'text-slate-400 text-sm leading-relaxed' }, repo.assessment));

  return card;
}

function renderSkeletons(n) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < n; i++) {
    const card = h('div', { className: 'bg-slate-800 border border-slate-700 rounded-xl p-5 flex flex-col gap-3 animate-pulse' });
    card.appendChild(h('div', { className: 'h-4 bg-slate-700 rounded w-3/4' }));
    card.appendChild(h('div', { className: 'h-3 bg-slate-700 rounded w-1/4' }));
    card.appendChild(h('div', { className: 'h-3 bg-slate-700 rounded w-1/2' }));
    card.appendChild(h('div', { className: 'h-3 bg-slate-700 rounded w-2/3' }));
    card.appendChild(h('div', { className: 'h-10 bg-slate-700 rounded' }));
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
      ? `analyzed the ${attempted} most recently updated`
      : `analyzed all ${attempted}`;
  }

  return `${inventory} — ${analyzed}`;
}

function renderBanner(d) {
  const banner = h('div', { className: 'bg-slate-800 border border-slate-700 rounded-xl p-5 mb-4' });
  banner.appendChild(h('p', { className: 'text-slate-400 text-xs uppercase tracking-wider mb-2' }, 'Overall Assessment'));
  banner.appendChild(h('p', { className: 'text-slate-100 leading-relaxed' }, d.synthesis));
  return banner;
}

function renderError(err) {
  const card = h('div', { className: 'bg-red-500/10 border border-red-500/30 rounded-xl p-5' });
  card.appendChild(h('p', { className: 'text-red-400 text-xs uppercase tracking-wider font-medium mb-2' }, 'Error'));

  const code = err?.code;
  if (code === 'user_not_found') {
    const p = document.createElement('p');
    p.className = 'text-slate-200';
    p.appendChild(document.createTextNode('No GitHub user named '));
    const b = document.createElement('strong');
    b.textContent = state.username;
    p.appendChild(b);
    p.appendChild(document.createTextNode('. Check the spelling.'));
    card.appendChild(p);
  } else if (code === 'github_rate_limit') {
    card.appendChild(h('p', { className: 'text-slate-200' },
      'GitHub rate limit reached. Add a GITHUB_TOKEN to .env (see README) to raise it to 5,000/hr.'
    ));
  } else if (code === 'ai_auth') {
    card.appendChild(h('p', { className: 'text-slate-200' },
      'AI API key invalid or expired. Set a valid AI_API_KEY in .env (see README).'
    ));
  } else {
    card.appendChild(h('p', { className: 'text-slate-200' },
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
    banner.appendChild(h('p', { className: 'text-slate-400 text-sm mb-3' }, 'Analyzing repos…'));

    const sb = h('div', { className: 'bg-slate-800 border border-slate-700 rounded-xl p-5 mb-4 animate-pulse' });
    sb.appendChild(h('div', { className: 'h-3 bg-slate-700 rounded w-1/4 mb-3' }));
    sb.appendChild(h('div', { className: 'h-4 bg-slate-700 rounded w-full mb-2' }));
    sb.appendChild(h('div', { className: 'h-4 bg-slate-700 rounded w-3/4' }));
    banner.appendChild(sb);

    const grid = h('div', { className: 'grid gap-4 sm:grid-cols-2 lg:grid-cols-3' });
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
    const card = h('div', { className: 'bg-slate-800 border border-slate-700 rounded-xl p-5' });
    card.appendChild(h('p', { className: 'text-slate-400 text-xs uppercase tracking-wider mb-2' }, 'Nothing to analyze'));
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
    card.appendChild(h('p', { className: 'text-slate-200' }, msg));
    banner.appendChild(card);
    return;
  }

  if (state.status === 'success') {
    const d = state.data;
    banner.appendChild(renderBanner(d));
    results.appendChild(h('p', { className: 'text-slate-400 text-sm mb-4' }, repoSummary(d)));
    const grid = h('div', { className: 'grid gap-4 sm:grid-cols-2 lg:grid-cols-3' });
    for (const repo of d.repos) grid.appendChild(renderCard(repo));
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
