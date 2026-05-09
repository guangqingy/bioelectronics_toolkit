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
      dismissErrorBanner();
      return;
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
  dpEnhanceFileLists();
  dpEnhanceResettableSections();
  dpInstallGlobalKeyboardShortcuts();
});
