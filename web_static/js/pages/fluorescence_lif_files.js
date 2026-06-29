function scanLifFolder() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) { setStatus('status', 'Enter a folder path', 'error'); return; }
  setStatus('status', 'Scanning .lif files...', 'loading');

  api('/api/fluorescence/lif/browse', { folder }).then(d => {
    if (d.error) throw new Error(d.error);
    _lifFiles = d.files || [];
    renderLifFileList();
    setStatus('status', `${_lifFiles.length} LIF project(s) found`, 'ok');
  }).catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function renderLifFileList() {
  const el = document.getElementById('lifFileList');
  if (!_lifFiles.length) {
    el.innerHTML = '<div class="file-list-empty">No .lif files found</div>';
    return;
  }
  el.innerHTML = _lifFiles.map((f, i) => `
    <div class="file-item" data-idx="${i}" data-dp-click="selectLifFile(${i})">${dpEscapeHtml(f.name)}</div>
  `).join('');
}

function selectLifFile(i) {
  const rec = _lifFiles[i];
  if (!rec) return;
  document.querySelectorAll('#lifFileList .file-item').forEach(e => e.classList.remove('active'));
  document.querySelector(`#lifFileList .file-item[data-idx="${i}"]`)?.classList.add('active');
  document.getElementById('lifPath').value = rec.path;
  loadLifProject();
}

function loadLifProject() {
  const path = document.getElementById('lifPath').value.trim();
  if (!path) { setStatus('status', 'Choose a .lif file', 'error'); return; }
  _lifPath = path;
  _lifActiveIndex = null;
  loadRenameMap(path);
  btnBusy('btnLoadLif', true, 'Loading...');
  setStatus('status', 'Reading Leica project metadata...', 'loading');
  document.getElementById('lifSubfileList').innerHTML = '<div class="file-list-empty">Loading LIF project...</div>';
  document.getElementById('subfileSection').style.display = '';

  api('/api/fluorescence/lif/info', {
    path,
    sort: document.getElementById('sortMode').value,
  }).then(d => {
    btnBusy('btnLoadLif', false, 'Load Project');
    if (d.error) throw new Error(d.error);
    _lifRecords = d.records || [];
    sortLifRecords();
    renderLifBrowser();
    document.getElementById('btnManifest').disabled = !_lifRecords.length;
    document.getElementById('btnExportAllTiff').disabled = !_lifRecords.length;
    const timed = d.timestamp_count || 0;
    document.getElementById('lifMeta').textContent =
      `${d.name || 'LIF project'} · ${d.n_images || 0} subfiles · ${timed} with timestamp metadata`;
    setStatus('status', 'Ready', 'ok');
    if (_lifDisplayRecords.length) selectLifRecord(_lifDisplayRecords[0].index);
  }).catch(e => {
    btnBusy('btnLoadLif', false, 'Load Project');
    setStatus('status', 'Error: ' + e.message, 'error');
    showLog('LIF Load Error', e.message || String(e));
  });
}

function onSortChange() {
  if (!_lifRecords.length) return;
  sortLifRecords();
  renderLifBrowser();
}

function sortLifRecords() {
  const mode = document.getElementById('sortMode').value;
  _lifDisplayRecords = [..._lifRecords].sort((a, b) => {
    if (mode === 'original') return (a.original_order || 0) - (b.original_order || 0);
    if (mode === 'name') {
      const an = String(displayName(a) || a.full_name || '').toLowerCase();
      const bn = String(displayName(b) || b.full_name || '').toLowerCase();
      return an.localeCompare(bn) || ((a.original_order || 0) - (b.original_order || 0));
    }
    const at = Number.isFinite(Number(a.sort_value));
    const bt = Number.isFinite(Number(b.sort_value));
    if (at && bt) return Number(a.sort_value) - Number(b.sort_value) || ((a.original_order || 0) - (b.original_order || 0));
    if (at) return -1;
    if (bt) return 1;
    return (a.original_order || 0) - (b.original_order || 0);
  });
}

function dimText(rec) {
  const d = rec.dimensions || {};
  const parts = [`${d.x || 0}x${d.y || 0}`];
  if ((d.z || 1) > 1) parts.push(`Z${d.z}`);
  if ((d.t || 1) > 1) parts.push(`T${d.t}`);
  if ((d.m || 1) > 1) parts.push(`M${d.m}`);
  (rec.extra_dimensions || []).forEach(dim => {
    if (Number(dim.count || 1) > 1) parts.push(`${dim.label || ('D' + dim.id)}${dim.count}`);
  });
  parts.push(`C${rec.channels || 1}`);
  return parts.join(' · ');
}

