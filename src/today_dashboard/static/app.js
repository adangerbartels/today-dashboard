'use strict';

/* ── state ─────────────────────────────────────────────────────────── */

const state = {
  data: null,
  loading: true,
  fatal: null,
  fetchedAt: null,
  filter: 'all',
  doneOpen: false,
  refreshing: false,
};

const REFRESH_MS = 60_000;

const el = (id) => document.getElementById(id);

const dom = {
  banner: el('banner'),
  freshness: el('freshness'),
  refresh: el('refresh'),
  toast: el('toast'),
  addForm: el('add-form'),
  addInput: el('add-input'),
  todosBody: el('todos-body'),
  todosCount: el('todos-count'),
  progress: el('todos-progress'),
  progressFill: el('todos-fill'),
  progressLabel: el('todos-progress-label'),
  doneBlock: el('done-block'),
  doneToggle: el('done-toggle'),
  doneToggleLabel: el('done-toggle-label'),
  doneBody: el('done-body'),
  clearDone: el('clear-done'),
  calBody: el('cal-body'),
  calCount: el('cal-count'),
  lunchBody: el('lunch-body'),
  lunchCount: el('lunch-count'),
  mailBody: el('mail-body'),
  mailCount: el('mail-count'),
  slackBody: el('slack-body'),
  slackCount: el('slack-count'),
  slackSource: el('slack-source'),
  jiraBody: el('jira-body'),
  jiraCount: el('jira-count'),
  githubBody: el('github-body'),
  githubCount: el('github-count'),
  githubFilters: el('github-filters'),
};

/* ── helpers ───────────────────────────────────────────────────────── */

function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Only absolute http(s) links get rendered — API payloads are data, not trusted markup.
 *  Parsing with no base means relative junk (and "null") throws instead of
 *  resolving against our own origin. */
function safeUrl(value) {
  if (!value) return '';
  try {
    const url = new URL(String(value));
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : '';
  } catch {
    return '';
  }
}

/** "gh:acme/web#1177" → "acme/web#1177" */
function originLabel(origin) {
  return String(origin || 'Open link').replace(/^(jira|gh):/, '');
}

function ago(iso) {
  if (!iso) return '';
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '';
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 45) return 'just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.round(days / 7);
  return weeks < 5 ? `${weeks}w ago` : `${Math.round(days / 30)}mo ago`;
}

/** Days until a YYYY-MM-DD date, in local time. Negative means overdue. */
function daysUntil(dateStr) {
  if (!dateStr) return null;
  const parts = /^(\d{4})-(\d{2})-(\d{2})/.exec(dateStr);
  if (!parts) return null;
  const due = new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((due - today) / 86_400_000);
}

function dueLabel(dateStr) {
  const days = daysUntil(dateStr);
  if (days === null) return null;
  if (days < 0) return { text: `${Math.abs(days)}d overdue`, urgent: true };
  if (days === 0) return { text: 'Due today', urgent: true };
  if (days === 1) return { text: 'Due tomorrow', urgent: true };
  if (days <= 7) return { text: `Due in ${days}d`, urgent: false };
  return { text: `Due ${dateStr.slice(5)}`, urgent: false };
}

