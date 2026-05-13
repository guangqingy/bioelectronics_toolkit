/* DataProcess shared frontend core. Keep page-specific logic in templates. */

let _toastT;

function escHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function toast(msg, isErr) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg || '';
  t.className = 'show' + (isErr ? ' err' : '');
  if (isErr) showErrorBanner(msg || 'Request failed');
  clearTimeout(_toastT);
  _toastT = setTimeout(() => t.className = '', 3000);
}

function dpProgressFromMessage(msg) {
  const text = String(msg || '');
  const percent = text.match(/(^|[^0-9])([0-9]{1,3})\s*%/);
  if (percent) {
    const n = Math.max(0, Math.min(100, Number(percent[2])));
    return Number.isFinite(n) ? n : null;
  }
  const ratio = text.match(/(^|[^0-9])([0-9]+)\s*\/\s*([0-9]+)/);
  if (ratio) {
    const cur = Number(ratio[2]);
    const total = Number(ratio[3]);
    if (Number.isFinite(cur) && Number.isFinite(total) && total > 0) {
      return Math.max(0, Math.min(100, Math.round((cur / total) * 100)));
    }
  }
  return null;
}

function setStatus(id, msg, cls, progress) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!cls) {
    el.className = 'status-bar';
    el.textContent = msg || '';
    return;
  }
  const norm = String(cls).startsWith('status-') ? cls : ('status-' + cls);
  el.className = 'status-bar ' + cls + ' ' + norm;
  const text = document.createElement('span');
  text.className = 'status-text';
  text.textContent = msg || '';
  el.replaceChildren(text);
  const progressValue = typeof progress === 'number' ? progress : dpProgressFromMessage(msg);
  if (norm === 'status-loading' && typeof progressValue === 'number') {
    const track = document.createElement('div');
    track.className = 'status-progress';
    const bar = document.createElement('div');
    bar.className = 'status-progress-bar';
    bar.style.width = `${Math.max(0, Math.min(100, progressValue))}%`;
    track.appendChild(bar);
    el.appendChild(track);
  }
  if (norm === 'status-error' || cls === 'err') {
    showErrorBanner(msg || 'Request failed');
  }
}

function showErrorBanner(msg) {
  const banner = document.getElementById('errorBanner');
  const text = document.getElementById('errorBannerText');
  if (!banner || !text) return;
  text.textContent = msg || 'Request failed';
  banner.hidden = false;
}

function dismissErrorBanner() {
  const banner = document.getElementById('errorBanner');
  if (banner) banner.hidden = true;
}

function openShortcutModal() {
  const modal = document.getElementById('shortcutModal');
  if (!modal) return;
  modal.classList.add('show');
  modal.setAttribute('aria-hidden', 'false');
}

function closeShortcutModal() {
  const modal = document.getElementById('shortcutModal');
  if (!modal) return;
  modal.classList.remove('show');
  modal.setAttribute('aria-hidden', 'true');
}

const DP_STATIC_COMMANDS = [
  {label: 'ABF demo data', url: '/abf/viewer?demo=abf', section: 'Demo', keywords: 'sample patch clamp abf example'},
  {label: 'EChem demo data', url: '/echem/photocurrent?demo=echem', section: 'Demo', keywords: 'sample electrochemistry photocurrent csv example'},
  {label: 'Fluorescence ROI demo data', url: '/fluorescence/roi?demo=fluorescence', section: 'Demo', keywords: 'sample tiff stack fluorescence roi example'},
  {label: 'Photocurrent pipelines', url: '/scripts/photocurrent', section: 'Pipeline', keywords: 'pipeline script photocurrent intensity response peak overlay heatmap decay'},
  {label: 'EMG pipelines', url: '/scripts/emg', section: 'Pipeline', keywords: 'pipeline script emg waveform overlay bar heatmap'},
  {label: 'EChem curve pipelines', url: '/scripts/echem_curves', section: 'Pipeline', keywords: 'pipeline script electrochemistry curve photovoltage photocurrent'},
  {label: 'Cell viability pipelines', url: '/scripts/viability', section: 'Pipeline', keywords: 'pipeline script live dead viability stardist watershed'},
  {label: 'Run history', url: '/runs', section: 'Workflow', keywords: 'manifest previous outputs history'},
  {label: 'Version API', url: '/api/version', section: 'Developer', keywords: 'version commit api metadata'},
];

