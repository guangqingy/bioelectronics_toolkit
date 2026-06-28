/* ──────────────────────────────────────────────
   fluorescence_gif.html — TIFF-to-GIF merge
────────────────────────────────────────────── */

const GIF_ROI_COLORS = ['#ffd166', '#06d6a0', '#ef476f', '#118ab2', '#9b5de5', '#f15bb5'];
const GIF_CROP_COLORS = ['#38bdf8', '#fb923c', '#a78bfa', '#22c55e'];

let _availableTiffs = [];   // {name, path, info}
let _checkedTiffIndices = new Set();
let _availableActiveIndex = -1;
let _tiffEntries = [];      // {id, path, frames, slices, scale}
let _entryCounter = 0;
let _gifPreviewTimer = null;
let _imgW = 0;
let _imgH = 0;
let _roiPolygons = [];
let _draftPolygon = null;
let _nextPolygonIdx = 1;
let _cropRects = [];
let _draftCropRect = null;
let _cropRectDragStart = null;
let _nextCropRectIdx = 1;
let _gifRoiCsvContent = null;
let _gifRoiPlotB64 = null;
let _gifRoiDefaultOutputDir = '';
let _gifKymoHeatmapCsv = null;
let _gifKymoSummaryCsv = null;
let _gifKymoPlotB64 = null;
let _gifKymoDefaultOutputDir = '';
let _gifPrefs = {};
let _gifFileProfileState = null;

function gifElement(id) {
  return document.getElementById(id);
}

function gifValue(id, fallback = '') {
  const el = gifElement(id);
  return el && el.value !== undefined ? el.value : fallback;
}

function gifTrimmedValue(id, fallback = '') {
  return String(gifValue(id, fallback)).trim();
}

function gifNumber(id, fallback = 0) {
  const n = parseFloat(gifValue(id, ''));
  return Number.isFinite(n) ? n : fallback;
}

function gifInteger(id, fallback = 0) {
  const n = parseInt(gifValue(id, ''), 10);
  return Number.isFinite(n) ? n : fallback;
}

function gifChecked(id, fallback = false) {
  const el = gifElement(id);
  return el ? !!el.checked : !!fallback;
}

function gifSetText(id, text) {
  const el = gifElement(id);
  if (el) el.textContent = text;
}

function gifSetDisplay(id, display) {
  const el = gifElement(id);
  if (el) el.style.display = display;
}

/* ---------- Folder browse / available TIFFs ---------- */

function renderAvailableTiffList() {
  const el = document.getElementById('availableTiffList');
  const count = document.getElementById('availableCount');
  if (count) count.textContent = `${_checkedTiffIndices.size}/${_availableTiffs.length} checked`;
  if (!el) return;
  if (!_availableTiffs.length) {
    el.innerHTML = '<div class="file-list-empty">No TIFF stacks scanned</div>';
    return;
  }

  el.innerHTML = _availableTiffs.map((f, i) => {
    const checked = _checkedTiffIndices.has(i) ? 'checked' : '';
    const active = i === _availableActiveIndex ? 'active' : '';
    const info = f.info || {};
    const meta = info.n_frames ? `${info.n_frames}f · ${shortScaleInfo(info) || 'scale pending'}` : 'not scanned';
    return `
      <div class="file-item gif-available-item ${active}" data-dp-click="selectAvailableTiff(${i})">
        <input class="dp-check" type="checkbox" ${checked} data-dp-click="event.stopPropagation()" data-dp-change="toggleTiffCheck(${i}, this.checked)">
        <span class="gif-available-name">${escHtml(f.name || fileBasename(f.path))}</span>
        <span class="gif-available-meta">${escHtml(meta)}</span>
        <button class="btn-secondary" style="font-size:11px;padding:1px 7px;min-height:22px" data-dp-click="event.stopPropagation();addAvailableByIndex(${i})">Add</button>
      </div>`;
  }).join('');
}

