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