let _commandPaletteIndex = 0;

function dpCommandPaletteItems() {
  const viewCommands = [];
  if (typeof VIEW_URLS === 'object' && typeof SETTINGS_VIEW_LABELS === 'object') {
    Object.entries(VIEW_URLS).forEach(([id, url]) => {
      viewCommands.push({
        label: SETTINGS_VIEW_LABELS[id] || id.replaceAll('_', ' '),
        url,
        section: 'Tool',
        keywords: `${id.replaceAll('_', ' ')} ${url}`,
      });
    });
  }
  return viewCommands.concat(DP_STATIC_COMMANDS);
}

function dpCommandScore(command, query) {
  const q = query.trim().toLowerCase();
  if (!q) return 1;
  const hay = `${command.label} ${command.section} ${command.keywords || ''} ${command.url}`.toLowerCase();
  if (hay.includes(q)) return 100 + q.length;
  let pos = 0;
  let score = 0;
  for (const ch of q) {
    const found = hay.indexOf(ch, pos);
    if (found === -1) return 0;
    score += found === pos ? 3 : 1;
    pos = found + 1;
  }
  return score;
}

function renderCommandPalette() {
  const input = document.getElementById('commandSearch');
  const list = document.getElementById('commandList');
  if (!input || !list) return;
  const query = input.value || '';
  const rows = dpCommandPaletteItems()
    .map(command => ({command, score: dpCommandScore(command, query)}))
    .filter(row => row.score > 0)
    .sort((a, b) => b.score - a.score || a.command.label.localeCompare(b.command.label))
    .slice(0, 12)
    .map(row => row.command);
  _commandPaletteIndex = Math.min(_commandPaletteIndex, Math.max(0, rows.length - 1));
  if (!rows.length) {
    list.innerHTML = '<div class="command-empty">No matching command</div>';
    return;
  }
  list.innerHTML = rows.map((command, i) => `
    <button class="command-item ${i === _commandPaletteIndex ? 'active' : ''}" type="button" data-url="${escHtml(command.url)}">
      <span class="command-main">${escHtml(command.label)}</span>
      <span class="command-meta">${escHtml(command.section)} · ${escHtml(command.url)}</span>
    </button>
  `).join('');
  list.querySelectorAll('.command-item').forEach((btn, i) => {
    btn.addEventListener('mouseenter', () => {
      _commandPaletteIndex = i;
      list.querySelectorAll('.command-item').forEach((item, j) => {
        item.classList.toggle('active', j === _commandPaletteIndex);
      });
    });
    btn.addEventListener('click', () => runCommandPaletteSelection());
  });
}

function openCommandPalette() {
  const modal = document.getElementById('commandPalette');
  const input = document.getElementById('commandSearch');
  if (!modal || !input) return;
  _commandPaletteIndex = 0;
  input.value = '';
  modal.classList.add('show');
  modal.setAttribute('aria-hidden', 'false');
  renderCommandPalette();
  setTimeout(() => input.focus(), 0);
}

function closeCommandPalette() {
  const modal = document.getElementById('commandPalette');
  if (!modal) return;
  modal.classList.remove('show');
  modal.setAttribute('aria-hidden', 'true');
}

function runCommandPaletteSelection() {
  const items = Array.from(document.querySelectorAll('#commandList .command-item'));
  const item = items[_commandPaletteIndex] || items[0];
  if (!item) return;
  const url = item.getAttribute('data-url');
  if (url) window.location.href = url;
}