let toastTimer = null;
function toast(message) {
  dom.toast.textContent = message;
  dom.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { dom.toast.hidden = true; }, 2600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : {},
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (payload.error) message = payload.error;
    } catch { /* non-JSON error body */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

/* ── fetch ─────────────────────────────────────────────────────────── */

async function load({ force = false, quiet = false } = {}) {
  if (!quiet) {
    state.refreshing = true;
    dom.refresh.classList.add('is-refreshing');
  }
  try {
    state.data = await api(`/api/items${force ? '?refresh=1' : ''}`);
    state.fetchedAt = Date.now();
    state.fatal = null;
  } catch (error) {
    state.fatal = error.message;
  } finally {
    state.loading = false;
    state.refreshing = false;
    dom.refresh.classList.remove('is-refreshing');
    render();
  }
}

/* ── render: shell ─────────────────────────────────────────────────── */

function renderBanner() {
  const data = state.data;
  if (!data) return void (dom.banner.hidden = true);

  const labels = {
    jira: 'Jira', github: 'GitHub', gcal: 'Calendar', gmail: 'Gmail',
    slack: 'Slack', catercow: 'CaterCow',
  };
  const missing = Object.keys(labels)
    .filter((key) => data[key] && data[key].configured === false)
    .map((key) => labels[key]);

  if (!missing.length) return void (dom.banner.hidden = true);

  // Calendar and Gmail share one Google connection — don't imply two setups.
  const listed = [...new Set(missing.map((n) => (n === 'Calendar' || n === 'Gmail' ? 'Google' : n)))];
  const phrase = listed.length > 1
    ? `${listed.slice(0, -1).join(', ')} and ${listed[listed.length - 1]}`
    : listed[0];

  dom.banner.hidden = false;
  dom.banner.innerHTML =
    `Showing <strong>demo data</strong> for ${esc(phrase)}. ` +
    `<button type="button" class="banner-cta" data-action="open-settings">Set up connections</button>`;
}

function renderFreshness() {
  if (state.loading) return void (dom.freshness.textContent = 'Loading…');
  if (state.fatal) return void (dom.freshness.textContent = 'Offline');
  dom.freshness.textContent = state.fetchedAt
    ? `Updated ${ago(new Date(state.fetchedAt).toISOString())}`
    : '';
}

function skeletons(count) {
  return Array.from({ length: count }, () => '<div class="skeleton"></div>').join('');
}

function emptyState(title, hint) {
  return `<div class="empty"><strong>${esc(title)}</strong>${hint ? esc(hint) : ''}</div>`;
}

function laneError(source, message) {
  return `<div class="lane-error"><strong>${esc(source)} unavailable</strong>${esc(message)}</div>`;
}

/* ── render: todos ─────────────────────────────────────────────────── */

function todoRow(todo) {
  const link = safeUrl(todo.link);
  return `
    <div class="todo ${todo.done ? 'is-done' : ''}" data-id="${esc(todo.id)}">
      <input type="checkbox" class="check" ${todo.done ? 'checked' : ''}
             data-action="toggle" aria-label="${esc(todo.title)}">
      <div class="todo-main">
        <div class="todo-title">${esc(todo.title)}</div>
        ${link ? `<a class="todo-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${esc(originLabel(todo.origin))}</a>` : ''}
      </div>
      <button type="button" class="icon-btn" data-action="delete" title="Delete" aria-label="Delete task">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2h4v1h3v1.5H3V3h3V2zm-1.5 4h7l-.6 8H5.1L4.5 6z"/></svg>
      </button>
    </div>`;
}

function renderTodos() {
  if (state.loading) {
    dom.todosBody.innerHTML = skeletons(3);
    return;
  }

  const todos = (state.data && state.data.todos) || [];
  const open = todos.filter((t) => !t.done);
  const done = todos.filter((t) => t.done);

  dom.todosCount.textContent = String(open.length);
  dom.todosBody.innerHTML = open.length
    ? open.map(todoRow).join('')
    : emptyState('Nothing queued', 'Add a task above, or pull one in from Jira or GitHub.');

  // Progress bar only earns its space once something is finished.
  if (todos.length && done.length) {
    dom.progress.hidden = false;
    dom.progressFill.style.width = `${Math.round((done.length / todos.length) * 100)}%`;
    dom.progressLabel.textContent = `${done.length} of ${todos.length} done`;
  } else {
    dom.progress.hidden = true;
  }

  dom.doneBlock.hidden = done.length === 0;
  dom.doneToggleLabel.textContent = `Done (${done.length})`;
  dom.doneToggle.setAttribute('aria-expanded', String(state.doneOpen));
  dom.doneBody.hidden = !state.doneOpen;
  dom.clearDone.hidden = !state.doneOpen;
  dom.doneBody.innerHTML = state.doneOpen ? done.map(todoRow).join('') : '';
}

/* ── render: calendar ──────────────────────────────────────────────── */

function clockTime(iso) {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '';
  return when.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

/** "in 8m" / "in 2h 10m" — how long until it starts. */
function untilLabel(minutes) {
  if (minutes === null || minutes === undefined) return '';
  if (minutes <= 0) return 'now';
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `in ${hours}h ${rest}m` : `in ${hours}h`;
}

function eventCard(event, pinned) {
  const url = safeUrl(event.url);
  const video = safeUrl(event.video_url);
  const tier = event.in_progress ? 'low' : event.needs_response ? 'medium' : '';

  const when = event.all_day
    ? 'All day'
    : `${clockTime(event.start_at)}${event.end_at ? `–${clockTime(event.end_at)}` : ''}`;

  const meta = [];
  if (event.attendee_count > 1) meta.push(`${event.attendee_count} people`);
  if (event.location) meta.push(esc(event.location));
  if (event.duration_minutes && !event.all_day) meta.push(`${event.duration_minutes}m`);

  const chips = [];
  if (event.in_progress) chips.push('<span class="chip chip-now">Now</span>');
  if (event.needs_response) chips.push('<span class="chip chip-changes-requested">RSVP</span>');
  if (event.tentative) chips.push('<span class="chip">Tentative</span>');

  return `
    <div class="card" ${tier ? `data-tier="${tier}"` : ''}>
      <div class="card-top">
        <span class="key when">${esc(when)}</span>
        ${chips.join('')}
        ${!event.in_progress && !event.all_day
          ? `<span class="ci until">${esc(untilLabel(event.minutes_until))}</span>` : ''}
      </div>
      ${url
        ? `<a class="card-title" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(event.title)}</a>`
        : `<p class="card-title">${esc(event.title)}</p>`}
      ${meta.length ? `<div class="card-meta">${meta.join('<span class="sep">·</span>')}</div>` : ''}
      <div class="card-actions">
        ${video ? `<a class="icon-btn" href="${esc(video)}" target="_blank" rel="noopener noreferrer"
             title="Join video call" aria-label="Join video call">
             <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 4.5h7v7H2zm8 2.2l4-2.2v7l-4-2.2z"/></svg>
           </a>` : ''}
        ${pinIconButton(`cal:${event.id}`, event.title, url, pinned)}
      </div>
    </div>`;
}

function renderCalendar() {
  if (state.loading) { dom.calBody.innerHTML = skeletons(3); return; }

  const feed = (state.data && state.data.gcal) || {};
  const items = feed.items || [];
  dom.calCount.textContent = String(items.length);
  dom.calCount.classList.toggle('is-hot', items.some((e) => e.in_progress));

  if (feed.error) { dom.calBody.innerHTML = laneError('Calendar', feed.error); return; }

  const pinned = pinnedOrigins();
  const partial = (feed.errors || []).length
    ? `<div class="lane-notice"><span>${esc(`Some calendars couldn't be read: ${feed.errors.join('; ')}`)}</span></div>`
    : '';

  dom.calBody.innerHTML = partial + (items.length
    ? items.map((event) => eventCard(event, pinned.has(`cal:${event.id}`))).join('')
    : emptyState('Nothing left today', 'No more events on your calendar before midnight.'));
}

/* ── render: catercow ──────────────────────────────────────────────── */

function lunchCard(day, pinned) {
  const url = safeUrl(day.url);
  const tier = day.is_today ? 'high' : day.is_tomorrow ? 'medium' : '';
  const when = day.is_today ? 'Today' : day.is_tomorrow ? 'Tomorrow' : `in ${day.days_out}d`;

  return `
    <div class="card" ${tier ? `data-tier="${tier}"` : ''}>
      <div class="card-top">
        ${url
          ? `<a class="card-title lunch-day" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(day.label)}</a>`
          : `<span class="card-title lunch-day">${esc(day.label)}</span>`}
        <span class="ci until ${day.is_today ? 'is-urgent' : ''}">${esc(when)}</span>
      </div>
      <div class="card-actions">
        ${pinIconButton(`lunch:${day.date}`, `Pick lunch for ${day.label}`, url, pinned)}
      </div>
    </div>`;
}

function renderLunch() {
  if (state.loading) { dom.lunchBody.innerHTML = skeletons(2); return; }

  const feed = (state.data && state.data.catercow) || {};
  const items = feed.items || [];
  dom.lunchCount.textContent = String(feed.pending || 0);
  dom.lunchCount.classList.toggle('is-hot', items.some((d) => d.is_today || d.is_tomorrow));

  if (feed.error) { dom.lunchBody.innerHTML = laneError('CaterCow', feed.error); return; }

  // Warnings matter here: with no readable source, "nothing pending" would be a lie.
  const notice = (feed.warnings || []).length
    ? `<div class="lane-notice">${feed.warnings.map((w) => `<span>${esc(w)}</span>`).join('')}</div>`
    : '';

  if (feed.unconfigured) {
    // Keep the warnings — they say *why* nothing could be read, which is the
    // difference between "not set up" and "your Google sign-in died".
    dom.lunchBody.innerHTML = notice + emptyState(
      notice ? 'Couldn’t check' : 'Not set up',
      notice ? '' : 'Add a CaterCow cookie, or connect Google to read confirmation emails.');
    return;
  }

  const pinned = pinnedOrigins();
  let html = notice;

  if (!items.length) {
    html += emptyState('All picked',
      `Every lunch day in the next ${feed.horizon_days || 14} days is sorted.`);
  } else {
    html += items.map((day) => lunchCard(day, pinned.has(`lunch:${day.date}`))).join('');
  }

  const covered = (feed.selected || []).length;
  if (covered) {
    html += `<div class="lane-more">${esc(
      `${covered} already selected${feed.sources && feed.sources.length
        ? ` · from ${feed.sources.join(' + ')}` : ''}`)}</div>`;
  }
  dom.lunchBody.innerHTML = html;
}

/* ── render: gmail ─────────────────────────────────────────────────── */

function mailRow(item, pinned) {
  const url = safeUrl(item.url);
  const who = item.from_name || item.from_email || 'Unknown sender';
  const flags = [];
  if (item.important) flags.push('<span class="chip chip-review-requested">Important</span>');
  if (item.starred) flags.push('<span class="chip chip-ready-to-merge">Starred</span>');
  if (item.bulk) flags.push('<span class="chip">Bulk</span>');

  return `
    <div class="card">
      <div class="card-top">
        <span class="key who-strong">${esc(who)}</span>
        ${flags.join('')}
      </div>
      ${url
        ? `<a class="card-title" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(item.subject)}</a>`
        : `<p class="card-title">${esc(item.subject)}</p>`}
      <div class="card-actions">
        ${pinIconButton(`mail:${item.id}`, `Reply: ${item.subject}`, url, pinned.has(`mail:${item.id}`))}
      </div>
    </div>`;
}

function renderMail() {
  if (state.loading) { dom.mailBody.innerHTML = skeletons(3); return; }

  const feed = (state.data && state.data.gmail) || {};
  const count = feed.count || 0;
  dom.mailCount.textContent = feed.count_is_partial ? `${count}+` : String(count);
  dom.mailCount.classList.toggle('is-hot', count > 0);

  if (feed.error) { dom.mailBody.innerHTML = laneError('Gmail', feed.error); return; }

  const items = feed.items || [];
  if (!count) {
    dom.mailBody.innerHTML = emptyState('Inbox clear',
      'No unread mail that looks like it needs you.');
    return;
  }

  const pinned = pinnedOrigins();
  let html = items.map((item) => mailRow(item, pinned)).join('');

  const hidden = count - (feed.shown || 0);
  if (hidden > 0) {
    const inbox = safeUrl(feed.inbox_url);
    html += `<div class="lane-more">${esc(`${hidden} more unread`)}${
      inbox ? ` — <a href="${esc(inbox)}" target="_blank" rel="noopener noreferrer">open Gmail</a>` : ''
    }</div>`;
  }
  dom.mailBody.innerHTML = html;
}

/* ── render: slack ─────────────────────────────────────────────────── */

/** Slack rotating tokens last 12h; say how long is left if we know. */
function expiryWarning(expiresAt) {
  const base = 'This Slack token expires and can’t renew itself';
  if (!expiresAt) {
    return `${base}. Add the refresh token, client ID and secret in Connections.`;
  }
  const minutes = Math.round((expiresAt * 1000 - Date.now()) / 60_000);
  if (minutes <= 0) {
    return 'This Slack token has expired. Add rotation credentials in Connections, or paste a new token.';
  }
  const left = minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  return `${base} — about ${left} left. Add the refresh token, client ID and secret in Connections.`;
}

function channelRow(channel) {
  const url = safeUrl(channel.url);
  const label = `${channel.private ? '🔒 ' : '#'}${channel.name}`;

  if (channel.error) {
    return `<div class="card" data-tier="high">
      <div class="card-top"><span class="key">${esc(label)}</span></div>
      <div class="card-meta">${esc(channel.error)}</div>
    </div>`;
  }

  const mentions = channel.mentions || 0;
  return `
    <div class="card channel" ${mentions ? 'data-tier="medium"' : ''}>
      <div class="card-top">
        ${url
          ? `<a class="key channel-name" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`
          : `<span class="key channel-name">${esc(label)}</span>`}
        <span class="tally ${mentions ? 'has-mentions' : ''}">
          ${esc(String(channel.count))}${channel.partial ? '+' : ''}
        </span>
      </div>
      ${mentions
        ? `<div class="chips"><span class="chip chip-ci-failing">${esc(
            mentions === 1 ? '1 mentions you' : `${mentions} mention you`)}</span></div>`
        : ''}
    </div>`;
}

function renderSlack() {
  if (state.loading) { dom.slackBody.innerHTML = skeletons(3); return; }

  const feed = (state.data && state.data.slack) || {};
  const total = feed.total || 0;
  // Nothing readable is not the same as nothing unread — don't show a
  // reassuring 0 when every channel failed.
  const blind = feed.readable === 0 && (feed.channels_selected || 0) > 0;
  dom.slackCount.textContent = blind ? '—' : String(total);
  dom.slackCount.classList.toggle('is-hot', !blind && (feed.mentions || 0) > 0);
  dom.slackSource.textContent = feed.workspace ? `Slack · ${feed.workspace}` : 'Slack';

  if (feed.error) { dom.slackBody.innerHTML = laneError('Slack', feed.error); return; }

  if (feed.needs_selection) {
    dom.slackBody.innerHTML = emptyState('No channels chosen',
      'Pick your important channels in Connections to see their unread counts.');
    return;
  }

  const items = feed.items || [];
  const warnings = [];

  // "recent" means Slack couldn't tell us what you'd read — say so.
  if (feed.mode === 'recent') {
    warnings.push('Showing messages from the last 24 hours: this token has no ' +
      'personal read state, so Slack can’t report true unread counts.');
  }
  // A rotating token with no renewal credentials dies within 12 hours.
  if (feed.rotating && !feed.can_renew) {
    warnings.push(expiryWarning(feed.expires_at));
  }
  // One message per distinct failure, not one per channel.
  for (const message of (feed.errors || [])) {
    warnings.push(message.includes('missing a scope')
      ? `${message}. Add those scopes to your Slack app and reinstall it, then paste the new token.`
      : message);
  }

  const notice = warnings.length
    ? `<div class="lane-notice">${warnings.map((w) => `<span>${esc(w)}</span>`).join('')}</div>`
    : '';

  if (!items.length) {
    dom.slackBody.innerHTML = notice + (blind
      ? emptyState('Can’t read your channels', 'See the note above.')
      : emptyState('All caught up',
          `Nothing unread in your ${feed.channels_selected || 0} chosen channels.`));
    return;
  }

  let html = notice + items.map(channelRow).join('');
  if (feed.quiet) html += `<div class="lane-more">${esc(`${feed.quiet} quiet`)}</div>`;
  dom.slackBody.innerHTML = html;
}

/* ── render: jira ──────────────────────────────────────────────────── */

function statusPillClass(status) {
  const name = (status || '').toLowerCase();
  if (/block|imped|hold/.test(name)) return 'pill-blocked';
  if (/review|qa|verif/.test(name)) return 'pill-review';
  return 'pill-progress';
}

function jiraTier(issue) {
  const due = daysUntil(issue.due_date);
  if (/block|imped|hold/i.test(issue.status || '')) return 'high';
  if (due !== null && due < 0) return 'high';
  if (/highest|high|critical|blocker/i.test(issue.priority || '')) return 'medium';
  if (due !== null && due <= 2) return 'medium';
  return '';
}

function jiraCard(issue, pinned) {
  const url = safeUrl(issue.url);
  const tier = jiraTier(issue);
  const due = dueLabel(issue.due_date);
  const meta = [];
  if (issue.type) meta.push(esc(issue.type));
  if (issue.priority) meta.push(esc(issue.priority));
  if (due) meta.push(`<span style="color:var(--${due.urgent ? 'red' : 'text-faint'})">${esc(due.text)}</span>`);
  if (issue.updated_at) meta.push(esc(ago(issue.updated_at)));

  return `
    <div class="card" ${tier ? `data-tier="${tier}"` : ''}>
      <div class="card-top">
        ${url
          ? `<a class="key" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(issue.key)}</a>`
          : `<span class="key">${esc(issue.key)}</span>`}
        <span class="pill ${statusPillClass(issue.status)}">${esc(issue.status)}</span>
      </div>
      ${url
        ? `<a class="card-title" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(issue.title)}</a>`
        : `<p class="card-title">${esc(issue.title)}</p>`}
      ${meta.length ? `<div class="card-meta">${meta.join('<span class="sep">·</span>')}</div>` : ''}
      ${pinButton(`jira:${issue.key}`, `${issue.key} — ${issue.title}`, url, pinned)}
    </div>`;
}

function renderJira() {
  if (state.loading) {
    dom.jiraBody.innerHTML = skeletons(4);
    return;
  }

  const feed = (state.data && state.data.jira) || {};
  const items = feed.items || [];
  dom.jiraCount.textContent = String(items.length);

  if (feed.error) {
    dom.jiraBody.innerHTML = laneError('Jira', feed.error);
    return;
  }

  const pinned = pinnedOrigins();
  dom.jiraBody.innerHTML = items.length
    ? items.map((issue) => jiraCard(issue, pinned.has(`jira:${issue.key}`))).join('')
    : emptyState('No issues in progress', 'Nothing is assigned to you and in flight right now.');
}

/* ── render: github ────────────────────────────────────────────────── */

const REASON_TIER = {
  'ci-failing': 'high',
  'changes-requested': 'medium',
  conflicts: 'medium',
  'review-requested': 'low',
  'ready-to-merge': 'done',
};

const CI_LABEL = {
  SUCCESS: ['ci-success', 'passing'],
  FAILURE: ['ci-failure', 'failing'],
  ERROR: ['ci-failure', 'errored'],
  PENDING: ['ci-pending', 'pending'],
  EXPECTED: ['ci-pending', 'queued'],
};

function prCard(pr, pinned) {
  const url = safeUrl(pr.url);
  const tier = REASON_TIER[pr.reasons[0]] || '';
  const [ciClass, ciText] = CI_LABEL[pr.checks] || [];
  const isNew = pr.reasons.includes('new-activity');

  const chips = pr.reasons
    .map((reason, i) => `<span class="chip chip-${esc(reason)}">${esc(pr.reason_labels[i] || reason)}</span>`)
    .join('');

  const meta = [];
  if (!pr.is_mine) meta.push(`<span class="who">${esc(pr.author)}</span>`);
  meta.push(`<span class="diff-add">+${pr.additions}</span> <span class="diff-del">−${pr.deletions}</span>`);
  if (pr.activity_at) {
    const who = pr.activity_by ? `${pr.activity_by} · ` : '';
    meta.push(esc(who + ago(pr.activity_at)));
  } else if (pr.updated_at) {
    meta.push(esc(ago(pr.updated_at)));
  }

  return `
    <div class="card" ${tier ? `data-tier="${tier}"` : ''} data-key="${esc(pr.key)}">
      <div class="card-top">
        ${url
          ? `<a class="key" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(pr.repo)} #${esc(pr.number)}</a>`
          : `<span class="key">${esc(pr.repo)} #${esc(pr.number)}</span>`}
        ${pr.is_draft ? '<span class="chip chip-draft">Draft</span>' : ''}
        ${ciText ? `<span class="ci ${ciClass}">${esc(ciText)}</span>` : ''}
      </div>
      ${url
        ? `<a class="card-title" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(pr.title)}</a>`
        : `<p class="card-title">${esc(pr.title)}</p>`}
      <div class="chips">${chips}</div>
      <div class="card-meta">${meta.join('<span class="sep">·</span>')}</div>
      <div class="card-actions">
        ${isNew ? `<button type="button" class="icon-btn" data-action="seen" data-key="${esc(pr.key)}" title="Mark activity as seen" aria-label="Mark activity as seen">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3C4.7 3 2 8 2 8s2.7 5 6 5 6-5 6-5-2.7-5-6-5zm0 8a3 3 0 110-6 3 3 0 010 6z"/></svg>
        </button>` : ''}
        ${pinIconButton(`gh:${pr.key}`, `${pr.repo}#${pr.number} — ${pr.title}`, url, pinned.has(`gh:${pr.key}`))}
      </div>
    </div>`;
}

/** Warnings about coverage: things that make the lane quieter than reality. */
function githubNotice(feed) {
  const notes = [];

  if (feed.sso === 'partial' || feed.sso === 'required') {
    notes.push(
      'Some organizations enforce SAML SSO and this token isn’t authorized for ' +
      'them, so their pull requests are being omitted.',
    );
  }
  if (feed.truncated) {
    notes.push(
      `Showing the most recent matches only — GitHub reports ${feed.total_open} ` +
      'open pull requests across both searches. Raise github.max_results or ' +
      'narrow the feed to see the rest.',
    );
  }
  if (feed.filtered_owners && feed.filtered_owners.length) {
    notes.push(`Hidden by your org filter: ${feed.filtered_owners.join(', ')}.`);
  }

  if (!notes.length) return '';
  return `<div class="lane-notice">${notes.map((n) => `<span>${esc(n)}</span>`).join('')}</div>`;
}

function filterPrs(items) {
  if (state.filter === 'reviews') return items.filter((pr) => !pr.is_mine);
  if (state.filter === 'mine') return items.filter((pr) => pr.is_mine);
  return items;
}

function renderGithub() {
  if (state.loading) {
    dom.githubBody.innerHTML = skeletons(4);
    return;
  }

  const feed = (state.data && state.data.github) || {};
  const all = feed.items || [];
  dom.githubCount.textContent = String(all.length);
  dom.githubCount.classList.toggle('is-hot', all.some((pr) => pr.reasons.includes('review-requested')));

  if (feed.error) {
    dom.githubBody.innerHTML = laneError('GitHub', feed.error);
    return;
  }

  const items = filterPrs(all);
  const pinned = pinnedOrigins();

  // A quiet lane must never be mistaken for "nothing to do" when the real
  // reason is an unauthorised org or a truncated search.
  const notice = githubNotice(feed);

  if (!items.length) {
    const hiddenNote = feed.drafts_hidden
      ? `<div class="lane-more">${esc(feed.drafts_hidden === 1
          ? '1 draft hidden' : `${feed.drafts_hidden} drafts hidden`)}</div>`
      : '';
    dom.githubBody.innerHTML = notice + (all.length
      ? emptyState('Nothing in this view', 'Try a different filter.')
      : emptyState('Inbox zero', 'No pull requests need you right now.')) + hiddenNote;
    return;
  }

  let html = notice + items.map((pr) => prCard(pr, pinned)).join('');
  const unseen = items.filter((pr) => pr.reasons.includes('new-activity'));
  if (unseen.length > 1) {
    html += `<button type="button" class="btn btn-quiet btn-sm" data-action="seen-all"
               style="align-self:flex-start;margin-top:4px">Mark ${unseen.length} as seen</button>`;
  }
  // Say what's being withheld rather than quietly dropping it.
  if (feed.drafts_hidden) {
    html += `<div class="lane-more">${esc(
      feed.drafts_hidden === 1 ? '1 draft hidden' : `${feed.drafts_hidden} drafts hidden`)}</div>`;
  }
  dom.githubBody.innerHTML = html;
}

/* ── pin (add feed item to local todos) ────────────────────────────── */

function pinnedOrigins() {
  const todos = (state.data && state.data.todos) || [];
  return new Set(todos.filter((t) => !t.done && t.origin).map((t) => t.origin));
}

/** `already` is a boolean — callers do the set lookup, so a single card can be
 *  rendered without building one. */
function pinIconButton(origin, title, url, already) {
  return `<button type="button" class="icon-btn ${already ? 'is-done' : ''}"
            data-action="pin" data-origin="${esc(origin)}" data-title="${esc(title)}"
            data-link="${esc(url || '')}" ${already ? 'disabled' : ''}
            title="${already ? 'Already on your list' : 'Add to your to-do list'}"
            aria-label="${already ? 'Already on your list' : 'Add to your to-do list'}">
            ${already
              ? '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.2 11.6L3 8.4l1.1-1.1 2.1 2.1 5.6-5.6L13 4.9z"/></svg>'
              : '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M7.25 3h1.5v4.25H13v1.5H8.75V13h-1.5V8.75H3v-1.5h4.25z"/></svg>'}
          </button>`;
}

function pinButton(origin, title, url, already) {
  return `<div class="card-actions">${pinIconButton(origin, title, url, already)}</div>`;
}

/* ── render ────────────────────────────────────────────────────────── */

function render() {
  renderBanner();
  renderFreshness();

  if (state.fatal && !state.data) {
    dom.todosBody.innerHTML = laneError('Server', state.fatal);
    for (const body of [dom.calBody, dom.lunchBody, dom.jiraBody, dom.githubBody,
                        dom.mailBody, dom.slackBody]) {
      body.innerHTML = '';
    }
    return;
  }

  // Isolated: a bug rendering one lane shouldn't blank the other five.
  const lanes = [
    ['Calendar', renderCalendar, dom.calBody],
    ['CaterCow', renderLunch, dom.lunchBody],
    ['To do', renderTodos, dom.todosBody],
    ['Jira', renderJira, dom.jiraBody],
    ['GitHub', renderGithub, dom.githubBody],
    ['Gmail', renderMail, dom.mailBody],
    ['Slack', renderSlack, dom.slackBody],
  ];

  for (const [label, renderer, body] of lanes) {
    try {
      renderer();
    } catch (error) {
      console.error(`Failed to render the ${label} lane`, error);
      body.innerHTML = laneError(label, `Could not render: ${error.message}`);
    }
  }
}

/* ── actions ───────────────────────────────────────────────────────── */

function localTodos() {
  return (state.data && state.data.todos) || [];
}

async function addTodo(title, link, origin) {
  try {
    const todo = await api('/api/todos', { method: 'POST', body: { title, link, origin } });
    if (state.data) state.data.todos = [todo, ...localTodos()];
    render();
  } catch (error) {
    toast(`Could not add: ${error.message}`);
  }
}

async function toggleTodo(id, done) {
  const todos = localTodos();
  const todo = todos.find((t) => t.id === id);
  if (!todo) return;

  const previous = todo.done;
  todo.done = done;                  // optimistic
  todo.completed_at = done ? new Date().toISOString() : null;
  render();

  try {
    await api(`/api/todos/${id}`, { method: 'PATCH', body: { done } });
  } catch (error) {
    todo.done = previous;
    render();
    toast(`Could not update: ${error.message}`);
  }
}

async function deleteTodo(id) {
  const before = localTodos();
  if (state.data) state.data.todos = before.filter((t) => t.id !== id);
  render();
  try {
    await api(`/api/todos/${id}`, { method: 'DELETE' });
  } catch (error) {
    if (state.data) state.data.todos = before;
    render();
    toast(`Could not delete: ${error.message}`);
  }
}

async function markSeen(keys) {
  try {
    await api('/api/seen', { method: 'POST', body: { keys } });
    await load({ force: true, quiet: true });
  } catch (error) {
    toast(`Could not mark seen: ${error.message}`);
  }
}

/* ── events ────────────────────────────────────────────────────────── */

dom.addForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const title = dom.addInput.value.trim();
  if (!title) return;
  dom.addInput.value = '';
  addTodo(title, null, null);
});

dom.refresh.addEventListener('click', () => load({ force: true }));

dom.doneToggle.addEventListener('click', () => {
  state.doneOpen = !state.doneOpen;
  renderTodos();
});

dom.clearDone.addEventListener('click', async () => {
  try {
    await api('/api/todos/clear-completed', { method: 'POST', body: {} });
    if (state.data) state.data.todos = localTodos().filter((t) => !t.done);
    state.doneOpen = false;
    render();
  } catch (error) {
    toast(`Could not clear: ${error.message}`);
  }
});

dom.githubFilters.addEventListener('click', (event) => {
  const button = event.target.closest('.filter');
  if (!button) return;
  state.filter = button.dataset.filter;
  for (const node of dom.githubFilters.querySelectorAll('.filter')) {
    node.classList.toggle('is-active', node === button);
  }
  renderGithub();
});

// One delegated handler for every row/card action.
document.addEventListener('click', (event) => {
  const target = event.target.closest('[data-action]');
  if (!target) return;

  const action = target.dataset.action;

  if (action === 'toggle') {
    const row = target.closest('.todo');
    if (row) toggleTodo(row.dataset.id, target.checked);
    return;
  }

  if (action === 'delete') {
    const row = target.closest('.todo');
    if (row) deleteTodo(row.dataset.id);
    return;
  }

  if (action === 'pin') {
    target.disabled = true;
    addTodo(target.dataset.title, target.dataset.link || null, target.dataset.origin);
    return;
  }

  if (action === 'seen') {
    markSeen([target.dataset.key]);
    return;
  }

  if (action === 'seen-all') {
    const feed = (state.data && state.data.github) || {};
    const keys = filterPrs(feed.items || [])
      .filter((pr) => pr.reasons.includes('new-activity'))
      .map((pr) => pr.key);
    if (keys.length) markSeen(keys);
  }
});

const FILTERS = ['all', 'reviews', 'mine'];

document.addEventListener('keydown', (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;

  const tag = (event.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea') {
    if (event.key === 'Escape') event.target.blur();
    return;
  }

  if (event.key === 'n') {
    event.preventDefault();
    dom.addInput.focus();
  } else if (event.key === 'r') {
    event.preventDefault();
    load({ force: true });
  } else if (event.key === 'f') {
    event.preventDefault();
    const next = FILTERS[(FILTERS.indexOf(state.filter) + 1) % FILTERS.length];
    dom.githubFilters.querySelector(`[data-filter="${next}"]`).click();
  }
});

/* ── boot ──────────────────────────────────────────────────────────── */

el('today-date').textContent = new Date().toLocaleDateString(undefined, {
  weekday: 'long', month: 'long', day: 'numeric',
});

render();
load();

setInterval(renderFreshness, 1000);
setInterval(() => {
  if (!document.hidden) load({ quiet: true });
}, REFRESH_MS);

// A tab left open overnight should catch up the moment it's looked at again.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && state.fetchedAt && Date.now() - state.fetchedAt > REFRESH_MS) {
    load({ quiet: true });
  }
});

// Explicit contract for settings.js, rather than leaning on implicit globals.
window.App = { api, toast, esc, load, state };