function toggleTiffCheck(i, checked) {
  if (checked) _checkedTiffIndices.add(i);
  else _checkedTiffIndices.delete(i);
  renderAvailableTiffList();
}

function clearTiffChecks() {
  _checkedTiffIndices.clear();
  renderAvailableTiffList();
}

function selectAvailableTiff(i) {
  if (i < 0 || i >= _availableTiffs.length) return;
  _availableActiveIndex = i;
  renderAvailableTiffList();
  scheduleGifPreview();
  loadGifProfileForCurrent(true, false);
}

function entryKey(path) {
  return String(path || '').trim();
}

function collectGifPrefs() {
  return {
    folderPath: gifValue('folderPath'),
    gifFps: gifValue('gifFps'),
    gifLut: gifValue('gifLut'),
    gifBarUm: gifValue('gifBarUm'),
    gifAutoScale: gifChecked('gifAutoScale'),
    gifPxPerUm: gifValue('gifPxPerUm'),
    gifLabelMode: gifValue('gifLabelMode'),
    gifCropMode: gifValue('gifCropMode'),
    gifCropPadding: gifValue('gifCropPadding'),
    gifShowRoiOverlay: gifChecked('gifShowRoiOverlay'),
    gifOutput: gifValue('gifOutput'),
    gifBgMode: gifValue('gifBgMode'),
    gifRoiMetric: gifValue('gifRoiMetric'),
    gifRoiPlotMetric: gifValue('gifRoiPlotMetric'),
    gifRoiRefFrame: gifValue('gifRoiRefFrame'),
    gifRoiPrefix: gifValue('gifRoiPrefix'),
    gifKymoValueMode: gifValue('gifKymoValueMode'),
    gifKymoBins: gifValue('gifKymoBins'),
    gifKymoLowPct: gifValue('gifKymoLowPct'),
    gifKymoHighPct: gifValue('gifKymoHighPct'),
    gifKymoThresholds: gifValue('gifKymoThresholds'),
    gifKymoPercentiles: gifValue('gifKymoPercentiles'),
    gifKymoTopMeans: gifValue('gifKymoTopMeans'),
    gifKymoPeakLine: gifChecked('gifKymoPeakLine'),
    gifKymoMeanLine: gifChecked('gifKymoMeanLine'),
    gifKymoSmoothIntensity: gifValue('gifKymoSmoothIntensity'),
    gifKymoSmoothTime: gifValue('gifKymoSmoothTime'),
    gifKymoSmoothLines: gifChecked('gifKymoSmoothLines'),
    gifKymoRefFrame: gifValue('gifKymoRefFrame'),
    gifKymoRefStat: gifValue('gifKymoRefStat'),
    gifKymoPrefix: gifValue('gifKymoPrefix'),
  };
}

function applyGifPrefs(prefs) {
  if (!prefs || typeof prefs !== 'object') return;
  const setVal = (id, value) => {
    const el = document.getElementById(id);
    if (el && value !== undefined && value !== null) el.value = value;
  };
  const setChecked = (id, value) => {
    const el = document.getElementById(id);
    if (el && value !== undefined) el.checked = !!value;
  };
  Object.entries(prefs).forEach(([key, value]) => {
    if (typeof value === 'boolean') setChecked(key, value);
    else setVal(key, value);
  });
  toggleScaleMode();
  updateGifCropControls();
  updateGifBgControls();
  updateGifKymoControls();
}

async function saveGifDefaults() {
  _gifPrefs.defaults = collectGifPrefs();
  _gifPrefs.updated_at = new Date().toISOString();
  await saveViewPreferences('fluorescence_gif', _gifPrefs);
  setStatus('gifPrefsStatus', 'Defaults saved.', 'ok');
  toast('GIF defaults saved');
}