function dpConfirmAction(options) {
  const opts = options || {};
  return new Promise(resolve => {
    let modal = document.getElementById('dpConfirmModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'dpConfirmModal';
      modal.className = 'modal-overlay';
      modal.innerHTML = `
        <div class="modal-card confirm-card" role="dialog" aria-modal="true" aria-labelledby="dpConfirmTitle">
          <div class="modal-header">
            <div>
              <div class="modal-title" id="dpConfirmTitle"></div>
              <div class="prefs-sub" id="dpConfirmSub"></div>
            </div>
          </div>
          <div class="modal-body">
            <div class="confirm-detail" id="dpConfirmDetail"></div>
            <div class="prefs-actions" style="margin-top:14px">
              <button class="btn-secondary" type="button" data-confirm="cancel">Cancel</button>
              <button class="btn-primary" type="button" data-confirm="ok">Continue</button>
            </div>
          </div>
        </div>
      `;
      document.body.appendChild(modal);
    }
    modal.querySelector('#dpConfirmTitle').textContent = opts.title || 'Confirm action';
    modal.querySelector('#dpConfirmSub').textContent = opts.subtitle || '';
    modal.querySelector('#dpConfirmDetail').innerHTML = opts.html || escHtml(opts.message || '');
    const okBtn = modal.querySelector('[data-confirm="ok"]');
    const cancelBtn = modal.querySelector('[data-confirm="cancel"]');
    okBtn.textContent = opts.confirmText || 'Continue';
    okBtn.classList.toggle('btn-danger', !!opts.danger);
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');
    const finish = value => {
      modal.classList.remove('show');
      modal.setAttribute('aria-hidden', 'true');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      resolve(value);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    cancelBtn.focus();
  });
}

function dpNormalizeApiPayload(payload, responseOk) {
  if (!payload || typeof payload !== 'object') {
    return {
      ok: !!responseOk,
      data: {},
      outputs: [],
      warnings: [],
      error: responseOk ? null : 'Request failed',
      value: payload,
      _envelope: null,
    };
  }

  const isEnvelope = ['ok', 'data', 'outputs', 'warnings', 'error'].every(key =>
    Object.prototype.hasOwnProperty.call(payload, key)
  );
  if (!isEnvelope) {
    const ok = responseOk !== false && !payload.error;
    return Object.assign({}, payload, {
      ok,
      data: ok ? Object.assign({}, payload) : {},
      outputs: Array.isArray(payload.outputs) ? payload.outputs : [],
      warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
      error: ok ? null : (payload.error || 'Request failed'),
      _envelope: null,
    });
  }

  const data = payload.data && typeof payload.data === 'object' && !Array.isArray(payload.data)
    ? payload.data
    : {};
  const legacyOk = Object.prototype.hasOwnProperty.call(data, 'ok') ? data.ok : payload.ok;
  return Object.assign({}, data, payload, {
    ok: legacyOk === false ? false : payload.ok !== false,
    outputs: Array.isArray(payload.outputs) ? payload.outputs : [],
    warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
    error: payload.error || null,
    _envelope: payload,
  });
}

async function api(url, body, options) {
  const opts = options || {};
  const method = opts.method || 'POST';
  const fetchOptions = {
    method,
    headers: Object.assign({'Content-Type': 'application/json'}, opts.headers || {}),
  };
  if (method.toUpperCase() !== 'GET') {
    fetchOptions.body = JSON.stringify(body || {});
  }
  const r = await fetch(url, fetchOptions);
  const payload = await r.json().catch(() => ({}));
  const d = dpNormalizeApiPayload(payload, r.ok);
  if (!r.ok && !d.error) d.error = 'Request failed';
  return d;
}

async function dpJobGet(jobId) {
  const d = await api('/api/jobs/get', {job_id: jobId});
  if (d.error) throw new Error(d.error);
  return d.job;
}

async function dpJobList(limit, includeFinished) {
  const d = await api('/api/jobs/list', {
    limit: limit || 50,
    include_finished: includeFinished !== false,
  });
  if (d.error) throw new Error(d.error);
  return d.jobs || [];
}

async function dpJobCancel(jobId) {
  const d = await api('/api/jobs/cancel', {job_id: jobId});
  if (d.error) throw new Error(d.error);
  return d.job;
}

async function dpPollJob(jobId, onUpdate, options) {
  const opts = options || {};
  const intervalMs = Math.max(250, Number(opts.interval_ms || 1000));
  const terminal = new Set(['succeeded', 'failed', 'cancelled']);
  while (true) {
    const job = await dpJobGet(jobId);
    if (typeof onUpdate === 'function') onUpdate(job);
    if (terminal.has(job.status)) return job;
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
}

function dpNormalizeJobData(data) {
  if (!data || typeof data !== 'object') return {};
  if (
    data.data && typeof data.data === 'object' &&
    Object.prototype.hasOwnProperty.call(data, 'ok') &&
    Object.prototype.hasOwnProperty.call(data, 'error')
  ) {
    return Object.assign({}, data.data, data);
  }
  return data;
}

async function dpRunJobEndpoint(endpoint, payload, options) {
  const opts = options || {};
  const started = await api(endpoint, payload || {});
  if (started.error) throw new Error(started.error);
  const jobId = started.job_id || (started.job && started.job.job_id);
  if (!jobId) return dpNormalizeJobData(started);

  const finalJob = await dpPollJob(jobId, opts.on_update, {
    interval_ms: opts.interval_ms || 1000,
  });
  const data = dpNormalizeJobData(finalJob.data || {});
  if ((!Array.isArray(data.outputs) || !data.outputs.length) && Array.isArray(finalJob.outputs) && finalJob.outputs.length) {
    data.outputs = finalJob.outputs;
  }
  if ((!Array.isArray(data.warnings) || !data.warnings.length) && Array.isArray(finalJob.warnings) && finalJob.warnings.length) {
    data.warnings = finalJob.warnings;
  }
  if (finalJob.status === 'failed' || finalJob.status === 'cancelled') {
    throw new Error(finalJob.error || data.error || finalJob.message || 'Background job failed');
  }
  if (data.error) throw new Error(data.error);
  return data;
}

function showLogoutScreen(message) {
  document.body.innerHTML = `
    <div class="logout-screen">
      <div class="logout-card">
        <div class="logout-title">${APP_LABEL} Closed</div>
        <div class="logout-sub">${message || 'The Python service has stopped. You can close this tab now.'}</div>
      </div>
    </div>
  `;
}

async function logoutServer() {
  if (!confirm(`Close ${APP_LABEL} and stop the Python server?`)) return;

  btnBusy('logoutBtn', true, 'Closing...');
  try {
    const d = await api('/api/system/logout', {});
    toast((d && d.message) || `${APP_LABEL} is closing...`);
    setTimeout(() => showLogoutScreen('The Python service has stopped. You can close this tab now.'), 300);
  } catch (e) {
    const msg = (e && e.message) || '';
    if (/failed to fetch|networkerror/i.test(msg)) {
      setTimeout(() => showLogoutScreen('Connection ended. DataProcess Web is closed.'), 150);
      return;
    }
    btnBusy('logoutBtn', false, 'Log Out');
    toast('Log out failed: ' + msg, true);
  }
}

function btnBusy(id, busy, label) {
  const b = document.getElementById(id);
  if (!b) return;
  b.disabled = busy;
  if (busy) {
    b.dataset.label = b.dataset.label || b.textContent;
    b.innerHTML = '<span class="spinner"></span>' + (label || '');
  } else {
    b.textContent = label || b.dataset.label || 'Run';
  }
}

function populateSelect(id, options, selected) {
  const s = document.getElementById(id);
  if (!s) return;
  s.innerHTML = options.map(o =>
    `<option${o === selected ? ' selected' : ''}>${o}</option>`
  ).join('');
}

function buildTable(rows, cols) {
  if (!rows || !rows.length) return '<div class="file-list-empty">No data</div>';
  const ths = cols.map(c => `<th>${c}</th>`).join('');
  const trs = rows.map(r =>
    '<tr>' + cols.map(c => `<td>${r[c] !== undefined ? r[c] : ''}</td>`).join('') + '</tr>'
  ).join('');
  return `<div class="data-table-wrap"><table class="dp-table"><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table></div>`;
}

function setPlot(containerId, b64, fmt) {
  const c = document.getElementById(containerId);
  if (!c) return;
  if (!b64) {
    c.innerHTML = '<div class="plot-placeholder">No output</div>';
    return;
  }
  c.innerHTML = `<img src="data:image/${fmt || 'png'};base64,${b64}" alt="plot"/>`;
}

function buildFileList(containerId, files, onSelect) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!files || !files.length) {
    el.innerHTML = '<div class="file-list-empty">No files found</div>';
    dpApplyFileListFilter(containerId);
    return;
  }
  el.innerHTML = files.map((f, i) =>
    `<div class="file-item" data-idx="${i}" data-path="${f.path}" onclick="(${onSelect.toString()})(this)">${f.name}</div>`
  ).join('');
  dpApplyFileListFilter(containerId);
}