function fmtMetaNumber(v, suffix) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return 'not found';
  const text = n >= 100 ? n.toFixed(2) : n >= 10 ? n.toFixed(3) : n.toFixed(4);
  return text.replace(/0+$/, '').replace(/\.$/, '') + (suffix || '');
}

function pixelSizeText(rec) {
  const cal = rec.calibration || {};
  const x = fmtMetaNumber(cal.pixel_width_um, ' um/px');
  const y = fmtMetaNumber(cal.pixel_height_um, ' um/px');
  if (x === 'not found' && y === 'not found') return 'not found';
  if (x === y) return x;
  return `${x} x ${y}`;
}

function renderLifBrowser() {
  renderLifSubfileList();
  renderSelectedDetail();
  document.getElementById('lifBrowserCount').textContent = _lifDisplayRecords.length ? `${_lifDisplayRecords.length}` : '';
}

function renderLifSubfileList() {
  const el = document.getElementById('lifSubfileList');
  if (!_lifDisplayRecords.length) {
    el.innerHTML = '<div class="file-list-empty">No subfiles found</div>';
    return;
  }
  el.innerHTML = _lifDisplayRecords.map((rec, i) => {
    const active = Number(rec.index) === Number(_lifActiveIndex) ? 'active' : '';
    const customLine = hasCustomName(rec) ? `<span class="lif-subfile-original">Original: ${dpEscapeHtml(rec.name || '')}</span>` : '';
    return `
      <div class="file-item lif-subfile-item ${active}" data-dp-click="selectLifRecord(${rec.index})">
        <span class="lif-order">${i + 1}</span>
        <span class="lif-subfile-main">${dpEscapeHtml(displayName(rec))}</span>
        <span class="lif-subfile-time">${dpEscapeHtml(rec.acquired_at || 'Leica order')} · LIF ${rec.original_order || ''} · ${dpEscapeHtml(dimText(rec))}</span>
        ${customLine}
      </div>`;
  }).join('');
}

function findRecord(index) {
  return _lifRecords.find(r => Number(r.index) === Number(index));
}

function selectLifRecord(index) {
  const rec = findRecord(index);
  if (!rec) return;
  _lifActiveIndex = Number(index);
  renderLifBrowser();
  document.getElementById('previewSection').style.display = '';
  document.getElementById('lifPreviewName').textContent = displayName(rec);
  updatePreviewControls(rec);
  refreshLifPreview();
}

function renderSelectedDetail() {
  const body = document.getElementById('lifDetailBody');
  const rec = findRecord(_lifActiveIndex);
  if (!rec) {
    body.innerHTML = '<div class="file-list-empty">No subfile selected</div>';
    return;
  }
  const d = rec.dimensions || {};
  const planes = planeCount(rec);
  const stackLine = planes > 1 ? `${planes} planes will export as one multi-page TIFF stack.` : 'Single plane TIFF export.';
  body.innerHTML = `
    <div class="lif-detail-title">${dpEscapeHtml(displayName(rec))}</div>
    ${hasCustomName(rec) ? `<div class="lif-detail-muted">Original: ${dpEscapeHtml(rec.name || '')}</div>` : ''}
    <div class="lif-detail-grid">
      <span>Project order</span><strong>${rec.original_order || ''}</strong>
      <span>Time</span><strong>${dpEscapeHtml(rec.acquired_at || 'No timestamp')}</strong>
      <span>Folder</span><strong>${dpEscapeHtml(rec.folder || 'Project')}</strong>
      <span>Dimensions</span><strong>${dpEscapeHtml(dimText(rec))}</strong>
      <span>Planes</span><strong>${planes}</strong>
      <span>Bit depth</span><strong>${dpEscapeHtml((rec.bit_depth || []).join('/') || '')}</strong>
      <span>Pixel size</span><strong>${dpEscapeHtml(pixelSizeText(rec))}</strong>
      <span>Z spacing</span><strong>${dpEscapeHtml(fmtMetaNumber((rec.calibration || {}).z_spacing_um, ' um'))}</strong>
      <span>T interval</span><strong>${dpEscapeHtml(fmtMetaNumber((rec.calibration || {}).frame_interval_s, ' s'))}</strong>
    </div>
    <div class="param-row" style="margin-top:12px">
      <div class="param-label">Export name</div>
      <input type="text" id="renameInput" value="${escapeAttr(displayName(rec))}" placeholder="output name" style="font-size:12px">
    </div>
    <div class="btn-row">
      <button class="btn-secondary" id="btnApplyRename" data-dp-click="applySelectedRename()">Rename</button>
      <button class="btn-tertiary" data-dp-click="resetSelectedRename()">Reset</button>
    </div>
    <div class="lif-export-note">${dpEscapeHtml(stackLine)}</div>
    <button class="btn-primary" id="btnExportTiff" data-label="Export Selected TIFF" data-dp-click="exportSelectedTiff()" style="margin-top:10px">Export Selected TIFF</button>
  `;
}