async function resetGifDefaults() {
  _gifPrefs = {};
  await saveViewPreferences('fluorescence_gif', _gifPrefs);
  setStatus('gifPrefsStatus', 'Defaults reset.', 'ok');
  toast('GIF defaults reset');
}

function addAvailableByIndex(i) {
  if (i < 0 || i >= _availableTiffs.length) return false;
  const f = _availableTiffs[i];
  const path = entryKey(f.path);
  if (!path) return false;
  if (_tiffEntries.some(e => entryKey(e.path) === path)) return false;
  const id = 'te_' + (_entryCounter++);
  const info = f.info || null;
  _tiffEntries.push({
    id,
    path,
    frames: info && info.n_frames ? info.n_frames : null,
    slices: '',
    scale: info,
  });
  renderTiffList();
  scheduleGifPreview();
  return true;
}

function addCheckedTiffs() {
  if (!_checkedTiffIndices.size) {
    setStatus('status', 'No checked TIFFs to add', 'error');
    return;
  }
  let added = 0;
  Array.from(_checkedTiffIndices).sort((a, b) => a - b).forEach(i => {
    if (addAvailableByIndex(i)) added += 1;
  });
  setStatus('status', `Added ${added} TIFF(s) to GIF queue`, added ? 'ok' : 'error');
}

async function scanGifFolder() {
  const folder = gifTrimmedValue('folderPath');
  if (!folder) {
    setStatus('status', 'Please choose a TIFF folder', 'error');
    return;
  }
  setStatus('status', 'Scanning TIFF folder...', 'loading');
  try {
    const d = await api('/api/fluorescence/browse', {folder});
    if (d.error) throw new Error(d.error);
    _availableTiffs = (d.files || []).map(f => ({name: f.name || fileBasename(f.path), path: f.path || '', info: null}));
    _checkedTiffIndices.clear();
    _availableActiveIndex = _availableTiffs.length ? 0 : -1;
    renderAvailableTiffList();
    if (_availableTiffs.length) await scanAvailableInfo(false);
    if (_availableTiffs.length) await loadGifProfileForCurrent(true, false);
    setStatus('status', `${_availableTiffs.length} TIFF stack(s) found`, _availableTiffs.length ? 'ok' : 'error');
    scheduleGifPreview();
  } catch(ex) {
    setStatus('status', 'Folder scan failed: ' + ex.message, 'error');
  }
}

async function scanAvailableInfo(showStatus = true) {
  const paths = _availableTiffs.map(f => f.path).filter(Boolean);
  if (!paths.length) return;
  if (showStatus) setStatus('status', 'Scanning available TIFF metadata...', 'loading');
  try {
    const d = await api('/api/fluorescence/tiff_info_batch', {paths});
    if (d.error) throw new Error(d.error);
    const infoMap = d.info || {};
    _availableTiffs.forEach(f => { f.info = infoMap[f.path] || null; });
    renderAvailableTiffList();
    if (showStatus) setStatus('status', 'Available TIFF metadata scanned', 'ok');
  } catch(ex) {
    if (showStatus) setStatus('status', 'Metadata scan failed: ' + ex.message, 'error');
  }
}

/* ---------- GIF queue management ---------- */

function addTiffEntry(path) {
  const id = 'te_' + (_entryCounter++);
  _tiffEntries.push({ id, path: path || '', frames: null, slices: '', scale: null });
  renderTiffList();
  scheduleGifPreview();
}

function removeTiffEntry(id) {
  _tiffEntries = _tiffEntries.filter(e => e.id !== id);
  renderTiffList();
  scheduleGifPreview();
}

function moveTiffEntry(id, dir) {
  const idx = _tiffEntries.findIndex(e => e.id === id);
  if (idx < 0) return;
  const swap = idx + dir;
  if (swap < 0 || swap >= _tiffEntries.length) return;
  [_tiffEntries[idx], _tiffEntries[swap]] = [_tiffEntries[swap], _tiffEntries[idx]];
  renderTiffList();
  scheduleGifPreview();
}