function dpFileListItemText(item) {
  return [
    item.textContent || '',
    item.getAttribute('data-path') || '',
    item.getAttribute('title') || '',
  ].join(' ').toLowerCase();
}

function dpApplyFileListFilter(listId) {
  const list = document.getElementById(listId);
  const filter = document.querySelector(`.file-filter input[data-target="${listId}"]`);
  const count = document.querySelector(`.file-filter-count[data-target="${listId}"]`);
  if (!list || !filter) return;
  const q = filter.value.trim().toLowerCase();
  const items = Array.from(list.querySelectorAll('.file-item'));
  let shown = 0;
  items.forEach(item => {
    if (!item.hasAttribute('tabindex')) item.setAttribute('tabindex', '0');
    const match = !q || dpFileListItemText(item).includes(q);
    item.hidden = !match;
    if (match) shown += 1;
  });
  if (count) count.textContent = items.length ? `${shown}/${items.length}` : '';
}

function dpEnhanceFileLists() {
  document.querySelectorAll('.file-list[id]').forEach(list => {
    if (list.dataset.filterReady === '1' || list.dataset.noFilter === '1') return;
    const listId = list.id;
    const wrap = document.createElement('div');
    wrap.className = 'file-filter';
    wrap.innerHTML = `
      <input type="search" data-target="${escHtml(listId)}" placeholder="Filter list" aria-label="Filter ${escHtml(listId)}">
      <span class="file-filter-count" data-target="${escHtml(listId)}"></span>
    `;
    list.parentNode.insertBefore(wrap, list);
    const input = wrap.querySelector('input');
    input.addEventListener('input', () => dpApplyFileListFilter(listId));
    const observer = new MutationObserver(() => dpApplyFileListFilter(listId));
    observer.observe(list, {childList: true, subtree: false});
    list.dataset.filterReady = '1';
    dpApplyFileListFilter(listId);
  });
}

