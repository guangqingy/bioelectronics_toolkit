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
      <div class="file-item gif-available-item ${active}" onclick="selectAvailableTiff(${i})">
        <input type="checkbox" ${checked} onclick="event.stopPropagation()" onchange="toggleTiffCheck(${i}, this.checked)">
        <span class="gif-available-name">${escHtml(f.name || fileBasename(f.path))}</span>
        <span class="gif-available-meta">${escHtml(meta)}</span>
        <button class="btn-secondary" style="font-size:11px;padding:1px 7px;min-height:22px" onclick="event.stopPropagation();addAvailableByIndex(${i})">Add</button>
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
  const val = id => document.getElementById(id)?.value ?? '';
  const checked = id => !!document.getElementById(id)?.checked;
  return {
    folderPath: val('folderPath'),
    gifFps: val('gifFps'),
    gifLut: val('gifLut'),
    gifBarUm: val('gifBarUm'),
    gifAutoScale: checked('gifAutoScale'),
    gifPxPerUm: val('gifPxPerUm'),
    gifLabelMode: val('gifLabelMode'),
    gifCropMode: val('gifCropMode'),
    gifCropPadding: val('gifCropPadding'),
    gifShowRoiOverlay: checked('gifShowRoiOverlay'),
    gifOutput: val('gifOutput'),
    gifBgMode: val('gifBgMode'),
    gifRoiMetric: val('gifRoiMetric'),
    gifRoiPlotMetric: val('gifRoiPlotMetric'),
    gifRoiRefFrame: val('gifRoiRefFrame'),
    gifRoiPrefix: val('gifRoiPrefix'),
    gifKymoValueMode: val('gifKymoValueMode'),
    gifKymoBins: val('gifKymoBins'),
    gifKymoLowPct: val('gifKymoLowPct'),
    gifKymoHighPct: val('gifKymoHighPct'),
    gifKymoThresholds: val('gifKymoThresholds'),
    gifKymoPercentiles: val('gifKymoPercentiles'),
    gifKymoTopMeans: val('gifKymoTopMeans'),
    gifKymoPeakLine: checked('gifKymoPeakLine'),
    gifKymoMeanLine: checked('gifKymoMeanLine'),
    gifKymoSmoothIntensity: val('gifKymoSmoothIntensity'),
    gifKymoSmoothTime: val('gifKymoSmoothTime'),
    gifKymoSmoothLines: checked('gifKymoSmoothLines'),
    gifKymoRefFrame: val('gifKymoRefFrame'),
    gifKymoRefStat: val('gifKymoRefStat'),
    gifKymoPrefix: val('gifKymoPrefix'),
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
  const folder = document.getElementById('folderPath').value.trim();
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
               oninput="updateTiffPath('${e.id}', this.value)">
        <button class="btn-icon" title="Choose TIFF" onclick="pickTiffFile('${e.id}')">⌕</button>
      </div>
      <div class="gif-slice-row">
        <span class="gif-mini-label">Slices</span>
        <input type="text" value="${escHtml(e.slices || '')}"
               placeholder="all or 1-20,25,30-40:2"
               oninput="updateSliceSpec('${e.id}', this.value)">
        <div class="gif-row-actions">
          <button class="btn-icon" title="Move up"   onclick="moveTiffEntry('${e.id}', -1)" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button class="btn-icon" title="Move down" onclick="moveTiffEntry('${e.id}', +1)" ${i === _tiffEntries.length-1 ? 'disabled' : ''}>↓</button>
          <button class="btn-icon btn-danger" title="Remove" onclick="removeTiffEntry('${e.id}')">×</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function fileBasename(path) {
  const s = String(path || '').trim();
  if (!s) return '';
  return s.split(/[\\/]/).filter(Boolean).pop() || s;
}

function compactPath(path) {
  const s = String(path || '').trim();
  if (!s) return '';
  const parts = s.split(/[\\/]/).filter(Boolean);
  if (parts.length <= 3) return s;
  const prefix = s.startsWith('/') ? '/' : '';
  return `${prefix}${parts[0]}/.../${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
}

/* Use folder picker to get path then trim to file (user types or we use the
   folder approach — open folder then user picks manually). */
async function pickTiffFile(id) {
  try {
    const e = _tiffEntries.find(e => e.id === id);
    const d = await api('/api/system/select_file', {start: e ? e.path : ''});
    if (d && d.path && !d.cancelled) {
      if (e) { e.path = d.path; e.frames = null; e.scale = null; }
      renderTiffList();
      await scanFrameCounts(false);
      scheduleGifPreview();
    }
  } catch(ex) { toast('File picker error: ' + ex.message, true); }
}

function toggleScaleMode() {
  const auto = document.getElementById('gifAutoScale').checked;
  const row = document.getElementById('manualScaleRow');
  const input = document.getElementById('gifPxPerUm');
  if (row) row.style.opacity = auto ? '0.55' : '1';
  if (input) input.disabled = auto;
}

function shortScaleInfo(info) {
  if (!info) return '';
  const px = Number(info.pixel_size_um);
  if (Number.isFinite(px) && px > 0) return `${px.toFixed(4)} µm/px`;
  return info.scale_source || '';
}

function formatScaleInfo(d) {
  if (!d) return '';
  const px = Number(d.pixel_size_um);
  const ppum = Number(d.pixels_per_um);
  const source = d.scale_source || 'manual px/um';
  let bits = [];
  if (Number.isFinite(px) && px > 0) bits.push(`${px.toFixed(6)} µm/px`);
  if (Number.isFinite(ppum) && ppum > 0) bits.push(`${ppum.toFixed(4)} px/µm`);
  bits.push(source);
  return bits.join(' · ');
}

function scheduleGifPreview() {
  clearTimeout(_gifPreviewTimer);
  _gifPreviewTimer = setTimeout(refreshGifPreview, 250);
}

function getPreviewEntry() {
  if (_availableActiveIndex >= 0 && _availableActiveIndex < _availableTiffs.length) {
    const f = _availableTiffs[_availableActiveIndex];
    const queued = _tiffEntries.find(e => entryKey(e.path) === entryKey(f.path));
    return {
      path: f.path,
      name: f.name || fileBasename(f.path),
      slices: queued ? queued.slices : '',
    };
  }
  const first = _tiffEntries.find(e => e.path && e.path.trim());
  if (!first) return null;
  return {
    path: first.path,
    name: fileBasename(first.path),
    slices: first.slices || '',
  };
}

function gifProjectRoot() {
  return document.getElementById('folderPath').value.trim();
}

function gifPrimaryFile() {
  const entry = getPreviewEntry();
  return entry ? entry.path : '';
}

function collectGifFilePayload() {
  return {
    tiffEntries: _tiffEntries.map(e => ({...e})),
    roiPolygons: _roiPolygons.map(p => ({...p, points: Array.isArray(p.points) ? p.points.map(pt => ({...pt})) : []})),
    cropRects: _cropRects.map(r => ({...r})),
  };
}

function restoreGifFilePayload(payload, includeQueue) {
  if (!payload || typeof payload !== 'object') return;
  if (includeQueue && Array.isArray(payload.tiffEntries)) {
    _tiffEntries = payload.tiffEntries.map(e => ({
      id: e.id || ('te_' + (_entryCounter++)),
      path: e.path || '',
      frames: e.frames ?? null,
      slices: e.slices || '',
      scale: e.scale || null,
    }));
    _entryCounter = Math.max(_entryCounter, _tiffEntries.length + 1);
    renderTiffList();
    scanFrameCounts(false);
  }
  if (Array.isArray(payload.roiPolygons)) {
    _roiPolygons = payload.roiPolygons.map(p => ({...p, points: Array.isArray(p.points) ? p.points.map(pt => ({...pt})) : []}));
    const maxPolygonIdx = _roiPolygons.reduce((m, p) => Math.max(m, parseInt(String(p.label || '').replace(/\D/g, ''), 10) || 0), 0);
    _nextPolygonIdx = maxPolygonIdx + 1;
    renderPolygonList();
  }
  if (Array.isArray(payload.cropRects)) {
    _cropRects = payload.cropRects.map(r => ({...r}));
    const maxCropIdx = _cropRects.reduce((m, r) => Math.max(m, parseInt(String(r.label || '').replace(/\D/g, ''), 10) || 0), 0);
    _nextCropRectIdx = maxCropIdx + 1;
    renderCropRectList();
  }
  updateGifCropControls();
  drawPolygons();
}

window.dpApplyRunManifest = manifest => {
  dpApplyRunManifestFallback(manifest);
  const params = manifest.parameters || {};
  if (params.settings) applyGifPrefs(params.settings);
  if (Array.isArray(params.roi_polygons)) {
    _roiPolygons = params.roi_polygons.map(p => ({...p, points: Array.isArray(p.points) ? p.points.map(pt => ({...pt})) : []}));
    renderPolygonList();
  }
  if (manifest.input_files && manifest.input_files.length) {
    _tiffEntries = manifest.input_files
      .filter(rec => rec.path)
      .map((rec, i) => ({id: 'manifest_' + i, path: rec.path, frames: null, slices: rec.slices || '', scale: null}));
    _entryCounter = _tiffEntries.length + 1;
    if (_tiffEntries.length) document.getElementById('folderPath').value = manifest.project_root || dpPathDir(_tiffEntries[0].path);
    renderTiffList();
    scanFrameCounts(false);
  }
  scheduleGifPreview();
  setStatus('status', 'GIF parameters loaded from run manifest', 'ok');
};

function renderGifFileProfileOptions(data) {
  _gifFileProfileState = data || null;
  const select = document.getElementById('gifFileProfileSelect');
  if (!select) return;
  if (!gifPrimaryFile()) {
    select.innerHTML = '<option value="">No file selected</option>';
    return;
  }
  const selected = data?.selected_profile || data?.last_profile || '';
  select.innerHTML = fileProfileOptionsHtml(data?.profiles || {}, selected);
}

async function loadGifProfileForCurrent(auto, includeQueue = false) {
  const file = gifPrimaryFile();
  if (!file) return null;
  try {
    const data = await loadFileProfile('fluorescence_gif', file, gifProjectRoot(), '');
    renderGifFileProfileOptions(data);
    const shouldApply = !!data.profile && (!auto || document.getElementById('gifAutoLoadProfile')?.checked);
    if (shouldApply) {
      applyGifPrefs(data.profile.settings || {});
      restoreGifFilePayload(data.profile.payload || {}, includeQueue);
      scheduleGifPreview();
      setStatus('gifFileProfileStatus', `Loaded file profile: ${data.selected_profile || data.last_profile || 'default'}`, data.stale ? 'warning' : 'ok');
    } else if (auto && document.getElementById('gifAutoLoadProfile')?.checked && !data.profile) {
      const created = await saveFileProfile('fluorescence_gif', file, gifProjectRoot(), 'default', collectGifPrefs(), collectGifFilePayload());
      renderGifFileProfileOptions(created);
      setStatus('gifFileProfileStatus', 'Created default file profile for this TIFF.', 'ok');
    } else {
      setStatus('gifFileProfileStatus', 'No saved profile for this TIFF yet.', '');
    }
    return data;
  } catch (e) {
    setStatus('gifFileProfileStatus', 'File profile not loaded: ' + e.message, 'warning');
    return null;
  }
}

async function loadSelectedGifFileProfile(auto) {
  const file = gifPrimaryFile();
  if (!file) { setStatus('gifFileProfileStatus', 'Select or queue a TIFF first.', 'error'); return; }
  const name = document.getElementById('gifFileProfileSelect').value;
  if (!name) { setStatus('gifFileProfileStatus', 'No profile selected.', 'error'); return; }
  try {
    const data = await loadFileProfile('fluorescence_gif', file, gifProjectRoot(), name);
    renderGifFileProfileOptions(data);
    if (data.profile) {
      applyGifPrefs(data.profile.settings || {});
      restoreGifFilePayload(data.profile.payload || {}, true);
      scheduleGifPreview();
      setStatus('gifFileProfileStatus', `Loaded file profile: ${name}`, data.stale ? 'warning' : 'ok');
    }
  } catch (e) {
    setStatus('gifFileProfileStatus', 'Load failed: ' + e.message, 'error');
  }
}

async function saveGifFileProfile(saveAs) {
  const file = gifPrimaryFile();
  if (!file) { setStatus('gifFileProfileStatus', 'Select or queue a TIFF first.', 'error'); return; }
  let name = document.getElementById('gifFileProfileSelect').value || 'default';
  if (saveAs) {
    name = promptProfileName(name);
    if (!name) return;
  }
  try {
    const data = await saveFileProfile('fluorescence_gif', file, gifProjectRoot(), name, collectGifPrefs(), collectGifFilePayload());
    renderGifFileProfileOptions(data);
    setStatus('gifFileProfileStatus', `Saved file profile: ${name}`, 'ok');
    toast('GIF file profile saved');
  } catch (e) {
    setStatus('gifFileProfileStatus', 'Save failed: ' + e.message, 'error');
  }
}

async function deleteSelectedGifFileProfile() {
  const file = gifPrimaryFile();
  const name = document.getElementById('gifFileProfileSelect').value;
  if (!file || !name || !confirm(`Delete file profile "${name}"?`)) return;
  try {
    const data = await deleteFileProfile('fluorescence_gif', file, gifProjectRoot(), name);
    renderGifFileProfileOptions(data);
    setStatus('gifFileProfileStatus', `Deleted file profile: ${name}`, 'ok');
  } catch (e) {
    setStatus('gifFileProfileStatus', 'Delete failed: ' + e.message, 'error');
  }
}

function setPreviewPlaceholder(message) {
  const wrap = document.getElementById('gifPreviewWrap');
  const ph = document.getElementById('gifPreviewPlaceholder');
  const canvas = document.getElementById('gifRoiCanvas');
  if (wrap) wrap.style.display = 'none';
  if (canvas) canvas.style.display = 'none';
  if (ph) {
    ph.style.display = '';
    ph.textContent = message;
  }
  _imgW = 0;
  _imgH = 0;
}

function setPreviewImage(b64, isGif) {
  const wrap = document.getElementById('gifPreviewWrap');
  const ph = document.getElementById('gifPreviewPlaceholder');
  const img = document.getElementById('gifPreviewImg');
  const canvas = document.getElementById('gifRoiCanvas');
  if (!wrap || !img) return;
  if (ph) ph.style.display = 'none';
  wrap.style.display = '';
  if (canvas) canvas.style.display = isGif ? 'none' : '';
  img.onload = () => {
    initRoiCanvas(!isGif);
  };
  img.src = `data:image/${isGif ? 'gif' : 'png'};base64,${b64}`;
}

function setGeneratedGifPreview(b64, metaText, outputPath) {
  const box = document.getElementById('generatedGifPreview');
  const meta = document.getElementById('generatedGifMeta');
  const name = document.getElementById('generatedGifName');
  if (!box) return;
  if (!b64) {
    box.innerHTML = '<div class="plot-placeholder">Generated GIF preview is unavailable for this output.</div>';
  } else {
    box.innerHTML = `<img src="data:image/gif;base64,${b64}" alt="generated GIF preview"/>`;
  }
  if (meta) meta.textContent = metaText || '';
  if (name) name.textContent = outputPath ? fileBasename(outputPath) : '';
}

function clearGeneratedGifPreview() {
  setGeneratedGifPreview('', 'Generate a GIF to preview the animation here.', '');
}

async function refreshGifPreview() {
  const meta = document.getElementById('gifPreviewMeta');
  const name = document.getElementById('gifPreviewName');
  const entry = getPreviewEntry();
  if (!meta) return;
  if (!entry || !entry.path) {
    setPreviewPlaceholder('Scan a folder or choose a TIFF stack to preview');
    meta.textContent = '';
    if (name) name.textContent = '';
    return;
  }
  try {
    const d = await api('/api/fluorescence/gif_preview', {
      input_path: entry.path.trim(),
      slice_spec: (entry.slices || '').trim(),
      fps: parseFloat(document.getElementById('gifFps').value) || 5,
      lut: document.getElementById('gifLut').value,
      scale_bar_um: parseFloat(document.getElementById('gifBarUm').value) || 0,
      px_per_um: parseFloat(document.getElementById('gifPxPerUm').value) || 3.45,
      auto_scale: document.getElementById('gifAutoScale').checked,
      add_timestamp: gifLabelMode() !== 'none',
      label_mode: gifLabelMode(),
      show_roi_overlay: gifShowRoiOverlay(),
      roi_polygons: getClosedRoiPolygons(),
      ...gifCropPayload(),
    });
    if (d.error) {
      setPreviewPlaceholder(d.error);
      meta.textContent = '';
      if (name) name.textContent = '';
      return;
    }
    _imgW = d.width || 0;
    _imgH = d.height || 0;
    setPreviewImage(d.img, false);
    meta.textContent = `Slice ${d.frame} / ${d.n_frames} · ${formatScaleInfo(d)}`;
    if (name) name.textContent = entry.name || fileBasename(entry.path);
  } catch(ex) {
    setPreviewPlaceholder(ex.message || String(ex));
    meta.textContent = '';
  }
}

/* ---------- Polygon ROI overlay ---------- */

function getClosedRoiPolygons() {
  return _roiPolygons
    .filter(p => p.points && p.points.length >= 3)
    .map(p => ({
      label: p.label,
      color: p.color,
      points: p.points.map(pt => ({x: pt.x, y: pt.y})),
    }));
}

function getCropRects() {
  return _cropRects
    .filter(r => Number(r.width) >= 2 && Number(r.height) >= 2)
    .map(r => ({
      label: r.label,
      color: r.color,
      x: Math.round(r.x),
      y: Math.round(r.y),
      width: Math.round(r.width),
      height: Math.round(r.height),
    }));
}

function updateGifBgControls(preferredLabel) {
  const modeEl = document.getElementById('gifBgMode');
  const row = document.getElementById('gifBgRoiRow');
  const sel = document.getElementById('gifBgRoiSelect');
  if (!modeEl || !row || !sel) return;

  const mode = modeEl.value;
  row.style.display = mode === 'roi' ? 'flex' : 'none';
  const prev = preferredLabel || sel.value;
  const polys = getClosedRoiPolygons();
  sel.innerHTML = polys.map(p => `<option value="${escHtml(p.label)}">${escHtml(p.label)}</option>`).join('');
  if (polys.some(p => p.label === prev)) sel.value = prev;
  else if (polys.length) sel.value = polys[0].label;
}

function gifBgLabel() {
  const mode = document.getElementById('gifBgMode')?.value || 'none';
  if (mode !== 'roi') return '';
  return document.getElementById('gifBgRoiSelect')?.value || '';
}

function updateGifKymoControls() {
  const sel = document.getElementById('gifKymoRoiSelect');
  if (!sel) return;
  const prev = sel.value;
  const bgLabel = gifBgLabel();
  const polys = getClosedRoiPolygons().filter(p => p.label !== bgLabel);
  sel.innerHTML = polys.map(p => `<option value="${escHtml(p.label)}">${escHtml(p.label)}</option>`).join('');
  if (polys.some(p => p.label === prev)) sel.value = prev;
  else if (polys.length) sel.value = polys[0].label;
}

function updateGifCropControls() {
  const modeEl = document.getElementById('gifCropMode');
  const row = document.getElementById('gifCropRoiRow');
  const padRow = document.getElementById('gifCropPadRow');
  const sel = document.getElementById('gifCropRoiSelect');
  if (!modeEl || !row || !padRow || !sel) return;

  const mode = modeEl.value || 'full';
  const needsRoi = mode === 'selected_rect';
  const needsCrop = mode !== 'full';
  row.style.display = needsRoi ? 'flex' : 'none';
  padRow.style.display = needsCrop ? 'flex' : 'none';

  const prev = sel.value;
  const rects = getCropRects();
  sel.innerHTML = rects.map(r => `<option value="${escHtml(r.label)}">${escHtml(r.label)}</option>`).join('');
  if (rects.some(r => r.label === prev)) sel.value = prev;
  else if (rects.length) sel.value = rects[0].label;
}

function gifLabelMode() {
  return document.getElementById('gifLabelMode')?.value || 'frame';
}

function gifShowRoiOverlay() {
  return !!document.getElementById('gifShowRoiOverlay')?.checked;
}

function gifCropPayload() {
  const mode = document.getElementById('gifCropMode')?.value || 'full';
  const rectLabel = document.getElementById('gifCropRoiSelect')?.value || '';
  const padding = parseInt(document.getElementById('gifCropPadding')?.value, 10);
  return {
    crop_mode: mode,
    crop_rect_label: mode === 'selected_rect' ? rectLabel : '',
    crop_rects: getCropRects(),
    crop_padding_px: Number.isFinite(padding) ? Math.max(0, padding) : 0,
  };
}

function renumberGifPolygons() {
  const bgBefore = gifBgLabel();
  const bgIndex = _roiPolygons.findIndex(p => p.label === bgBefore);
  _roiPolygons.forEach((p, i) => {
    p.label = 'ROI ' + (i + 1);
    p.color = GIF_ROI_COLORS[i % GIF_ROI_COLORS.length];
  });
  if (_draftPolygon) {
    const idx = _roiPolygons.length + 1;
    _draftPolygon.label = 'ROI ' + idx;
    _draftPolygon.color = GIF_ROI_COLORS[(idx - 1) % GIF_ROI_COLORS.length];
  }
  _nextPolygonIdx = _roiPolygons.length + (_draftPolygon ? 2 : 1);
  return bgIndex >= 0 && _roiPolygons[bgIndex] ? _roiPolygons[bgIndex].label : '';
}

function startPolygon() {
  if (!_imgW || !_imgH) {
    setStatus('status', 'Load a preview frame before drawing polygon ROI', 'error');
    return;
  }
  const cropModeEl = document.getElementById('gifCropMode');
  if (cropModeEl && cropModeEl.value !== 'full') {
    cropModeEl.value = 'full';
    updateGifCropControls();
    scheduleGifPreview();
    setStatus('status', 'Switched to full frame preview. Click + Polygon again after the preview refreshes.', 'ok');
    return;
  }
  _draftCropRect = null;
  _cropRectDragStart = null;
  if (_draftPolygon && _draftPolygon.points.length >= 3) closePolygon();
  const idx = _roiPolygons.length + 1;
  _draftPolygon = {
    label: 'ROI ' + idx,
    color: GIF_ROI_COLORS[(idx - 1) % GIF_ROI_COLORS.length],
    points: [],
  };
  _nextPolygonIdx = idx + 1;
  document.getElementById('polygonHint').textContent = 'Click preview to add polygon points, then Done Drawing.';
  renderPolygonList();
  drawPolygons();
}

function closePolygon() {
  if (!_draftPolygon) {
    setStatus('status', 'No active polygon to close', 'error');
    return;
  }
  if (_draftPolygon.points.length < 3) {
    setStatus('status', 'Polygon needs at least 3 points', 'error');
    return;
  }
  _roiPolygons.push(_draftPolygon);
  _draftPolygon = null;
  document.getElementById('polygonHint').textContent = `${_roiPolygons.length} polygon ROI marker(s) ready · drawing complete`;
  renderPolygonList();
  drawPolygons();
}

function finishPolygonDrawing() {
  if (_draftPolygon) {
    closePolygon();
    return;
  }
  if (_roiPolygons.length) {
    document.getElementById('polygonHint').textContent = `${_roiPolygons.length} polygon ROI marker(s) ready · drawing complete`;
    setStatus('status', 'ROI drawing complete', 'ok');
  } else {
    setStatus('status', 'No ROI has been drawn yet', 'error');
  }
}

function undoPolygonPoint() {
  if (_draftPolygon && _draftPolygon.points.length) {
    _draftPolygon.points.pop();
  } else if (_roiPolygons.length) {
    const last = _roiPolygons[_roiPolygons.length - 1];
    last.points.pop();
    if (last.points.length < 3) _roiPolygons.pop();
  }
  renderPolygonList();
  drawPolygons();
}

function removePolygon(i) {
  if (i < 0 || i >= _roiPolygons.length) return;
  _roiPolygons.splice(i, 1);
  renderPolygonList();
  drawPolygons();
}

function clearPolygons() {
  _roiPolygons = [];
  _draftPolygon = null;
  _nextPolygonIdx = 1;
  document.getElementById('polygonHint').textContent = 'Click + Polygon, then click the preview image to add points.';
  renderPolygonList();
  drawPolygons();
}

function renderPolygonList() {
  const el = document.getElementById('polygonList');
  if (!el) return;
  const preferredBgLabel = renumberGifPolygons();
  let bgLabel = preferredBgLabel || gifBgLabel();
  if (document.getElementById('gifBgMode')?.value === 'roi') {
    if (!_roiPolygons.some(p => p.label === bgLabel)) {
      bgLabel = _roiPolygons.length ? _roiPolygons[0].label : '';
    }
  }
  const rows = _roiPolygons.map((p, i) => `
    <div class="gif-roi-item">
      <span class="gif-roi-swatch" style="background:${p.color}"></span>
      <span class="gif-roi-name">${escHtml(p.label)}${p.label === bgLabel ? ' · BG' : ''} · ${p.points.length} pts</span>
      <button class="btn-icon btn-danger" style="width:24px;min-width:24px;height:24px;padding:0" onclick="removePolygon(${i})">×</button>
    </div>`);
  if (_draftPolygon) {
    rows.push(`
      <div class="gif-roi-item">
        <span class="gif-roi-swatch" style="background:${_draftPolygon.color}"></span>
        <span class="gif-roi-name">${escHtml(_draftPolygon.label)} · drawing · ${_draftPolygon.points.length} pts</span>
      </div>`);
  }
  el.innerHTML = rows.join('');
  updateGifBgControls(preferredBgLabel);
  updateGifKymoControls();
  updateGifCropControls();
}

function renumberCropRects() {
  _cropRects.forEach((r, i) => {
    r.label = 'ROI2 ' + (i + 1);
    r.color = GIF_CROP_COLORS[i % GIF_CROP_COLORS.length];
  });
  if (_draftCropRect) {
    const idx = _cropRects.length + 1;
    _draftCropRect.label = 'ROI2 ' + idx;
    _draftCropRect.color = GIF_CROP_COLORS[(idx - 1) % GIF_CROP_COLORS.length];
  }
  _nextCropRectIdx = _cropRects.length + (_draftCropRect ? 2 : 1);
}

function startCropRect() {
  if (!_imgW || !_imgH) {
    setStatus('status', 'Load a preview frame before drawing ROI2 crop rectangle', 'error');
    return;
  }
  const cropModeEl = document.getElementById('gifCropMode');
  if (cropModeEl && cropModeEl.value !== 'full') {
    cropModeEl.value = 'full';
    updateGifCropControls();
    scheduleGifPreview();
    setStatus('status', 'Switched to full frame preview. Click + Rectangle again after the preview refreshes.', 'ok');
    return;
  }
  if (_draftPolygon) {
    if (_draftPolygon.points.length >= 3) closePolygon();
    else _draftPolygon = null;
  }
  const idx = _cropRects.length + 1;
  _draftCropRect = {
    label: 'ROI2 ' + idx,
    color: GIF_CROP_COLORS[(idx - 1) % GIF_CROP_COLORS.length],
    x: 0,
    y: 0,
    width: 0,
    height: 0,
  };
  _cropRectDragStart = null;
  _nextCropRectIdx = idx + 1;
  document.getElementById('cropRectHint').textContent = 'Drag on the preview image to define a rectangular crop ROI2.';
  renderCropRectList();
  drawPolygons();
}

function normalizeRectObject(r) {
  let x = Number(r.x) || 0;
  let y = Number(r.y) || 0;
  let w = Number(r.width) || 0;
  let h = Number(r.height) || 0;
  if (w < 0) { x += w; w = Math.abs(w); }
  if (h < 0) { y += h; h = Math.abs(h); }
  x = Math.max(0, Math.min(_imgW - 1, Math.round(x)));
  y = Math.max(0, Math.min(_imgH - 1, Math.round(y)));
  w = Math.max(0, Math.min(_imgW - x, Math.round(w)));
  h = Math.max(0, Math.min(_imgH - y, Math.round(h)));
  return {...r, x, y, width: w, height: h};
}

function finishCropRect() {
  if (!_draftCropRect) return;
  const rect = normalizeRectObject(_draftCropRect);
  _draftCropRect = null;
  _cropRectDragStart = null;
  if (rect.width < 2 || rect.height < 2) {
    document.getElementById('cropRectHint').textContent = 'ROI2 rectangle was too small; drag a larger rectangle.';
    renderCropRectList();
    drawPolygons();
    return;
  }
  _cropRects.push(rect);
  const modeEl = document.getElementById('gifCropMode');
  if (modeEl) modeEl.value = 'selected_rect';
  document.getElementById('cropRectHint').textContent = `${_cropRects.length} ROI2 crop rectangle(s) ready`;
  renderCropRectList();
  drawPolygons();
  scheduleGifPreview();
}

function removeCropRect(i) {
  if (i < 0 || i >= _cropRects.length) return;
  _cropRects.splice(i, 1);
  renderCropRectList();
  drawPolygons();
  scheduleGifPreview();
}

function clearCropRects() {
  _cropRects = [];
  _draftCropRect = null;
  _cropRectDragStart = null;
  _nextCropRectIdx = 1;
  const modeEl = document.getElementById('gifCropMode');
  if (modeEl) modeEl.value = 'full';
  document.getElementById('cropRectHint').textContent = 'Click + Rectangle, then drag on the preview image.';
  renderCropRectList();
  drawPolygons();
  scheduleGifPreview();
}

function renderCropRectList() {
  const el = document.getElementById('cropRectList');
  if (!el) return;
  renumberCropRects();
  const rows = _cropRects.map((r, i) => `
    <div class="gif-roi-item">
      <span class="gif-roi-swatch" style="background:${r.color}"></span>
      <span class="gif-roi-name">${escHtml(r.label)} · ${Math.round(r.width)}×${Math.round(r.height)} px</span>
      <button class="btn-icon btn-danger" style="width:24px;min-width:24px;height:24px;padding:0" onclick="removeCropRect(${i})">×</button>
    </div>`);
  if (_draftCropRect) {
    const r = normalizeRectObject(_draftCropRect);
    rows.push(`
      <div class="gif-roi-item">
        <span class="gif-roi-swatch" style="background:${_draftCropRect.color}"></span>
        <span class="gif-roi-name">${escHtml(_draftCropRect.label)} · drawing · ${Math.round(r.width)}×${Math.round(r.height)} px</span>
      </div>`);
  }
  el.innerHTML = rows.join('');
  updateGifCropControls();
}

function initRoiCanvas(showCanvas = true) {
  const img = document.getElementById('gifPreviewImg');
  const canvas = document.getElementById('gifRoiCanvas');
  if (!img || !canvas || !img.clientWidth || !img.clientHeight) return;
  canvas.width = img.clientWidth;
  canvas.height = img.clientHeight;
  canvas.style.width = img.clientWidth + 'px';
  canvas.style.height = img.clientHeight + 'px';
  canvas.style.display = showCanvas ? '' : 'none';
  if (showCanvas) drawPolygons();
}

function nativeToCanvas(pt) {
  const canvas = document.getElementById('gifRoiCanvas');
  return {
    x: pt.x / Math.max(1, _imgW) * canvas.width,
    y: pt.y / Math.max(1, _imgH) * canvas.height,
  };
}

function canvasToNative(cx, cy) {
  const canvas = document.getElementById('gifRoiCanvas');
  return {
    x: Math.round(cx / Math.max(1, canvas.width) * _imgW),
    y: Math.round(cy / Math.max(1, canvas.height) * _imgH),
  };
}

function drawOnePolygon(ctx, poly, closed, isBg = false) {
  if (!poly || !poly.points || !poly.points.length) return;
  const pts = poly.points.map(nativeToCanvas);
  ctx.strokeStyle = poly.color;
  ctx.fillStyle = poly.color;
  ctx.lineWidth = 2;
  ctx.setLineDash(isBg || !closed ? [5, 3] : []);
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (const p of pts.slice(1)) ctx.lineTo(p.x, p.y);
  if (closed && pts.length >= 3) ctx.closePath();
  ctx.stroke();
  ctx.setLineDash([]);
  for (const p of pts) {
    ctx.beginPath();
    ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.font = 'bold 12px sans-serif';
  ctx.fillText(poly.label + (isBg ? ' (BG)' : ''), pts[0].x + 6, pts[0].y + 14);
}

function drawOneCropRect(ctx, rect, isDraft = false) {
  if (!rect) return;
  const r = normalizeRectObject(rect);
  if (r.width <= 0 || r.height <= 0) return;
  const p0 = nativeToCanvas({x: r.x, y: r.y});
  const p1 = nativeToCanvas({x: r.x + r.width, y: r.y + r.height});
  const x = Math.min(p0.x, p1.x);
  const y = Math.min(p0.y, p1.y);
  const w = Math.abs(p1.x - p0.x);
  const h = Math.abs(p1.y - p0.y);
  ctx.save();
  ctx.strokeStyle = rect.color || '#38bdf8';
  ctx.fillStyle = rect.color || '#38bdf8';
  ctx.lineWidth = 2;
  ctx.setLineDash(isDraft ? [6, 4] : [3, 2]);
  ctx.strokeRect(x, y, w, h);
  ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(0, 0, 0, 0.68)';
  ctx.fillRect(x + 4, y + 4, 64, 18);
  ctx.fillStyle = rect.color || '#38bdf8';
  ctx.font = 'bold 12px sans-serif';
  ctx.fillText(rect.label || 'ROI2', x + 8, y + 17);
  ctx.restore();
}

function drawPolygons() {
  const canvas = document.getElementById('gifRoiCanvas');
  if (!canvas || !canvas.width || canvas.style.display === 'none') return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if ((document.getElementById('gifCropMode')?.value || 'full') !== 'full') return;
  const bgLabel = gifBgLabel();
  _roiPolygons.forEach(p => drawOnePolygon(ctx, p, true, p.label === bgLabel));
  if (_draftPolygon) drawOnePolygon(ctx, _draftPolygon, false);
  _cropRects.forEach(r => drawOneCropRect(ctx, r, false));
  if (_draftCropRect) drawOneCropRect(ctx, _draftCropRect, true);
}

/* ---------- Scan frame counts ---------- */
async function scanFrameCounts(showStatus = true) {
  const paths = _tiffEntries.map(e => e.path.trim()).filter(Boolean);
  if (!paths.length) { toast('Add at least one TIFF first', true); return; }
  if (showStatus) setStatus('status', 'Scanning frames…', 'loading');
  try {
    const d = await api('/api/fluorescence/tiff_info_batch', { paths });
    if (d.error) { if (showStatus) setStatus('status', 'Scan error: ' + d.error, 'error'); return; }
    const infoMap = d.info || {};
    _tiffEntries.forEach(e => {
      const info = infoMap[e.path.trim()];
      e.frames = info ? info.n_frames : null;
      e.scale = info || null;
    });
    renderTiffList();
    const total = _tiffEntries.reduce((s, e) => s + (e.frames || 0), 0);
    if (showStatus) setStatus('status', `Scanned — total ${total} frames across ${Object.keys(infoMap).length} file(s)`, 'ok');
    scheduleGifPreview();
  } catch(ex) { if (showStatus) setStatus('status', 'Scan failed: ' + ex.message, 'error'); }
}

/* ---------- Generate GIF ---------- */
async function runGifBackgroundJob(endpoint, payload, label) {
  return dpRunJobEndpoint(endpoint, payload, {
    interval_ms: 900,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `${label}${pct}${msg}`, 'loading');
    },
  });
}

async function generateGif() {
  const paths = _tiffEntries.map(e => e.path.trim()).filter(Boolean);
  if (!paths.length) { toast('Add at least one TIFF first', true); return; }

  btnBusy('btnGenerate', true, 'Generating…');
  setStatus('status', 'Generating GIF…', 'loading');
  document.getElementById('resultArea').innerHTML = '';

  const payload = {
    tiff_paths:    paths,
    slice_specs:   _tiffEntries.filter(e => e.path.trim()).map(e => (e.slices || '').trim()),
    fps:           parseFloat(document.getElementById('gifFps').value) || 5,
    lut:           document.getElementById('gifLut').value,
    scale_bar_um:  parseFloat(document.getElementById('gifBarUm').value) || 0,
    px_per_um:     parseFloat(document.getElementById('gifPxPerUm').value) || 3.45,
    auto_scale:    document.getElementById('gifAutoScale').checked,
    add_timestamp: gifLabelMode() !== 'none',
    label_mode:    gifLabelMode(),
    show_roi_overlay: gifShowRoiOverlay(),
    roi_polygons:  getClosedRoiPolygons(),
    ...gifCropPayload(),
    output_path:   document.getElementById('gifOutput').value.trim(),
  };

  try {
    const d = await runGifBackgroundJob('/api/fluorescence/merge_gif_job', payload, 'Generating GIF');
    btnBusy('btnGenerate', false, 'Generate GIF');

    if (d.error) {
      setStatus('status', 'Error: ' + d.error, 'error');
      document.getElementById('resultArea').innerHTML =
        `<pre class="log-box">${escHtml(d.error)}</pre>`;
      return;
    }

    const sliceNote = d.selected_slices ? ` (${d.selected_slices} selected)` : '';
    setStatus('status', `Done — ${d.n_frames} frames${sliceNote} → ${d.output_path}`, 'ok');

    setGeneratedGifPreview(
      d.gif_preview || '',
      `Generated GIF · ${formatScaleInfo(d)} · ${d.roi_polygons || 0} polygon ROI · marked ${d.show_roi_overlay ? 'yes' : 'no'}`,
      d.output_path || ''
    );

    /* Result card */
    const outDir = d.output_path ? d.output_path.replace(/\/[^\/]+$/, '') : '';
    document.getElementById('resultArea').innerHTML = `
      <div class="result-card" style="padding:12px 16px;background:#f8fffe;border:1px solid #cce;border-radius:6px">
        <div style="font-weight:600;margin-bottom:6px">GIF Generated</div>
        <div style="font-size:12px;color:#555;margin-bottom:4px">Frames: <b>${d.n_frames}</b></div>
        ${d.selected_slices ? `<div style="font-size:12px;color:#555;margin-bottom:4px">Selected slices: <b>${d.selected_slices}</b></div>` : ''}
        <div style="font-size:12px;color:#555;margin-bottom:4px">Polygon ROI: <b>${d.roi_polygons || 0}</b> · marked: <b>${d.show_roi_overlay ? 'yes' : 'no'}</b></div>
        ${d.crop && d.crop.mode && d.crop.mode !== 'full' ? `<div style="font-size:12px;color:#555;margin-bottom:4px">Crop: <b>${escHtml(d.crop.width)}×${escHtml(d.crop.height)} px</b> from (${escHtml(d.crop.x)}, ${escHtml(d.crop.y)})</div>` : ''}
        <div style="font-size:12px;color:#555;margin-bottom:4px">Scale: <b>${escHtml(formatScaleInfo(d))}</b></div>
        <div style="font-size:12px;color:#555;margin-bottom:8px;word-break:break-all">Path: <code>${escHtml(d.output_path)}</code></div>
        ${outDir ? `<button class="btn-secondary" onclick="openFolder('${escHtml(outDir)}')">Open Folder</button>` : ''}
      </div>`;
    recordRunHistory({
      view: 'fluorescence_gif',
      title: 'Merged GIF',
      status: 'ok',
      project_root: gifProjectRoot(),
      input_files: gifInputFileRecords(_tiffEntries.filter(e => e.path && e.path.trim())),
      outputs: d.output_path ? [{path: d.output_path, type: 'gif'}] : [],
      parameters: payload,
      metadata: {
        n_frames: d.n_frames || 0,
        selected_slices: d.selected_slices || '',
        scale: formatScaleInfo(d),
      },
    });
  } catch(ex) {
    btnBusy('btnGenerate', false, 'Generate GIF');
    setStatus('status', 'Request failed: ' + ex.message, 'error');
  }
}

function gifAnalysisEntries() {
  const queued = _tiffEntries
    .filter(e => e.path && e.path.trim())
    .map(e => ({path: e.path.trim(), slices: (e.slices || '').trim()}));
  if (queued.length) return queued;

  const preview = getPreviewEntry();
  if (preview && preview.path) {
    return [{path: preview.path.trim(), slices: (preview.slices || '').trim()}];
  }
  return [];
}

function gifInputFileRecords(entries) {
  return (entries || [])
    .filter(e => e && e.path)
    .map(e => ({path: e.path, role: 'source_tiff', slices: e.slices || ''}));
}

function gifRoiNowStamp() {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, '_');
}

function sanitizeGifRoiPrefix(s) {
  const raw = String(s || '').trim() || 'gif_roi_time_analysis';
  return raw.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '') || 'gif_roi_time_analysis';
}

function buildGifRoiPrefix() {
  return sanitizeGifRoiPrefix(document.getElementById('gifRoiPrefix')?.value) + '_' + gifRoiNowStamp();
}

function buildGifRoiReferencePrefix(entry) {
  const baseName = fileBasename(entry && entry.path ? entry.path : '').replace(/\.[^.]+$/, '');
  const raw = (baseName || 'gif') + '_roi_reference';
  return raw.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '') + '_' + gifRoiNowStamp();
}

function upsertGifResultCard(cardId, headerHtml, bodyHtml) {
  const area = document.getElementById('resultArea');
  area.style.display = 'flex';
  let card = document.getElementById(cardId);
  if (!card) {
    card = document.createElement('div');
    card.id = cardId;
    card.className = 'result-card';
    area.prepend(card);
  }
  card.innerHTML = `
    <div class="result-card-header">${headerHtml}</div>
    <div class="result-card-body" style="padding:8px">${bodyHtml}</div>`;
}

async function exportGifRoiPreview() {
  const entry = getPreviewEntry();
  const rois = getClosedRoiPolygons();
  if (!entry || !entry.path) {
    setStatus('status', 'Choose a preview TIFF before exporting ROI preview', 'error');
    return;
  }
  if (!rois.length) {
    setStatus('status', 'Draw and close at least one polygon ROI first', 'error');
    return;
  }

  btnBusy('btnExportGifRoiPreview', true, 'Saving...');
  setStatus('status', 'Saving ROI preview PNG...', 'loading');
  try {
    const d = await runGifBackgroundJob('/api/fluorescence/gif_roi/export_preview_job', {
      input_path: entry.path.trim(),
      slice_spec: (entry.slices || '').trim(),
      roi_polygons: rois,
      lut: document.getElementById('gifLut').value,
      scale_bar_um: parseFloat(document.getElementById('gifBarUm').value) || 0,
      px_per_um: parseFloat(document.getElementById('gifPxPerUm').value) || 3.45,
      auto_scale: document.getElementById('gifAutoScale').checked,
      show_name: true,
      show_scale_bar: true,
      ...gifCropPayload(),
      prefix: buildGifRoiReferencePrefix(entry),
    }, 'Saving ROI preview PNG');
    btnBusy('btnExportGifRoiPreview', false, 'Save ROI Preview');
    if (d.error) throw new Error(d.error);
    _gifRoiDefaultOutputDir = d.output_dir || _gifRoiDefaultOutputDir;

    const openButton = d.output_dir
      ? `<button class="btn-secondary" data-folder="${escHtml(d.output_dir)}" onclick="openFolder(this.dataset.folder)">Open Folder</button>`
      : '';
    const header =
      `ROI Preview <span style="font-weight:400;color:var(--silver)">` +
      `${d.roi_polygons || rois.length} ROI | slice ${d.frame} | ${escHtml(formatScaleInfo(d))}</span>`;
    const body = `
      <img src="data:image/png;base64,${d.img}" style="max-width:100%;border-radius:4px"/>
      <div style="font-size:11px;color:var(--silver);margin-top:6px;word-break:break-all">Saved: <code>${escHtml(d.output_path || '')}</code></div>
      <div style="font-size:11px;color:var(--silver);margin-top:2px">Scale bar: ${Number(d.scale_bar_um || 0).toPrecision(4)} um · ${escHtml(formatScaleInfo(d))}</div>
      <div style="margin-top:8px">${openButton}</div>`;
    upsertGifResultCard('gifRoiPreviewResultCard', header, body);
    setStatus('status', 'ROI preview saved: ' + (d.output_path || ''), 'ok');
    toast('ROI preview saved');
    recordRunHistory({
      view: 'fluorescence_gif',
      title: 'GIF ROI Preview',
      status: 'ok',
      project_root: gifProjectRoot(),
      input_files: gifInputFileRecords([entry]),
      outputs: d.output_path ? [{path: d.output_path, type: 'roi_preview_png'}] : [],
      parameters: {
        settings: collectGifPrefs(),
        roi_polygons: rois,
        crop: gifCropPayload(),
      },
      metadata: {
        output_dir: d.output_dir || '',
        frame: d.frame,
      },
    });
  } catch(ex) {
    btnBusy('btnExportGifRoiPreview', false, 'Save ROI Preview');
    setStatus('status', 'Save failed: ' + ex.message, 'error');
    toast('Save failed: ' + ex.message, true);
  }
}

function buildGifRoiAnalysisPayload() {
  const entries = gifAnalysisEntries();
  if (!entries.length) {
    throw new Error('Add at least one TIFF to the queue, or select a preview TIFF');
  }

  const allPolys = getClosedRoiPolygons();
  if (!allPolys.length) {
    throw new Error('Draw and close at least one polygon ROI first');
  }

  const bgMode = document.getElementById('gifBgMode').value;
  const bgLabel = gifBgLabel();
  const plotMetric = document.getElementById('gifRoiPlotMetric').value;
  let bgRoi = null;
  let signalRois = allPolys.slice();

  if (bgMode === 'roi') {
    bgRoi = allPolys.find(p => p.label === bgLabel) || null;
    if (!bgRoi) throw new Error('Select a background ROI or change BG mode');
    signalRois = allPolys.filter(p => p.label !== bgLabel);
  }

  if (!signalRois.length) {
    throw new Error('All polygons are assigned to background; draw another signal ROI');
  }
  if ((plotMetric === 'bg_subtracted' || plotMetric === 'bg_normalized') && bgMode === 'none') {
    throw new Error('Choose a BG mode before using BG Subtracted or F / F_BG');
  }

  const fps = parseFloat(document.getElementById('gifFps').value) || 5;
  const refFrame = parseInt(document.getElementById('gifRoiRefFrame').value, 10) || 1;
  return {
    tiff_paths: entries.map(e => e.path),
    slice_specs: entries.map(e => e.slices),
    rois: signalRois,
    bg_mode: bgMode,
    bg_roi: bgRoi || undefined,
    metric: document.getElementById('gifRoiMetric').value,
    plot_metric: plotMetric,
    fps,
    ref_frame: Math.max(1, refFrame),
  };
}

async function runGifRoiAnalysis() {
  let payload;
  try {
    payload = buildGifRoiAnalysisPayload();
  } catch(ex) {
    setStatus('status', ex.message, 'error');
    return;
  }

  btnBusy('btnAnalyzeGifRoi', true, 'Analyzing...');
  setStatus('status', 'Running ROI time analysis...', 'loading');

  try {
    const d = await runGifBackgroundJob('/api/fluorescence/gif_roi/analyze_job', payload, 'Running ROI time analysis');
    btnBusy('btnAnalyzeGifRoi', false, 'Analyze ROI Time');
    if (d.error) {
      setStatus('status', 'Error: ' + d.error, 'error');
      return;
    }

    _gifRoiCsvContent = d.csv || null;
    _gifRoiPlotB64 = d.img || null;
    _gifRoiDefaultOutputDir = d.default_output_dir || _gifRoiDefaultOutputDir;

    const refText = d.plot_metric === 'delta_f_over_f0' ? ` | ref frame ${d.ref_frame_applied}` : '';
    const warnText = (d.warnings || []).length
      ? `<div style="font-size:11px;color:#9a6a00;margin-top:6px">${(d.warnings || []).map(escHtml).join('<br>')}</div>`
      : '';
    const header =
      `ROI Time Analysis <span style="font-weight:400;color:var(--silver)">` +
      `${d.n_frames} frames | ${d.n_rois} ROI | ${d.metric} | ${d.plot_metric}${refText}</span>`;
    const body = `
      <img src="data:image/png;base64,${d.img}" style="max-width:100%;border-radius:4px"/>
      ${warnText}`;
    upsertGifResultCard('gifRoiTimeResultCard', header, body);
    document.getElementById('gifRoiExportSection').style.display = '';
    setStatus('status', `ROI time analysis complete: ${d.n_frames} frame(s)`, 'ok');
    toast('ROI time analysis complete');
  } catch(ex) {
    btnBusy('btnAnalyzeGifRoi', false, 'Analyze ROI Time');
    setStatus('status', 'Request failed: ' + ex.message, 'error');
  }
}

async function saveGifRoiOutputs(opts) {
  const options = Object.assign({saveCsv: true, savePlot: true}, opts || {});
  const hasRequestedOutput =
    (options.saveCsv && !!_gifRoiCsvContent) ||
    (options.savePlot && !!_gifRoiPlotB64);
  if (!hasRequestedOutput) {
    setStatus('status', 'No ROI time-analysis output to save', 'error');
    return;
  }

  const entries = gifAnalysisEntries();
  const prefix = buildGifRoiPrefix();
  setStatus('status', 'Saving ROI time-analysis output...', 'loading');

  try {
    const d = await runGifBackgroundJob('/api/fluorescence/gif_roi/export_job', {
      tiff_paths: entries.map(e => e.path),
      output_dir: _gifRoiDefaultOutputDir || undefined,
      prefix,
      save_csv: !!options.saveCsv,
      save_plot: !!options.savePlot,
      csv: _gifRoiCsvContent || '',
      plot_png_b64: _gifRoiPlotB64 || '',
    }, 'Saving ROI time-analysis output');
    if (d.error) throw new Error(d.error);
    _gifRoiDefaultOutputDir = d.output_dir || _gifRoiDefaultOutputDir;
    setStatus('status', 'Saved: ' + (d.saved_paths || []).join(' | '), 'ok');
    toast('ROI time-analysis output saved');
    recordRunHistory({
      view: 'fluorescence_gif',
      title: 'GIF ROI Time Export',
      status: 'ok',
      project_root: gifProjectRoot(),
      input_files: gifInputFileRecords(entries),
      outputs: dpAsPathRecords(d.saved_paths || [], 'gif_roi_time_output'),
      parameters: {
        settings: collectGifPrefs(),
        export_options: options,
        prefix,
      },
      metadata: {
        output_dir: d.output_dir || '',
      },
    });
  } catch(ex) {
    setStatus('status', 'Save failed: ' + ex.message, 'error');
    toast('Save failed: ' + ex.message, true);
  }
}

function exportGifRoiCSV() {
  saveGifRoiOutputs({saveCsv: true, savePlot: false});
}

function exportGifRoiPlotPNG() {
  saveGifRoiOutputs({saveCsv: false, savePlot: true});
}

function exportGifRoiAll() {
  saveGifRoiOutputs({saveCsv: true, savePlot: true});
}

function sanitizeGifKymoPrefix(s) {
  const raw = String(s || '').trim() || 'gif_roi_kymograph';
  return raw.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '') || 'gif_roi_kymograph';
}

function buildGifKymoPrefix() {
  return sanitizeGifKymoPrefix(document.getElementById('gifKymoPrefix')?.value) + '_' + gifRoiNowStamp();
}

function parseGifKymoPercentList(raw, maxItems = 8) {
  const seen = new Set();
  const vals = String(raw || '')
    .split(/[,;\s]+/)
    .map(x => parseFloat(x))
    .filter(x => Number.isFinite(x) && x > 0 && x <= 100)
    .filter(x => {
      const key = x.toFixed(6);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  return vals.slice(0, maxItems);
}

function buildGifKymoPayload() {
  const entries = gifAnalysisEntries();
  if (!entries.length) {
    throw new Error('Add at least one TIFF to the queue, or select a preview TIFF');
  }

  const polys = getClosedRoiPolygons();
  if (!polys.length) {
    throw new Error('Draw and close at least one polygon ROI first');
  }

  const roiLabel = document.getElementById('gifKymoRoiSelect')?.value || '';
  const roi = polys.find(p => p.label === roiLabel) || null;
  if (!roi) {
    throw new Error('Choose one signal ROI for kymography');
  }

  const bgMode = document.getElementById('gifBgMode').value;
  const bgLabel = gifBgLabel();
  const bgRoi = bgMode === 'roi' ? (polys.find(p => p.label === bgLabel) || null) : null;
  if (bgMode === 'roi' && !bgRoi) {
    throw new Error('Select a background ROI or change BG mode');
  }
  if (bgMode === 'roi' && bgRoi.label === roi.label) {
    throw new Error('Kymography ROI cannot also be the background ROI');
  }

  const valueMode = document.getElementById('gifKymoValueMode').value;
  if (valueMode === 'bg_subtracted' && bgMode === 'none') {
    throw new Error('Choose a BG mode before using BG Subtracted');
  }

  const bins = parseInt(document.getElementById('gifKymoBins').value, 10) || 80;
  const lowPct = parseFloat(document.getElementById('gifKymoLowPct').value);
  const highPct = parseFloat(document.getElementById('gifKymoHighPct').value);
  const smoothIntensity = parseFloat(document.getElementById('gifKymoSmoothIntensity').value);
  const smoothTime = parseFloat(document.getElementById('gifKymoSmoothTime').value);
  const thresholdsRaw = document.getElementById('gifKymoThresholds')?.value || '';
  const thresholdLines = thresholdsRaw
    .split(/[,;\s]+/)
    .map(x => parseFloat(x))
    .filter(x => Number.isFinite(x));
  const overlayPercentiles = parseGifKymoPercentList(document.getElementById('gifKymoPercentiles')?.value || '', 8);
  const overlayTopMeans = parseGifKymoPercentList(document.getElementById('gifKymoTopMeans')?.value || '', 6);
  if (!Number.isFinite(lowPct) || !Number.isFinite(highPct) || highPct <= lowPct) {
    throw new Error('Range percentiles must be valid and high > low');
  }
  if (!Number.isFinite(smoothIntensity) || !Number.isFinite(smoothTime) || smoothIntensity < 0 || smoothTime < 0) {
    throw new Error('Smooth values must be zero or positive');
  }

  return {
    tiff_paths: entries.map(e => e.path),
    slice_specs: entries.map(e => e.slices),
    roi,
    bg_mode: bgMode,
    bg_roi: bgRoi || undefined,
    value_mode: valueMode,
    fps: parseFloat(document.getElementById('gifFps').value) || 5,
    ref_frame: Math.max(1, parseInt(document.getElementById('gifKymoRefFrame').value, 10) || 1),
    ref_stat: document.getElementById('gifKymoRefStat').value,
    bins: Math.max(8, Math.min(240, bins)),
    range_low_pct: lowPct,
    range_high_pct: highPct,
    smooth_intensity_bins: Math.max(0, Math.min(8, smoothIntensity)),
    smooth_time_frames: Math.max(0, Math.min(8, smoothTime)),
    smooth_lines: !!document.getElementById('gifKymoSmoothLines')?.checked,
    overlay_peak: !!document.getElementById('gifKymoPeakLine')?.checked,
    overlay_mean: !!document.getElementById('gifKymoMeanLine')?.checked,
    threshold_lines: thresholdLines,
    overlay_percentiles: overlayPercentiles,
    overlay_top_means: overlayTopMeans,
  };
}

async function runGifKymograph() {
  let payload;
  try {
    updateGifKymoControls();
    payload = buildGifKymoPayload();
  } catch(ex) {
    setStatus('status', ex.message, 'error');
    return;
  }

  btnBusy('btnRunGifKymo', true, 'Running...');
  setStatus('status', 'Building ROI kymograph...', 'loading');

  try {
    const d = await runGifBackgroundJob('/api/fluorescence/gif_roi/kymograph_job', payload, 'Building ROI kymograph');
    btnBusy('btnRunGifKymo', false, 'Run Kymograph');
    if (d.error) {
      setStatus('status', 'Error: ' + d.error, 'error');
      return;
    }

    _gifKymoHeatmapCsv = d.heatmap_csv || null;
    _gifKymoSummaryCsv = d.summary_csv || null;
    _gifKymoPlotB64 = d.img || null;
    _gifKymoDefaultOutputDir = d.default_output_dir || _gifKymoDefaultOutputDir;

    const refText = d.value_mode === 'delta_f_over_f0'
      ? ` | ref frame ${d.ref_frame_applied} (${d.ref_stat}, F0=${Number(d.f0_value || 0).toPrecision(4)})`
      : '';
    const warnText = (d.warnings || []).length
      ? `<div style="font-size:11px;color:#9a6a00;margin-top:6px">${(d.warnings || []).map(escHtml).join('<br>')}</div>`
      : '';
    const header =
      `ROI Kymograph <span style="font-weight:400;color:var(--silver)">` +
      `${escHtml(d.roi_label)} | ${d.n_frames} frames | ${d.bins} bins | ${d.value_mode} | smooth I ${Number(d.smooth_intensity_bins || 0).toFixed(1)} / T ${Number(d.smooth_time_frames || 0).toFixed(1)}${refText}</span>`;
    const thresholdText = (d.threshold_lines || []).length
      ? ` · thresholds: ${(d.threshold_lines || []).map(x => Number(x).toPrecision(4)).join(', ')}`
      : '';
    const overlayBits = [];
    if (d.overlay_peak) overlayBits.push('peak bin');
    if (d.overlay_mean) overlayBits.push('mean');
    if ((d.overlay_percentiles || []).length) overlayBits.push(`p% ${(d.overlay_percentiles || []).map(x => Number(x).toPrecision(4)).join(', ')}`);
    if ((d.overlay_top_means || []).length) overlayBits.push(`top mean % ${(d.overlay_top_means || []).map(x => Number(x).toPrecision(4)).join(', ')}`);
    const overlayText = overlayBits.length ? ` · overlay: ${overlayBits.join(' / ')}` : ' · no overlay lines';
    const body = `
      <img src="data:image/png;base64,${d.img}" style="max-width:100%;border-radius:4px"/>
      <div style="font-size:11px;color:var(--silver);margin-top:6px">Intensity range: ${Number(d.range_min).toPrecision(4)} to ${Number(d.range_max).toPrecision(4)}${thresholdText}${overlayText} · heatmap CSV includes raw and smoothed bin percentages</div>
      ${warnText}`;
    upsertGifResultCard('gifKymoResultCard', header, body);
    document.getElementById('gifKymoExportSection').style.display = '';
    setStatus('status', `Kymograph complete: ${d.n_frames} frame(s), ${d.bins} bins`, 'ok');
    toast('ROI kymograph complete');
  } catch(ex) {
    btnBusy('btnRunGifKymo', false, 'Run Kymograph');
    setStatus('status', 'Request failed: ' + ex.message, 'error');
  }
}

async function saveGifKymoOutputs(opts) {
  const options = Object.assign({saveHeatmapCsv: true, saveSummaryCsv: true, savePlot: true}, opts || {});
  const hasRequestedOutput =
    (options.saveHeatmapCsv && !!_gifKymoHeatmapCsv) ||
    (options.saveSummaryCsv && !!_gifKymoSummaryCsv) ||
    (options.savePlot && !!_gifKymoPlotB64);
  if (!hasRequestedOutput) {
    setStatus('status', 'No kymograph output to save', 'error');
    return;
  }

  const entries = gifAnalysisEntries();
  const prefix = buildGifKymoPrefix();
  setStatus('status', 'Saving kymograph output...', 'loading');
  try {
    const d = await runGifBackgroundJob('/api/fluorescence/gif_roi/kymograph_export_job', {
      tiff_paths: entries.map(e => e.path),
      output_dir: _gifKymoDefaultOutputDir || undefined,
      prefix,
      save_heatmap_csv: !!options.saveHeatmapCsv,
      save_summary_csv: !!options.saveSummaryCsv,
      save_plot: !!options.savePlot,
      heatmap_csv: _gifKymoHeatmapCsv || '',
      summary_csv: _gifKymoSummaryCsv || '',
      plot_png_b64: _gifKymoPlotB64 || '',
    }, 'Saving kymograph output');
    if (d.error) throw new Error(d.error);
    _gifKymoDefaultOutputDir = d.output_dir || _gifKymoDefaultOutputDir;
    setStatus('status', 'Saved: ' + (d.saved_paths || []).join(' | '), 'ok');
    toast('Kymograph output saved');
    recordRunHistory({
      view: 'fluorescence_gif',
      title: 'GIF Kymograph Export',
      status: 'ok',
      project_root: gifProjectRoot(),
      input_files: gifInputFileRecords(entries),
      outputs: dpAsPathRecords(d.saved_paths || [], 'gif_kymograph_output'),
      parameters: {
        settings: collectGifPrefs(),
        export_options: options,
        prefix,
      },
      metadata: {
        output_dir: d.output_dir || '',
      },
    });
  } catch(ex) {
    setStatus('status', 'Save failed: ' + ex.message, 'error');
    toast('Save failed: ' + ex.message, true);
  }
}

function exportGifKymoPlotPNG() {
  saveGifKymoOutputs({saveHeatmapCsv: false, saveSummaryCsv: false, savePlot: true});
}

function exportGifKymoHeatmapCSV() {
  saveGifKymoOutputs({saveHeatmapCsv: true, saveSummaryCsv: false, savePlot: false});
}

function exportGifKymoSummaryCSV() {
  saveGifKymoOutputs({saveHeatmapCsv: false, saveSummaryCsv: true, savePlot: false});
}

function exportGifKymoAll() {
  saveGifKymoOutputs({saveHeatmapCsv: true, saveSummaryCsv: true, savePlot: true});
}

async function openFolder(path) {
  try { await api('/api/scripts/open_folder', { path }); }
  catch(e) { toast('Could not open folder', true); }
}

function setupRoiCanvasEvents() {
  const canvas = document.getElementById('gifRoiCanvas');
  if (!canvas) return;
  canvas.addEventListener('mousedown', e => {
    if (!_draftCropRect || !_imgW || !_imgH) return;
    const rect = canvas.getBoundingClientRect();
    const pt = canvasToNative(e.clientX - rect.left, e.clientY - rect.top);
    _cropRectDragStart = pt;
    _draftCropRect.x = pt.x;
    _draftCropRect.y = pt.y;
    _draftCropRect.width = 0;
    _draftCropRect.height = 0;
    drawPolygons();
  });
  canvas.addEventListener('mousemove', e => {
    if (!_draftCropRect || !_cropRectDragStart || !_imgW || !_imgH) return;
    const rect = canvas.getBoundingClientRect();
    const pt = canvasToNative(e.clientX - rect.left, e.clientY - rect.top);
    _draftCropRect.x = _cropRectDragStart.x;
    _draftCropRect.y = _cropRectDragStart.y;
    _draftCropRect.width = pt.x - _cropRectDragStart.x;
    _draftCropRect.height = pt.y - _cropRectDragStart.y;
    renderCropRectList();
    drawPolygons();
  });
  canvas.addEventListener('mouseup', () => {
    if (_draftCropRect && _cropRectDragStart) finishCropRect();
  });
  canvas.addEventListener('mouseleave', () => {
    if (_draftCropRect && _cropRectDragStart) finishCropRect();
  });
  canvas.addEventListener('click', e => {
    if (!_draftPolygon || !_imgW || !_imgH) return;
    const rect = canvas.getBoundingClientRect();
    const pt = canvasToNative(e.clientX - rect.left, e.clientY - rect.top);
    _draftPolygon.points.push(pt);
    renderPolygonList();
    drawPolygons();
  });
  canvas.addEventListener('contextmenu', e => {
    e.preventDefault();
    if (_draftPolygon && _draftPolygon.points.length >= 3) closePolygon();
  });
}

/* ---------- Init ---------- */
document.addEventListener('DOMContentLoaded', async () => {
  try {
    _gifPrefs = await loadViewPreferences('fluorescence_gif');
    applyGifPrefs(_gifPrefs.defaults || {});
    if (_gifPrefs.defaults) setStatus('gifPrefsStatus', 'Defaults loaded.', 'ok');
  } catch (e) {
    setStatus('gifPrefsStatus', 'Defaults not loaded.', 'warning');
  }
  toggleScaleMode();
  renderAvailableTiffList();
  renderTiffList();
  renderPolygonList();
  renderCropRectList();
  updateGifBgControls();
  updateGifKymoControls();
  updateGifCropControls();
  clearGeneratedGifPreview();
  setupRoiCanvasEvents();
});

document.addEventListener('dp:prefs-saved', event => {
  if (!event.detail || event.detail.view !== 'fluorescence_gif') return;
  _gifPrefs = event.detail.data || {};
  applyGifPrefs(_gifPrefs.defaults || {});
  setStatus('gifPrefsStatus', 'Defaults updated from Settings.', 'ok');
});
