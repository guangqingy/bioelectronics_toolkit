/* DataProcess shared frontend core. Keep page-specific logic in templates. */

let _toastT;
const DP_FILE_LIST_VIRTUAL_THRESHOLD = 160;
const DP_FILE_LIST_PAGE_SIZE = 120;
const _dpVirtualLists = {};

function dpEscapeHtml(value) {
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

function dpCompactPathForStatus(path) {
  const text = String(path || '');
  if (text.length <= 64) return text;
  const parts = text.split(/[\\/]+/).filter(Boolean);
  if (parts.length < 3) return text;
  return '…/' + parts.slice(-2).join('/');
}

function dpCompactStatusMessage(msg) {
  const text = String(msg || '');
  if (text.length <= 180) return text;
  let compact = text.replace(/(?:\/[^\s|;,]+){3,}/g, match => dpCompactPathForStatus(match));
  if (compact.length > 240) compact = compact.slice(0, 237) + '...';
  return compact;
}

function setStatus(id, msg, cls, progress) {
  const el = document.getElementById(id);
  if (!el) return;
  const fullMessage = String(msg || '');
  const compactMessage = dpCompactStatusMessage(fullMessage);
  el.title = compactMessage !== fullMessage ? fullMessage : '';
  if (!cls) {
    el.className = 'status-bar';
    el.textContent = compactMessage;
    return;
  }
  const norm = String(cls).startsWith('status-') ? cls : ('status-' + cls);
  el.className = 'status-bar ' + cls + ' ' + norm;
  const text = document.createElement('span');
  text.className = 'status-text';
  const message = document.createElement('span');
  message.className = 'status-message';
  message.textContent = compactMessage;
  if (compactMessage !== fullMessage) message.title = fullMessage;
  text.appendChild(message);
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

function showErrorBanner(msg, details, errorId) {
  const banner = document.getElementById('errorBanner');
  const text = document.getElementById('errorBannerText');
  if (!banner || !text) return;
  const payload = (msg && typeof msg === 'object') ? msg : null;
  const message = payload ? payload.error : msg;
  const technical = details || payload?.technical_details || payload?.details || '';
  const id = errorId || payload?.error_id || payload?.id || '';
  text.textContent = message || 'Request failed';
  const detailsEl = document.getElementById('errorBannerDetails');
  const detailsText = document.getElementById('errorBannerDetailsText');
  const idEl = document.getElementById('errorBannerId');
  if (idEl) {
    idEl.textContent = id ? `ID ${id}` : '';
    idEl.hidden = !id;
  }
  if (detailsEl && detailsText) {
    detailsText.textContent = technical || '';
    detailsEl.hidden = !technical;
    if (!technical) detailsEl.open = false;
  }
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

function dpVisibleText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function dpReadableId(value) {
  return dpVisibleText(String(value || '')
    .replace(/^btn/i, '')
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2'));
}

function dpLabelledByText(el) {
  const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
  return dpVisibleText(ids.map(id => document.getElementById(id)?.textContent || '').join(' '));
}

function dpNativeLabelText(el) {
  if (el.labels && el.labels.length) {
    return dpVisibleText(Array.from(el.labels).map(label => label.textContent || '').join(' '));
  }
  const wrapped = el.closest('label');
  return wrapped ? dpVisibleText(wrapped.textContent || '') : '';
}

function dpNearbyControlLabel(el) {
  const row = el.closest(
    '.param-row, .form-row, .ctrl-row, .stack-control, .stack-preview-row, ' +
    '.gif-file-path-row, .gif-slice-row, .prefs-field, details, section, .ctrl-group'
  );
  const selectors = [
    '.param-label',
    '.form-label',
    '.ctrl-label',
    '.prefs-label',
    '.stack-mini-label',
    '.gif-mini-label',
    '.lif-detail-title',
  ];
  for (const selector of selectors) {
    const label = row?.querySelector(selector);
    const text = label && !label.contains(el) ? dpVisibleText(label.textContent) : '';
    if (text) return text;
  }
  for (const sibling of [el.previousElementSibling, el.nextElementSibling]) {
    if (sibling && !/^(INPUT|SELECT|TEXTAREA|BUTTON)$/i.test(sibling.tagName || '')) {
      const text = dpVisibleText(sibling.textContent || sibling.getAttribute('title') || '');
      if (text) return text;
    }
  }
  return '';
}

function dpControlAccessibleName(el) {
  return (
    dpVisibleText(el.getAttribute('aria-label')) ||
    dpLabelledByText(el) ||
    dpNativeLabelText(el) ||
    dpNearbyControlLabel(el) ||
    dpVisibleText(el.getAttribute('data-label')) ||
    dpVisibleText(el.getAttribute('title')) ||
    dpVisibleText(el.getAttribute('placeholder')) ||
    dpVisibleText(el.value && /^(button|submit|reset)$/i.test(el.type || '') ? el.value : '') ||
    dpReadableId(el.id || el.name)
  );
}

function dpControlHasProgrammaticName(el) {
  if (dpVisibleText(el.getAttribute('aria-label')) || dpLabelledByText(el)) return true;
  if (el.labels && el.labels.length) return true;
  if (el.tagName === 'BUTTON' && dpVisibleText(el.textContent || el.value || el.title)) return true;
  return false;
}

function dpEnsureControlNames(root) {
  const scope = root || document;
  const selector = 'input:not([type="hidden"]), select, textarea, button';
  const controls = scope.querySelectorAll
    ? Array.from(scope.querySelectorAll(selector))
    : [];
  if (scope.matches?.(selector)) controls.push(scope);
  controls.forEach(el => {
    if (dpControlHasProgrammaticName(el)) return;
    const label = dpControlAccessibleName(el);
    if (label) el.setAttribute('aria-label', label);
  });
}

const DP_FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function dpVisibleFocusable(el) {
  return !!(el && (el.offsetParent || el.getClientRects().length));
}

function dpActiveDialog() {
  const overlays = Array.from(
    document.querySelectorAll('.modal-overlay.show, .modal-overlay[aria-hidden="false"]')
  ).filter(dpVisibleFocusable);
  const overlay = overlays.at(-1);
  return overlay?.querySelector('[role="dialog"]') || null;
}

function dpTrapDialogFocus(event) {
  if (event.key !== 'Tab') return;
  const dialog = dpActiveDialog();
  if (!dialog) return;
  const focusables = Array.from(dialog.querySelectorAll(DP_FOCUSABLE)).filter(dpVisibleFocusable);
  if (!focusables.length) {
    event.preventDefault();
    dialog.setAttribute('tabindex', '-1');
    dialog.focus();
    return;
  }
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (!dialog.contains(document.activeElement)) {
    event.preventDefault();
    first.focus();
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function dpEnhanceDialogs(root) {
  const scope = root || document;
  const dialogs = scope.querySelectorAll ? Array.from(scope.querySelectorAll('.modal-card')) : [];
  if (scope.matches?.('.modal-card')) dialogs.push(scope);
  dialogs.forEach(card => {
    if (!card.getAttribute('role')) card.setAttribute('role', 'dialog');
    if (!card.getAttribute('aria-modal')) card.setAttribute('aria-modal', 'true');
  });
}

function dpInstallAccessibilityEnhancements() {
  dpEnsureControlNames(document);
  dpEnhanceDialogs(document);
  if (window._dpAccessibilityObserver) return;
  document.addEventListener('keydown', dpTrapDialogFocus);
  window._dpAccessibilityObserver = new MutationObserver(records => {
    records.forEach(record => {
      record.addedNodes.forEach(node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        dpEnsureControlNames(node);
        dpEnhanceDialogs(node);
      });
    });
  });
  window._dpAccessibilityObserver.observe(document.body, {childList: true, subtree: true});
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
            <div class="modal-actions">
              <button class="btn-tertiary" type="button" data-confirm="cancel">Cancel</button>
              <button class="btn-primary" type="button" data-confirm="ok">Continue</button>
            </div>
          </div>
        </div>
      `;
      document.body.appendChild(modal);
    }
    modal.querySelector('#dpConfirmTitle').textContent = opts.title || 'Confirm action';
    modal.querySelector('#dpConfirmSub').textContent = opts.subtitle || '';
    modal.querySelector('#dpConfirmDetail').innerHTML = opts.html || dpEscapeHtml(opts.message || '');
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

function dpPromptAction(options) {
  const opts = options || {};
  return new Promise(resolve => {
    let modal = document.getElementById('dpPromptModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'dpPromptModal';
      modal.className = 'modal-overlay';
      modal.innerHTML = `
        <div class="modal-card prompt-card" role="dialog" aria-modal="true" aria-labelledby="dpPromptTitle">
          <div class="modal-header">
            <div>
              <div class="modal-title" id="dpPromptTitle"></div>
              <div class="prefs-sub" id="dpPromptSub"></div>
            </div>
          </div>
          <div class="modal-body">
            <label class="form-label" id="dpPromptLabel" for="dpPromptInput"></label>
            <input id="dpPromptInput" type="text" autocomplete="off">
            <div class="modal-actions">
              <button class="btn-tertiary" type="button" data-prompt="cancel">Cancel</button>
              <button class="btn-primary" type="button" data-prompt="ok">Save</button>
            </div>
          </div>
        </div>
      `;
      document.body.appendChild(modal);
    }
    const title = modal.querySelector('#dpPromptTitle');
    const subtitle = modal.querySelector('#dpPromptSub');
    const label = modal.querySelector('#dpPromptLabel');
    const input = modal.querySelector('#dpPromptInput');
    const okBtn = modal.querySelector('[data-prompt="ok"]');
    const cancelBtn = modal.querySelector('[data-prompt="cancel"]');
    title.textContent = opts.title || 'Enter value';
    subtitle.textContent = opts.subtitle || '';
    label.textContent = opts.label || opts.title || 'Value';
    input.value = opts.defaultValue || '';
    input.placeholder = opts.placeholder || '';
    okBtn.textContent = opts.confirmText || 'Save';
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');
    const finish = value => {
      modal.classList.remove('show');
      modal.setAttribute('aria-hidden', 'true');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      input.removeEventListener('keydown', onKey);
      resolve(value);
    };
    const onOk = () => finish(input.value.trim());
    const onCancel = () => finish('');
    const onKey = event => {
      if (event.key === 'Enter') onOk();
      if (event.key === 'Escape') onCancel();
    };
    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    input.addEventListener('keydown', onKey);
    setTimeout(() => {
      input.focus();
      input.select();
    }, 0);
  });
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

function dpDataUrlFromB64(b64, fmt) {
  return `data:image/${fmt || 'png'};base64,${b64}`;
}

function dpOpenDataUrl(dataUrl) {
  if (!dataUrl) return;
  const win = window.open('', '_blank', 'noopener,noreferrer');
  if (!win) {
    window.open(dataUrl, '_blank', 'noopener,noreferrer');
    return;
  }
  win.document.write(`<!doctype html><title>Plot preview</title><img alt="plot preview" src="${dataUrl}" style="max-width:100%;height:auto;display:block;margin:auto">`);
  win.document.close();
}

function dpDownloadDataUrl(dataUrl, filename) {
  if (!dataUrl) return;
  const a = document.createElement('a');
  a.href = dataUrl;
  a.download = filename || 'plot.png';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function dpPlotActionsHtml(src, fmt, label) {
  return `
    <div class="plot-image-actions">
      <button class="btn-secondary" type="button" data-plot-action="open">Open</button>
      <button class="btn-secondary" type="button" data-plot-action="download">Download</button>
      <span class="sr-only">${dpEscapeHtml(label || 'Static plot preview')}</span>
    </div>
  `;
}

function dpBindPlotActions(container, img, fmt, label) {
  if (!container || !img || container.dataset.plotActionsReady === '1') return;
  container.dataset.plotActionsReady = '1';
  const openBtn = container.querySelector('[data-plot-action="open"]');
  const downloadBtn = container.querySelector('[data-plot-action="download"]');
  if (openBtn) openBtn.addEventListener('click', () => dpOpenDataUrl(img.currentSrc || img.src));
  if (downloadBtn) {
    downloadBtn.addEventListener('click', () => {
      const ext = String(fmt || 'png').replace(/^.*\//, '') || 'png';
      dpDownloadDataUrl(img.currentSrc || img.src, `${(label || 'plot').replace(/[^a-z0-9_-]+/gi, '_')}.${ext}`);
    });
  }
}

function setPlot(containerId, b64, fmt, altText) {
  const c = document.getElementById(containerId);
  if (!c) return;
  c.classList.remove('is-uplot');
  if (!b64) {
    c.innerHTML = '<div class="plot-placeholder">No output</div>';
    return;
  }
  const src = dpDataUrlFromB64(b64, fmt);
  const alt = altText || c.getAttribute('aria-label') || 'Static plot preview';
  c.innerHTML = `
    <div class="plot-image-frame">
      <img src="${src}" alt="${dpEscapeHtml(alt)}"/>
      ${dpPlotActionsHtml(src, fmt, alt)}
    </div>
  `;
  dpBindPlotActions(c.querySelector('.plot-image-frame'), c.querySelector('img'), fmt, alt);
}

function buildFileList(containerId, files, onSelect) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!files || !files.length) {
    delete _dpVirtualLists[containerId];
    el.innerHTML = '<div class="file-list-empty">No files found</div>';
    dpApplyFileListFilter(containerId);
    return;
  }
  const normalized = files.map((f, i) => ({
    name: f?.name || String(f?.path || f || ''),
    path: f?.path || String(f || ''),
    originalIndex: i,
    record: f,
  }));
  if (normalized.length > DP_FILE_LIST_VIRTUAL_THRESHOLD) {
    _dpVirtualLists[containerId] = {
      files: normalized,
      onSelect,
      page: 0,
      pageSize: DP_FILE_LIST_PAGE_SIZE,
      query: '',
    };
    dpRenderVirtualFileList(containerId);
    return;
  }
  delete _dpVirtualLists[containerId];
  el.innerHTML = normalized.map(f =>
    `<div class="file-item" data-idx="${f.originalIndex}" data-path="${dpEscapeHtml(f.path)}" title="${dpEscapeHtml(f.path)}">${dpEscapeHtml(f.name)}</div>`
  ).join('');
  el.querySelectorAll('.file-item').forEach(item => {
    item.addEventListener('click', () => onSelect(item));
  });
  dpApplyFileListFilter(containerId);
}

function dpRenderVirtualFileList(containerId) {
  const state = _dpVirtualLists[containerId];
  const el = document.getElementById(containerId);
  if (!state || !el) return;
  const filter = document.querySelector(`.file-filter input[data-target="${containerId}"]`);
  const query = String(state.query || filter?.value || '').trim().toLowerCase();
  const filtered = state.files.filter(item => {
    if (!query) return true;
    return `${item.name} ${item.path}`.toLowerCase().includes(query);
  });
  const totalPages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
  state.page = Math.max(0, Math.min(state.page, totalPages - 1));
  const start = state.page * state.pageSize;
  const pageItems = filtered.slice(start, start + state.pageSize);
  const controls = `
    <div class="file-list-meta" role="status">
      <span>${filtered.length}/${state.files.length} file(s)</span>
      <span>Page ${state.page + 1}/${totalPages}</span>
      <button class="btn-tertiary" type="button" data-file-page="prev"${state.page === 0 ? ' disabled' : ''}>Prev</button>
      <button class="btn-tertiary" type="button" data-file-page="next"${state.page >= totalPages - 1 ? ' disabled' : ''}>Next</button>
    </div>
  `;
  el.innerHTML = controls + pageItems.map(item =>
    `<div class="file-item" data-idx="${item.originalIndex}" data-path="${dpEscapeHtml(item.path)}" title="${dpEscapeHtml(item.path)}">${dpEscapeHtml(item.name)}</div>`
  ).join('');
  el.querySelector('[data-file-page="prev"]')?.addEventListener('click', () => {
    state.page -= 1;
    dpRenderVirtualFileList(containerId);
  });
  el.querySelector('[data-file-page="next"]')?.addEventListener('click', () => {
    state.page += 1;
    dpRenderVirtualFileList(containerId);
  });
  el.querySelectorAll('.file-item').forEach(item => {
    item.addEventListener('click', () => state.onSelect(item));
  });
  const count = document.querySelector(`.file-filter-count[data-target="${containerId}"]`);
  if (count) count.textContent = `${filtered.length}/${state.files.length}`;
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
  if (_dpVirtualLists[listId]) {
    _dpVirtualLists[listId].query = filter.value;
    _dpVirtualLists[listId].page = 0;
    dpRenderVirtualFileList(listId);
    return;
  }
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
      <input type="search" data-target="${dpEscapeHtml(listId)}" placeholder="Filter list" aria-label="Filter ${dpEscapeHtml(listId)}">
      <span class="file-filter-count" data-target="${dpEscapeHtml(listId)}"></span>
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

function dpEnhanceEmptyStates() {
  document.querySelectorAll('.plot-placeholder, .file-list-empty, .artifact-empty').forEach(el => {
    if (el.dataset.emptyEnhanced === '1') return;
    const text = (el.textContent || '').trim();
    if (!text || /examples\//i.test(text)) return;
    const hint = document.createElement('div');
    hint.className = 'empty-state-hint';
    hint.innerHTML = 'Example data: <code>examples/</code>';
    el.appendChild(hint);
    el.dataset.emptyEnhanced = '1';
  });
  dpEnhanceExampleEntrypoints();
}

function dpUseExamplesDir() {
  const inputs = [
    'folderPath',
    'inputFolder',
    'baseDir',
    'dataDir',
    'sourceFolder',
  ].map(id => document.getElementById(id)).filter(Boolean);
  if (!inputs.length || !DEFAULT_EXAMPLES_DIR) return false;
  inputs.forEach(input => {
    if (!input.value) input.value = DEFAULT_EXAMPLES_DIR;
  });
  toast('Example folder filled in');
  return true;
}

function dpEnhanceExampleEntrypoints() {
  document.querySelectorAll('.empty-state-hint').forEach(hint => {
    if (hint.dataset.examplesReady === '1') return;
    const btn = document.createElement('button');
    btn.className = 'btn-secondary empty-state-action';
    btn.type = 'button';
    btn.textContent = 'Use examples';
    btn.addEventListener('click', dpUseExamplesDir);
    hint.appendChild(btn);
    hint.dataset.examplesReady = '1';
  });
}

function dpEnhancePlotImages(root) {
  const scope = root || document;
  const images = scope.matches?.('img[src^="data:image/"]')
    ? [scope]
    : Array.from(scope.querySelectorAll('img[src^="data:image/"]'));
  images.forEach(img => {
    if (img.closest('.plot-image-frame') || img.dataset.plotEnhanced === '1') return;
    const container = img.closest('.plot-area, .result-card-body, .figure-grid, .artifact-preview, .stack-distribution-plot');
    if (!container) return;
    img.dataset.plotEnhanced = '1';
    if (!img.alt || img.alt === 'plot') img.alt = 'Static plot preview';
    const frame = document.createElement('div');
    frame.className = 'plot-image-frame';
    img.parentNode.insertBefore(frame, img);
    frame.appendChild(img);
    frame.insertAdjacentHTML('beforeend', dpPlotActionsHtml(img.src, img.src.includes('gif') ? 'gif' : 'png', img.alt));
    dpBindPlotActions(frame, img, img.src.includes('gif') ? 'gif' : 'png', img.alt);
  });
}

function dpInstallPlotImageObserver() {
  dpEnhancePlotImages(document);
  const observer = new MutationObserver(mutations => {
    for (const mutation of mutations) {
      mutation.addedNodes.forEach(node => {
        if (node.nodeType === 1) dpEnhancePlotImages(node);
      });
    }
  });
  observer.observe(document.body, {childList: true, subtree: true});
}
