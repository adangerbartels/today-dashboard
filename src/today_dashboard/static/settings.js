'use strict';

/* Setup wizard for every connection.
 *
 * Secrets are write-only: the server sends a masked hint so we can show that
 * something is stored, and never the value. A blank secret field on save
 * therefore means "keep what's already there".
 *
 * Google is the odd one out — its credential comes from an OAuth redirect
 * rather than a paste, so that panel has a Connect step before Test & save. */

(() => {
  const { api, esc, toast, load } = window.App;

  const dialog = document.getElementById('settings');
  const openButton = document.getElementById('open-settings');

  /** Per-source shape: which fields to submit, which is secret, which env var
   *  shadows it, and how its picker maps onto config. */
  const SOURCES = {
    jira: {
      label: 'Jira',
      fields: ['base_url', 'email', 'api_token', 'jql'],
      secret: 'api_token',
      env: 'JIRA_API_TOKEN',
      noun: 'issues match your JQL',
    },
    github: {
      label: 'GitHub',
      fields: ['token', 'extra_query'],
      secret: 'token',
      env: 'GITHUB_TOKEN',
      noun: 'pull requests need attention',
      picker: { selected: 'orgs', known: 'known_owners', from: 'orgs' },
    },
    google: {
      label: 'Google',
      fields: ['client_id', 'client_secret', 'gmail_query'],
      secret: 'client_secret',
      env: 'GOOGLE_REFRESH_TOKEN',
      noun: 'events left today',
      oauth: true,
      picker: { selected: 'calendar_ids', known: 'known_calendars', from: 'calendars' },
    },
    slack: {
      label: 'Slack',
      fields: ['token', 'refresh_token', 'client_id', 'client_secret'],
      secret: 'token',
      // Rotation credentials are secret too, and masked the same way.
      extraSecrets: { refresh_token: 'refresh_hint', client_secret: 'client_secret_hint' },
      env: 'SLACK_TOKEN',
      noun: 'unread messages',
      picker: { selected: 'channels', known: 'known_channels', from: 'channels' },
    },
    catercow: {
      label: 'CaterCow',
      fields: ['cookie', 'orders_path', 'email_query', 'selected_pattern', 'horizon_days'],
      secret: 'cookie',
      extraSecrets: {},
      env: 'CATERCOW_COOKIE',
      noun: 'lunch days still to pick',
      weekdays: true,
    },
  };

  const panels = {};
  for (const section of dialog.querySelectorAll('.conn')) {
    const source = section.dataset.source;
    const spec = SOURCES[source];
    panels[source] = {
      source,
      spec,
      section,
      status: section.querySelector('[data-role="status"]'),
      result: section.querySelector('[data-role="result"]'),
      notes: section.querySelector('[data-role="notes"]'),
      picker: section.querySelector('[data-role="picker"]'),
      pickerList: section.querySelector('[data-role="picker-list"]'),
      connect: section.querySelector('[data-role="connect"]'),
      test: section.querySelector('[data-role="test"]'),
      save: section.querySelector('[data-role="save"]'),
      disconnect: section.querySelector('[data-role="disconnect"]'),
      inputs: Object.fromEntries(
        spec.fields.map((name) => [name, section.querySelector(`[name="${name}"]`)]),
      ),
      // Entries currently rendered in the picker, so a save can re-derive them.
      entries: [],
    };
  }

  let lastFailure = null;   // source whose last attempt failed → offer "Save anyway"

  /** Every field on a panel whose value must stay masked. */
  function secretInputs(panel) {
    const names = [panel.spec.secret, ...Object.keys(panel.spec.extraSecrets || {})];
    return names.map((name) => panel.inputs[name]).filter(Boolean);
  }

  /* ── picker ─────────────────────────────────────────────────────── */

  /** Normalise the many shapes into {id, name, badge}. */
  function entriesFrom(source, list, account) {
    if (!Array.isArray(list)) return [];

    return list.map((entry) => {
      if (typeof entry === 'string') {
        // github's known_owners is a flat list of logins.
        return { id: entry, name: null, badge: entry === account ? 'you' : null };
      }
      if (source === 'github') {
        return { id: entry.login, name: entry.name, badge: entry.login === account ? 'you' : null };
      }
      if (source === 'slack') {
        return {
          id: entry.id,
          name: `${entry.private ? '🔒 ' : '#'}${entry.name || entry.id}`,
          badge: null,
        };
      }
      return { id: entry.id, name: entry.name, badge: entry.primary ? 'primary' : null };
    }).filter((entry) => entry.id);
  }

  function renderPicker(panel, entries, selected) {
    panel.entries = entries;
    if (!entries.length) {
      panel.picker.hidden = true;
      return;
    }

    const allowed = new Set((selected || []).map((v) => String(v).toLowerCase()));
    panel.picker.hidden = false;
    panel.pickerList.innerHTML = entries.map((entry) => {
      const checked = allowed.size === 0 || allowed.has(String(entry.id).toLowerCase());
      const label = entry.name && entry.name !== entry.id
        ? `<span class="org-login">${esc(entry.id)}</span><span class="org-name">${esc(entry.name)}</span>`
        : `<span class="org-login">${esc(entry.name || entry.id)}</span>`;
      return `
        <label class="org-row">
          <input type="checkbox" data-pick="${esc(entry.id)}" ${checked ? 'checked' : ''}>
          ${label}
          ${entry.badge ? `<span class="org-you">${esc(entry.badge)}</span>` : ''}
        </label>`;
    }).join('');
  }

  /** All ticked means "no filter"; none ticked isn't a filter either. */
  function collectPicker(panel) {
    if (!panel.spec.picker) return null;
    const boxes = [...panel.pickerList.querySelectorAll('[data-pick]')];
    if (!boxes.length) return undefined;   // nothing discovered — leave stored value
    const picked = boxes.filter((box) => box.checked).map((box) => box.dataset.pick);
    return picked.length === boxes.length || picked.length === 0 ? [] : picked;
  }

  function syncPickerBoxes(panel, stored) {
    const allowed = new Set((stored || []).map((v) => String(v).toLowerCase()));
    for (const box of panel.pickerList.querySelectorAll('[data-pick]')) {
      box.checked = allowed.size === 0 || allowed.has(box.dataset.pick.toLowerCase());
    }
  }

  /* ── status / result rendering ───────────────────────────────────── */

  function setStatus(panel, info) {
    const fromEnv = (info.env_overrides || []).includes(panel.spec.env);
    const live = panel.source === 'google' ? info.connected : info.configured;

    panel.status.classList.toggle('is-live', live && !fromEnv);
    panel.status.classList.toggle('is-env', fromEnv);

    if (fromEnv) panel.status.textContent = `From ${panel.spec.env}`;
    else if (live) panel.status.textContent = info.account ? `Connected · ${info.account}` : 'Connected';
    else panel.status.textContent = 'Not connected';
  }

  function showResult(panel, kind, message, detail) {
    panel.result.hidden = false;
    panel.result.className = `conn-result is-${kind}`;
    panel.result.innerHTML =
      `<strong>${esc(message)}</strong>${detail ? `<span class="detail">${esc(detail)}</span>` : ''}`;
  }

  function clearResult(panel) {
    panel.result.hidden = true;
    panel.result.textContent = '';
    if (panel.notes) { panel.notes.hidden = true; panel.notes.textContent = ''; }
  }

  function showNotes(panel, notes) {
    if (!panel.notes) return;
    if (!notes || !notes.length) {
      panel.notes.hidden = true;
      panel.notes.textContent = '';
      return;
    }
    panel.notes.hidden = false;
    panel.notes.innerHTML = notes
      .map((n) => `<div class="conn-note is-${esc(n.level)}">${esc(n.message)}</div>`)
      .join('');
  }

  function busy(button, on) {
    if (!button) return;
    button.classList.toggle('is-busy', on);
    button.disabled = on;
  }

  function successDetail(source, result) {
    if (!result) return null;
    const bits = [];
    if (typeof result.count === 'number') {
      bits.push(result.count === 0
        ? `No ${SOURCES[source].noun} right now`
        : `${result.count} ${SOURCES[source].noun}`);
    }
    if (typeof result.mail_count === 'number') {
      bits.push(`${result.mail_count} unread mail`);
    }
    if (result.orgs) bits.push(plural(result.orgs.length, 'organization'));
    if (result.calendars) bits.push(plural(result.calendars.length, 'calendar'));
    if (result.channels) bits.push(plural(result.channels.length, 'channel'));
    if (result.workspace) bits.push(result.workspace);
    if (result.scopes && result.scopes.length) bits.push(`scopes: ${result.scopes.join(', ')}`);
    if (typeof result.rate_remaining === 'number') {
      bits.push(`${result.rate_remaining.toLocaleString()} API calls left this hour`);
    }
    return bits.join(' · ') || null;
  }

  function plural(n, word) {
    return n === 1 ? `1 ${word}` : `${n} ${word}s`;
  }

  /* ── panel state ────────────────────────────────────────────────── */

  function renderPanel(source, info) {
    const panel = panels[source];
    const spec = panel.spec;
    setStatus(panel, info);

    const extraSecrets = spec.extraSecrets || {};
    for (const [name, input] of Object.entries(panel.inputs)) {
      if (!input) continue;

      if (name === spec.secret || name in extraSecrets) {
        input.value = '';
        const hint = name === spec.secret
          ? (info.token_hint || info.secret_hint)
          : info[extraSecrets[name]];
        input.placeholder = hint
          ? `${hint} — leave blank to keep`
          : input.dataset.placeholder || input.placeholder;
      } else if (info[name] !== undefined) {
        input.value = info[name] || '';
      }
    }

    // Open the rotation section unprompted when it's the thing that needs doing.
    if (source === 'slack' && info.rotating && !info.can_renew) {
      const rotation = document.getElementById('slack-rotation');
      if (rotation) rotation.open = true;
    }

    if (spec.weekdays) {
      const chosen = new Set((info.lunch_days || []).map(Number));
      for (const box of document.querySelectorAll('#lunch-weekdays [data-weekday]')) {
        box.checked = chosen.has(Number(box.dataset.weekday));
      }
      if (panel.inputs.email_query && !info.email_query) {
        panel.inputs.email_query.placeholder = info.email_query_default || '';
      }
      const state = document.getElementById('lunch-email-state');
      if (state) {
        state.textContent = info.email_available
          ? 'Reading confirmation emails via your Google connection (subjects only).'
          : 'Google isn’t connected, so only the session cookie below can be used.';
      }
    }

    if (source === 'google') {
      const redirect = panel.section.querySelector('[data-role="redirect"]');
      if (redirect) redirect.textContent = info.redirect_uri || '';
      if (panel.inputs.gmail_query && !info.gmail_query) {
        panel.inputs.gmail_query.placeholder = info.gmail_query_default || '';
      }
      // Consent has to happen before there's anything to test — but always keep
      // a way back. A stored token can be revoked by Google, and hiding Connect
      // whenever one exists leaves no route out of that.
      const connected = !!info.connected;
      panel.connect.hidden = false;
      panel.connect.textContent = connected
        ? 'Reconnect with Google' : 'Connect with Google';
      panel.connect.classList.toggle('btn-accent', !connected);
      panel.save.hidden = !connected;
      panel.test.hidden = !connected;
      panel.disconnect.hidden = !connected;
    } else {
      panel.disconnect.hidden = !(info.token_hint || info.secret_hint);
    }

    if (spec.picker) {
      const known = info[spec.picker.known] || [];
      renderPicker(panel, entriesFrom(source, known, info.account), info[spec.picker.selected] || []);
    }

    const shadowed = info.env_overrides || [];
    if (shadowed.length) {
      showResult(panel, 'warn',
        `Environment overrides in effect: ${shadowed.join(', ')}`,
        'Anything saved here is ignored until those are unset.');
    }
  }

  function renderPanelPreservingResult(source, settings, panel) {
    if (!settings) return;
    const keep = { html: panel.result.innerHTML, cls: panel.result.className };
    renderPanel(source, settings[source]);
    panel.result.hidden = false;
    panel.result.className = keep.cls;
    panel.result.innerHTML = keep.html;
  }

  /* ── actions ────────────────────────────────────────────────────── */

  function collect(source) {
    const panel = panels[source];
    const values = {};
    for (const [name, input] of Object.entries(panel.inputs)) values[name] = input.value;

    if (panel.spec.picker) {
      const picked = collectPicker(panel);
      if (picked !== undefined) values[panel.spec.picker.selected] = picked;
    }
    if (panel.spec.weekdays) {
      values.lunch_days = [...document.querySelectorAll('#lunch-weekdays [data-weekday]')]
        .filter((box) => box.checked)
        .map((box) => Number(box.dataset.weekday));
    }
    return values;
  }

  /** Report what a cookie fetch actually returned, to tune the page pattern. */
  async function probeCaterCow() {
    const panel = panels.catercow;
    const button = panel.section.querySelector('[data-role="probe"]');
    const target = panel.section.querySelector('[data-role="probe-result"]');
    busy(button, true);
    try {
      const response = await api('/api/catercow/probe', {
        method: 'POST', body: { values: collect('catercow') },
      });
      target.hidden = false;
      if (!response.ok) {
        target.className = 'conn-result is-bad';
        target.innerHTML = `<strong>${esc(response.error)}</strong>`;
        return;
      }
      const r = response.report;
      target.className = `conn-result is-${r.looks_signed_in ? 'ok' : 'bad'}`;
      target.innerHTML =
        `<strong>${esc(r.looks_signed_in ? 'Signed in' : 'Not signed in — served the login page')}</strong>` +
        `<span class="detail">${esc(
          `${r.bytes.toLocaleString()} bytes · ${r.date_like_strings} date-like strings · ` +
          `default pattern matched ${r.default_pattern_matches}` +
          (r.embedded_state.length ? ` · embedded state: ${r.embedded_state.join(', ')}` : '') +
          (r.distinct_dates.length ? ` · dates seen: ${r.distinct_dates.slice(0, 12).join(', ')}` : ''))}</span>`;
    } catch (error) {
      target.hidden = false;
      target.className = 'conn-result is-bad';
      target.innerHTML = `<strong>${esc(error.message)}</strong>`;
    } finally {
      busy(button, false);
    }
  }

  async function attempt(source, { save, saveAnyway = false } = {}) {
    const panel = panels[source];
    const button = save ? panel.save : panel.test;
    busy(button, true);
    clearResult(panel);

    try {
      const body = { source, values: collect(source) };
      if (saveAnyway) body.save_anyway = true;

      const response = await api(`/api/settings/${save ? 'save' : 'test'}`, { method: 'POST', body });

      if (!response.ok) {
        const overridable = save && response.kind === 'unverified';
        lastFailure = overridable ? source : null;
        panel.save.textContent = overridable ? 'Save anyway' : 'Test & save';
        showResult(panel, 'bad', response.error || 'Could not connect',
          overridable ? 'Press “Save anyway” to store these values regardless.' : null);
        return;
      }

      lastFailure = null;
      panel.save.textContent = 'Test & save';

      const who = response.result && response.result.account;
      const verb = save ? 'Saved' : 'Connected';
      if (response.warning) {
        showResult(panel, 'warn', response.warning, successDetail(source, response.result));
      } else {
        showResult(panel, 'ok', who ? `${verb} — connected as ${who}` : verb,
          successDetail(source, response.result));
      }

      if (save) renderPanelPreservingResult(source, response.settings, panel);

      // Discovery returns the full list, so it wins over the stored subset.
      const spec = panel.spec;
      if (spec.picker && response.result && response.result[spec.picker.from]) {
        const stored = save && response.settings
          ? response.settings[source][spec.picker.selected]
          : (collectPicker(panel) || []);
        renderPicker(panel,
          entriesFrom(source, response.result[spec.picker.from], who),
          stored);
        if (save) syncPickerBoxes(panel, stored);
      }
      showNotes(panel, response.result && response.result.notes);

      if (save) await load({ force: true, quiet: true });
    } catch (error) {
      showResult(panel, 'bad', error.message);
    } finally {
      busy(button, false);
    }
  }

  /** Google: save the client, open consent, then wait for the callback. */
  async function connectGoogle() {
    const panel = panels.google;
    busy(panel.connect, true);
    clearResult(panel);

    try {
      const response = await api('/api/google/connect', {
        method: 'POST',
        body: { values: collect('google') },
      });
      if (!response.ok) {
        showResult(panel, 'bad', response.error || 'Could not start sign-in');
        return;
      }

      window.open(response.auth_url, '_blank', 'noopener');
      showResult(panel, 'neutral', 'Waiting for Google…',
        'Approve access in the tab that just opened. This panel updates itself.');
      await waitForGoogle(panel);
    } catch (error) {
      showResult(panel, 'bad', error.message);
    } finally {
      busy(panel.connect, false);
    }
  }

  /** Poll settings until the callback has stored a refresh token. */
  async function waitForGoogle(panel, timeoutMs = 180_000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      let settings;
      try {
        settings = await api('/api/settings');
      } catch {
        continue;   // server busy or restarting; keep waiting
      }
      if (settings.google.connected) {
        renderPanel('google', settings.google);
        showResult(panel, 'ok', 'Google connected', 'Discovering your calendars…');
        await attempt('google', { save: true });
        await load({ force: true, quiet: true });
        return;
      }
    }
    showResult(panel, 'warn', 'Still not connected',
      'The sign-in tab may have been closed or declined. Press Connect to retry.');
  }

  async function disconnect(source) {
    const panel = panels[source];
    const name = panel.spec.label;
    const extra = source === 'google'
      ? '\n\nThis also revokes the app\'s access with Google.'
      : '\n\nIt can\'t be recovered from here — you\'d need to paste it again or create a new one.';
    if (!window.confirm(`Disconnect ${name}?${extra}`)) return;

    busy(panel.disconnect, true);
    try {
      const response = await api('/api/settings/disconnect', { method: 'POST', body: { source } });
      renderPanel(source, response.settings[source]);
      clearResult(panel);
      showResult(panel, 'neutral', `${name} disconnected`, 'Those lanes are back to demo data.');
      await load({ force: true, quiet: true });
    } catch (error) {
      toast(`Could not disconnect: ${error.message}`);
    } finally {
      busy(panel.disconnect, false);
    }
  }

  /* ── open / close ───────────────────────────────────────────────── */

  async function open() {
    if (dialog.open) return;
    dialog.showModal();

    for (const panel of Object.values(panels)) {
      panel.status.className = 'conn-status';
      panel.status.textContent = 'Checking…';
      clearResult(panel);
    }

    try {
      const settings = await api('/api/settings');
      document.getElementById('settings-path').textContent = settings.config_path;
      document.getElementById('settings-path-inline').textContent =
        settings.config_path.split('/').pop();
      for (const source of Object.keys(panels)) renderPanel(source, settings[source]);

      // Land on the first thing still needing attention.
      const pending = Object.keys(panels).find((source) => !(
        source === 'google' ? settings.google.connected : settings[source].configured
      ) && source !== 'catercow');   // CaterCow needs no credential of its own
      if (pending) {
        const inputs = panels[pending].inputs;
        const first = Object.values(inputs).find((input) => input && !input.hidden);
        if (first) first.focus();
      }
    } catch (error) {
      for (const panel of Object.values(panels)) {
        showResult(panel, 'bad', `Could not load settings: ${error.message}`);
      }
    }
  }

  /* ── wiring ─────────────────────────────────────────────────────── */

  openButton.addEventListener('click', open);
  document.getElementById('close-settings').addEventListener('click', dismiss);

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-action="open-settings"]')) open();
  });

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dismiss();
  });

  // Handle Escape ourselves so dismissal always runs the clearing path.
  dialog.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    dismiss();
  });

  for (const panel of Object.values(panels)) {
    panel.test?.addEventListener('click', () => attempt(panel.source, { save: false }));
    panel.save?.addEventListener('click', () =>
      attempt(panel.source, { save: true, saveAnyway: lastFailure === panel.source }));
    panel.disconnect?.addEventListener('click', () => disconnect(panel.source));
    panel.connect?.addEventListener('click', connectGoogle);
    panel.section.querySelector('[data-role="probe"]')
      ?.addEventListener('click', probeCaterCow);

    for (const input of Object.values(panel.inputs)) {
      if (!input) continue;
      input.addEventListener('input', () => {
        if (lastFailure === panel.source) {
          lastFailure = null;
          panel.save.textContent = 'Test & save';
        }
      });
      input.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' || input.tagName === 'TEXTAREA') return;
        event.preventDefault();
        // Google can't be tested before consent, so Enter starts the sign-in.
        if (panel.source === 'google' && !panel.connect.hidden) connectGoogle();
        else attempt(panel.source, { save: true, saveAnyway: lastFailure === panel.source });
      });
    }

    if (panel.pickerList) {
      // One-way on purpose: "clear all" would mean the same as "select all",
      // since an empty include-list includes everything.
      panel.section.querySelector('[data-role="picker-all"]')
        .addEventListener('click', () => {
          for (const box of panel.pickerList.querySelectorAll('[data-pick]')) box.checked = true;
        });
    }

    // Wire every masked field's Show/Hide toggle, in DOM order.
    for (const input of secretInputs(panel)) {
      input.dataset.placeholder = input.placeholder;
      const reveal = input.parentElement.querySelector('[data-role^="reveal"]');
      if (!reveal) continue;
      reveal.addEventListener('click', () => {
        const hidden = input.type === 'password';
        input.type = hidden ? 'text' : 'password';
        reveal.textContent = hidden ? 'Hide' : 'Show';
        reveal.setAttribute('aria-label', hidden ? 'Hide secret' : 'Show secret');
      });
    }
  }

  /** Leave no secret sitting in the DOM once the modal is dismissed. */
  function clearSecrets() {
    for (const panel of Object.values(panels)) {
      for (const input of secretInputs(panel)) {
        input.type = 'password';
        input.value = '';
        const reveal = input.parentElement.querySelector('[data-role^="reveal"]');
        if (reveal) reveal.textContent = 'Show';
      }
    }
  }

  /** The only way this modal should be closed.
   *
   *  Clearing is done here rather than in a `close` listener because the event
   *  isn't reliably dispatched everywhere this runs — and secrets lingering in
   *  the DOM is not something to leave to an event that might not fire. The
   *  listeners below stay as a backstop for paths that bypass this.
   */
  function dismiss() {
    clearSecrets();
    if (dialog.open) dialog.close();
  }

  dialog.addEventListener('close', clearSecrets);
  dialog.addEventListener('cancel', clearSecrets);

  document.addEventListener('keydown', (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey || dialog.open) return;
    const tag = (event.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;
    if (event.key === 's') {
      event.preventDefault();
      open();
    }
  });
})();