function filterFileList(listId) {
  return dpApplyFileListFilter(listId);
}

function installFileListFilters() {
  return dpEnhanceFileLists();
}

function dpControlValue(control) {
  if (control.type === 'checkbox' || control.type === 'radio') return control.checked;
  return control.value;
}

function dpSetControlValue(control, value) {
  if (control.type === 'checkbox' || control.type === 'radio') control.checked = !!value;
  else control.value = value ?? '';
  control.dispatchEvent(new Event('input', {bubbles: true}));
  control.dispatchEvent(new Event('change', {bubbles: true}));
}

function dpParamTokens(raw) {
  return String(raw || '')
    .split(/[\s,|]+/)
    .map(s => s.trim().toLowerCase())
    .filter(Boolean);
}

function dpApplyParamGroups(selectId, attr) {
  const select = document.getElementById(selectId);
  if (!select) return;
  const current = String(select.value || '').toLowerCase();
  document.querySelectorAll(`[${attr}]`).forEach(group => {
    const tokens = dpParamTokens(group.getAttribute(attr));
    group.hidden = !(tokens.includes(current) || tokens.includes('*'));
  });
}

function dpBindParamGroups(selectId, attr) {
  const select = document.getElementById(selectId);
  if (!select) return;
  const boundAttr = `data-param-group-bound-${attr.replace(/[^a-z0-9]+/gi, '-')}`;
  if (select.getAttribute(boundAttr) !== '1') {
    select.addEventListener('change', () => dpApplyParamGroups(selectId, attr));
    select.setAttribute(boundAttr, '1');
  }
  dpApplyParamGroups(selectId, attr);
}

function dpApplyToggleGroups(controlId, attr) {
  const control = document.getElementById(controlId);
  if (!control) return;
  const checked = !!control.checked;
  document.querySelectorAll(`[${attr}]`).forEach(group => {
    const tokens = dpParamTokens(group.getAttribute(attr));
    const wantsOn = tokens.includes('checked') || tokens.includes('on') || tokens.includes('true');
    const wantsOff = tokens.includes('unchecked') || tokens.includes('off') || tokens.includes('false');
    group.hidden = !((checked && wantsOn) || (!checked && wantsOff) || tokens.includes('*'));
  });
}

function dpBindToggleGroups(controlId, attr) {
  const control = document.getElementById(controlId);
  if (!control) return;
  const boundAttr = `data-toggle-group-bound-${attr.replace(/[^a-z0-9]+/gi, '-')}`;
  if (control.getAttribute(boundAttr) !== '1') {
    control.addEventListener('change', () => dpApplyToggleGroups(controlId, attr));
    control.setAttribute(boundAttr, '1');
  }
  dpApplyToggleGroups(controlId, attr);
}