function syncRenameFromInput() {
  const rec = findRecord(_lifActiveIndex);
  if (!rec) return '';
  const input = document.getElementById('renameInput');
  const raw = input ? input.value.trim() : '';
  if (raw && raw !== rec.name) {
    _lifRenameMap[String(rec.index)] = raw;
  } else {
    delete _lifRenameMap[String(rec.index)];
  }
  saveRenameMap();
  return displayName(rec);
}

function applySelectedRename() {
  const rec = findRecord(_lifActiveIndex);
  if (!rec) return;
  syncRenameFromInput();
  if (document.getElementById('sortMode').value === 'name') sortLifRecords();
  renderLifBrowser();
  document.getElementById('lifPreviewName').textContent = displayName(rec);
  setStatus('status', 'Rename saved for export/list display', 'ok');
}

function resetSelectedRename() {
  const rec = findRecord(_lifActiveIndex);
  if (!rec) return;
  delete _lifRenameMap[String(rec.index)];
  saveRenameMap();
  if (document.getElementById('sortMode').value === 'name') sortLifRecords();
  renderLifBrowser();
  document.getElementById('lifPreviewName').textContent = displayName(rec);
  setStatus('status', 'Rename reset', 'ok');
}

function setSliderDim(id, labelId, count) {
  const slider = document.getElementById(id);
  const label = document.getElementById(labelId);
  if (!slider || !label) return;
  const n = Math.max(1, Number(count) || 1);
  slider.max = String(n - 1);
  if (Number(slider.value) >= n) slider.value = '0';
  slider.disabled = n <= 1;
  label.textContent = `${Number(slider.value) + 1} / ${n}`;
}

function extraPreviewDims(rec) {
  return (rec.extra_dimensions || []).filter(dim => Number(dim.count || 1) > 1);
}

function renderExtraDimControls(rec) {
  const el = document.getElementById('extraDimControls');
  if (!el) return;
  const dims = extraPreviewDims(rec);
  const currentValues = {};
  dims.forEach(dim => {
    const id = Number(dim.id);
    const slider = document.getElementById(`extraDimSlider_${id}`);
    if (slider) currentValues[id] = Number(slider.value || 0);
  });
  if (!dims.length) {
    el.innerHTML = '';
    return;
  }
  el.innerHTML = dims.map(dim => {
    const id = Number(dim.id);
    const count = Math.max(1, Number(dim.count || 1));
    const value = Math.max(0, Math.min(count - 1, Number(currentValues[id] || 0)));
    const label = dpEscapeHtml(dim.label || ('D' + id));
    return `
      <div class="lif-slider-row lif-extra-dim-row" data-dim-id="${id}">
        <span>${label}</span>
        <input type="range" id="extraDimSlider_${id}" min="0" max="${count - 1}" value="${value}" data-dp-input="onLifDimSlide()">
        <span id="extraDimLabel_${id}">1 / ${count}</span>
      </div>`;
  }).join('');
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'applySelectedRename',
  'dimText',
  'extraPreviewDims',
  'findRecord',
  'fmtMetaNumber',
  'loadLifProject',
  'onSortChange',
  'pixelSizeText',
  'renderExtraDimControls',
  'renderLifBrowser',
  'renderLifFileList',
  'renderLifSubfileList',
  'renderSelectedDetail',
  'resetSelectedRename',
  'scanLifFolder',
  'selectLifFile',
  'selectLifRecord',
  'setSliderDim',
  'sortLifRecords',
  'syncRenameFromInput',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