function updateTiffPath(id, value) {
  const e = _tiffEntries.find(e => e.id === id);
  if (e) { e.path = value; e.frames = null; e.scale = null; }
  scheduleGifPreview();
}

function updateSliceSpec(id, value) {
  const e = _tiffEntries.find(e => e.id === id);
  if (e) e.slices = value;
  scheduleGifPreview();
}

function renderTiffList() {
  const wrap = document.getElementById('tiffList');
  const count = document.getElementById('tiffCount');
  if (count) {
    const filled = _tiffEntries.filter(e => e.path && e.path.trim()).length;
    count.textContent = `${filled}/${_tiffEntries.length} files`;
  }
  if (!wrap) return;
  if (!_tiffEntries.length) {
    wrap.innerHTML = '<div class="gif-empty-state">No TIFF stacks added yet</div>';
    return;
  }
  wrap.innerHTML = _tiffEntries.map((e, i) => {
    const frameTag = e.frames !== null
      ? `<span class="gif-pill">${e.frames} frames</span>`
      : '<span class="gif-pill gif-pill-muted">not scanned</span>';
    const scaleTag = e.scale && e.scale.scale_source
      ? `<span class="gif-pill gif-pill-muted">${escHtml(shortScaleInfo(e.scale))}</span>`
      : '';
    const fileName = fileBasename(e.path) || 'Choose a TIFF stack';
    const fileSub = e.path ? compactPath(e.path) : 'Use the file button to pick a .tif or .tiff stack';
    return `
    <div class="gif-file-row" id="${e.id}">
      <div class="gif-file-top">
        <div class="gif-file-index">${String(i + 1).padStart(2, '0')}</div>
        <div class="gif-file-summary">
          <div class="gif-file-name">${escHtml(fileName)}</div>
          <div class="gif-file-sub">${escHtml(fileSub)}</div>
        </div>
        <div class="gif-pill-row">${frameTag}${scaleTag}</div>
      </div>
      <div class="gif-file-path-row">
        <span class="gif-mini-label">File</span>
        <input type="text" value="${escHtml(e.path)}"
               placeholder="/path/to/file.tif"
 data-dp-input="updateTiffPath('${e.id}', this.value)">
        <button class="btn-icon" title="Choose TIFF" data-dp-click="pickTiffFile('${e.id}')">⌕</button>
      </div>
      <div class="gif-slice-row">
        <span class="gif-mini-label">Slices</span>
        <input type="text" value="${escHtml(e.slices || '')}"
               placeholder="all or 1-20,25,30-40:2"
 data-dp-input="updateSliceSpec('${e.id}', this.value)">
        <div class="gif-row-actions">
          <button class="btn-icon" title="Move up" data-dp-click="moveTiffEntry('${e.id}', -1)" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button class="btn-icon" title="Move down" data-dp-click="moveTiffEntry('${e.id}', +1)" ${i === _tiffEntries.length-1 ? 'disabled' : ''}>↓</button>
          <button class="btn-icon btn-danger" title="Remove" data-dp-click="removeTiffEntry('${e.id}')">×</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

function escHtml(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'addAvailableByIndex',
  'addCheckedTiffs',
  'addTiffEntry',
  'applyGifPrefs',
  'clearTiffChecks',
  'collectGifPrefs',
  'entryKey',
  'escHtml',
  'gifChecked',
  'gifElement',
  'gifInteger',
  'gifNumber',
  'gifSetDisplay',
  'gifSetText',
  'gifTrimmedValue',
  'gifValue',
  'moveTiffEntry',
  'removeTiffEntry',
  'renderAvailableTiffList',
  'renderTiffList',
  'resetGifDefaults',
  'saveGifDefaults',
  'scanAvailableInfo',
  'scanGifFolder',
  'selectAvailableTiff',
  'toggleTiffCheck',
  'updateSliceSpec',
  'updateTiffPath',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