function dpEnhanceResettableSections() {
  document.querySelectorAll('.ctrl-section[data-resettable="true"]').forEach(section => {
    if (section.dataset.resetReady === '1') return;
    const controls = Array.from(section.querySelectorAll('input, select, textarea'))
      .filter(el => !el.disabled && el.type !== 'button' && el.type !== 'submit' && el.type !== 'file');
    if (!controls.length) return;
    const defaults = controls.map(el => [el, dpControlValue(el)]);
    let label = section.querySelector('.ctrl-label');
    if (!label) return;
    if (!label.classList.contains('ctrl-label-row')) {
      label.classList.add('ctrl-label-row');
      if (!label.children.length) {
        const text = label.textContent;
        label.textContent = '';
        const span = document.createElement('span');
        span.textContent = text;
        label.appendChild(span);
      }
    }
    const btn = document.createElement('button');
    btn.className = 'btn-secondary ctrl-section-reset';
    btn.type = 'button';
    btn.title = 'Reset this section to its initial defaults';
    btn.textContent = 'Reset';
    btn.addEventListener('click', () => {
      defaults.forEach(([el, value]) => dpSetControlValue(el, value));
      setStatus('status', 'Section defaults restored', 'ok');
    });
    label.appendChild(btn);
    section.dataset.resetReady = '1';
  });
}

function dpClickFocusedListItem(delta) {
  const active = document.activeElement;
  const list = active && active.classList && active.classList.contains('file-item')
    ? active.closest('.file-list')
    : null;
  if (!list) return false;
  const visible = Array.from(list.querySelectorAll('.file-item:not([hidden])'));
  const idx = visible.indexOf(active);
  const next = visible[idx + delta];
  if (!next) return true;
  next.focus();
  return true;
}

function dpTryGlobalAction(kind) {
  if (kind === 'save') {
    const saveFns = [
      'saveSelectedFileProfile',
      'saveGenericFileProfile',
      'saveRoiFileProfile',
      'saveGifFileProfile',
      'saveTiffFileProfile',
    ];
    for (const name of saveFns) {
      if (typeof window[name] === 'function') {
        window[name](false);
        return true;
      }
    }
  }
  if (kind === 'export') {
    const exportSelectors = [
      '#btnGenerate',
      '#btnAnalyze',
      '#btnMergeExport',
      '#btnStackExport',
      '[onclick="exportCSV()"]',
    ];
    for (const sel of exportSelectors) {
      const btn = document.querySelector(sel);
      if (btn && !btn.disabled && btn.offsetParent !== null) {
        btn.click();
        return true;
      }
    }
  }
  return false;
}

function dpInstallGlobalKeyboardShortcuts() {
  document.addEventListener('keydown', ev => {
    const tag = (ev.target && ev.target.tagName || '').toLowerCase();
    const inTextField = ['input', 'textarea', 'select'].includes(tag);
    if (ev.key === 'Escape') {
      closeShortcutModal();
      closeCommandPalette();
      dismissErrorBanner();
      return;
    }
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'k') {
      ev.preventDefault();
      openCommandPalette();
      return;
    }
    if (document.getElementById('commandPalette')?.classList.contains('show')) {
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        const count = document.querySelectorAll('#commandList .command-item').length;
        if (count) {
          _commandPaletteIndex = (_commandPaletteIndex + (ev.key === 'ArrowDown' ? 1 : -1) + count) % count;
          renderCommandPalette();
        }
        return;
      }
      if (ev.key === 'Enter') {
        ev.preventDefault();
        runCommandPaletteSelection();
        return;
      }
    }
    if (!inTextField && ev.key === '?') {
      ev.preventDefault();
      openShortcutModal();
      return;
    }
    if (!inTextField && (ev.key === 'ArrowDown' || ev.key === 'ArrowUp')) {
      if (dpClickFocusedListItem(ev.key === 'ArrowDown' ? 1 : -1)) ev.preventDefault();
      return;
    }
    if (!inTextField && ev.key === 'Enter' && ev.target?.classList?.contains('file-item')) {
      ev.preventDefault();
      ev.target.click();
      return;
    }
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 's') {
      if (dpTryGlobalAction('save')) ev.preventDefault();
      return;
    }
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'e') {
      if (dpTryGlobalAction('export')) ev.preventDefault();
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  installFileListFilters();
  dpEnhanceResettableSections();
  dpInstallGlobalKeyboardShortcuts();
  const commandSearch = document.getElementById('commandSearch');
  if (commandSearch) commandSearch.addEventListener('input', () => {
    _commandPaletteIndex = 0;
    renderCommandPalette();
  });
});
