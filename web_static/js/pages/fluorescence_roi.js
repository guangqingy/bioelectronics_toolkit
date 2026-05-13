const ROI_COLORS = [
  '#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd',
  '#8c564b','#e377c2','#17becf','#bcbd22','#7f7f7f'
];

let _allPairs = [];
let _checkedPairIndices = new Set();
let _analysisPairs = [];
let _analysisActiveIndex = -1;
let _currentPairIndex = -1;
let _currentPair = null;
let _stack1Path = '';
let _stack2Path = '';

let _nFrames = 0;
let _imgW = 0;
let _imgH = 0;

let _csvContent = null;
let _lastPlotB64 = null;
let _lastPreviewB64 = null;
let _radialCsvContent = null;
let _lastRadialPlotB64 = null;
let _lastExportPrefix = 'roi_sequence_analysis';
let _lastRefSequence = '';
let _defaultOutputDir = '';

let _rois = [];
let _nextRoiIdx = 1;
let _roiPrefs = {};
let _roiFileProfileState = null;

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function pairKey(p) {
  return [p.base || '', p.stack1 || '', p.stack2 || ''].join('||');
}

function collectRoiSettings() {
  const val = id => document.getElementById(id)?.value ?? '';
  const checked = id => !!document.getElementById(id)?.checked;
  return {
    folderPath: val('folderPath'),
    stackSelect: val('stackSelect'),
    lutSelect: val('lutSelect'),
    previewScale: val('previewScale'),
    drawShape: val('drawShape'),
    ringWidthPx: val('ringWidthPx'),
    ringCount: val('ringCount'),
    ringWidthUm: val('ringWidthUm'),
    bgMode: val('bgMode'),
    bgRoiSelect: val('bgRoiSelect'),
    metric: val('metric'),
    plotMetric: val('plotMetric'),
    refSequence: val('refSequence'),
    outPrefix: val('outPrefix'),
    scaleBarUm: val('scaleBarUm'),
    gifFrameMs: val('gifFrameMs'),
    pixelSizeUm: val('pixelSizeUm'),
    scaleLabel: val('scaleLabel'),
    labelScale: val('labelScale'),
    showPreviewName: checked('showPreviewName'),
    showScaleBar: checked('showScaleBar'),
  };
}

function applyRoiSettings(settings) {
  if (!settings || typeof settings !== 'object') return;
  const setVal = (id, value) => {
    const el = document.getElementById(id);
    if (el && value !== undefined && value !== null) el.value = value;
  };
  const setChecked = (id, value) => {
    const el = document.getElementById(id);
    if (el && value !== undefined) el.checked = !!value;
  };
  Object.entries(settings).forEach(([key, value]) => {
    if (typeof value === 'boolean') setChecked(key, value);
    else setVal(key, value);
  });
  onBgModeChange();
  onDrawShapeChange();
}

function roiContextKey(name, settings) {
  return [settings.folderPath || '', name || 'manual'].join('|');
}

function renderRoiContextOptions(selectedKey) {
  const select = document.getElementById('roiContextSelect');
  if (!select) return;
  const contexts = Object.entries(_roiPrefs.contexts || {})
    .sort((a, b) => String(b[1].updated_at || '').localeCompare(String(a[1].updated_at || '')));
  select.innerHTML = ['<option value="">Saved contexts</option>'].concat(contexts.map(([key, ctx]) =>
    `<option value="${escapeHtml(key)}"${key === selectedKey ? ' selected' : ''}>${escapeHtml(ctx.name || key)}</option>`
  )).join('');
}

function roiProjectRoot() {
  return document.getElementById('folderPath').value.trim();
}

function roiPrimaryFile() {
  return (_currentPair && (_currentPair.stack1 || _currentPair.stack2)) || '';
}

function collectRoiFilePayload() {
  return {
    rois: _rois,
    analysisPairs: _analysisPairs,
    activePair: _currentPair,
  };
}

function restoreRoiPayload(payload, includeQueue) {
  if (!payload || typeof payload !== 'object') return;
  if (Array.isArray(payload.rois)) {
    _rois = payload.rois.map(r => ({...r}));
    if (!_rois.length) addRoi();
    else {
      renumberRois();
      renderRoiList();
      drawRects();
    }
  }
  if (includeQueue && Array.isArray(payload.analysisPairs)) {
    _analysisPairs = payload.analysisPairs.map(p => ({...p}));
    _analysisActiveIndex = _analysisPairs.length ? 0 : -1;
    renderAnalysisList();
  }
}

window.dpApplyRunManifest = manifest => {
  dpApplyRunManifestFallback(manifest);
  const params = manifest.parameters || {};
  if (params.settings) applyRoiSettings(params.settings);
  if (Array.isArray(params.rois)) {
    _rois = params.rois.map(r => ({...r, drawn: r.drawn !== false}));
    renumberRois();
    renderRoiList();
  }
  if (Array.isArray(manifest.input_files)) {
    const seen = new Map();
    manifest.input_files.forEach(rec => {
      const pair = rec.pair || rec.name || 'manifest';
      const item = seen.get(pair) || {base: pair, stack1: '', stack2: ''};
      if (rec.role === 'stack2') item.stack2 = rec.path;
      else item.stack1 = rec.path;
      seen.set(pair, item);
    });
    const pairs = Array.from(seen.values());
    if (pairs.length) {
      _analysisPairs = pairs;
      _currentPair = pairs[0];
      _stack1Path = _currentPair.stack1 || '';
      _stack2Path = _currentPair.stack2 || '';
      renderAnalysisList();
      renderRoiFileProfileOptions(_roiFileProfileState);
    }
  }
  drawRects();
  setStatus('status', 'ROI parameters loaded from run manifest', 'ok');
};

function renderRoiFileProfileOptions(data) {
  _roiFileProfileState = data || null;
  const select = document.getElementById('roiFileProfileSelect');
  if (!select) return;
  if (!roiPrimaryFile()) {
    select.innerHTML = '<option value="">No file selected</option>';
    return;
  }
  const selected = data?.selected_profile || data?.last_profile || '';
  select.innerHTML = fileProfileOptionsHtml(data?.profiles || {}, selected);
}

async function loadRoiProfileForCurrent(auto, includeQueue = false) {
  const file = roiPrimaryFile();
  if (!file) return null;
  try {
    const data = await loadFileProfile('fluorescence_roi', file, roiProjectRoot(), '');
    renderRoiFileProfileOptions(data);
    const shouldApply = !!data.profile && (!auto || document.getElementById('roiAutoLoadProfile')?.checked);
    if (shouldApply) {
      applyRoiSettings(data.profile.settings || {});
      restoreRoiPayload(data.profile.payload || {}, includeQueue);
      setStatus('roiFileProfileStatus', `Loaded file profile: ${data.selected_profile || data.last_profile || 'default'}`, data.stale ? 'warning' : 'ok');
    } else if (auto && document.getElementById('roiAutoLoadProfile')?.checked && !data.profile) {
      const created = await saveFileProfile('fluorescence_roi', file, roiProjectRoot(), 'default', collectRoiSettings(), collectRoiFilePayload());
      renderRoiFileProfileOptions(created);
      setStatus('roiFileProfileStatus', 'Created default file profile for this stack pair.', 'ok');
    } else {
      setStatus('roiFileProfileStatus', 'No saved profile for this stack pair yet.', '');
    }
    return data;
  } catch (e) {
    setStatus('roiFileProfileStatus', 'File profile not loaded: ' + e.message, 'warning');
    return null;
  }
}

async function loadSelectedRoiFileProfile(auto) {
  const file = roiPrimaryFile();
  if (!file) { setStatus('roiFileProfileStatus', 'Select a stack pair first.', 'error'); return; }
  const name = document.getElementById('roiFileProfileSelect').value;
  if (!name) { setStatus('roiFileProfileStatus', 'No profile selected.', 'error'); return; }
  try {
    const data = await loadFileProfile('fluorescence_roi', file, roiProjectRoot(), name);
    renderRoiFileProfileOptions(data);
    if (data.profile) {
      applyRoiSettings(data.profile.settings || {});
      restoreRoiPayload(data.profile.payload || {}, true);
      loadStack();
      setStatus('roiFileProfileStatus', `Loaded file profile: ${name}`, data.stale ? 'warning' : 'ok');
    }
  } catch (e) {
    setStatus('roiFileProfileStatus', 'Load failed: ' + e.message, 'error');
  }
}

async function saveRoiFileProfile(saveAs) {
  const file = roiPrimaryFile();
  if (!file) { setStatus('roiFileProfileStatus', 'Select a stack pair first.', 'error'); return; }
  let name = document.getElementById('roiFileProfileSelect').value || 'default';
  if (saveAs) {
    name = promptProfileName(name);
    if (!name) return;
  }
  try {
    const data = await saveFileProfile('fluorescence_roi', file, roiProjectRoot(), name, collectRoiSettings(), collectRoiFilePayload());
    renderRoiFileProfileOptions(data);
    setStatus('roiFileProfileStatus', `Saved file profile: ${name}`, 'ok');
    toast('ROI file profile saved');
  } catch (e) {
    setStatus('roiFileProfileStatus', 'Save failed: ' + e.message, 'error');
  }
}

async function deleteSelectedRoiFileProfile() {
  const file = roiPrimaryFile();
  const name = document.getElementById('roiFileProfileSelect').value;
  if (!file || !name || !confirm(`Delete file profile "${name}"?`)) return;
  try {
    const data = await deleteFileProfile('fluorescence_roi', file, roiProjectRoot(), name);
    renderRoiFileProfileOptions(data);
    setStatus('roiFileProfileStatus', `Deleted file profile: ${name}`, 'ok');
  } catch (e) {
    setStatus('roiFileProfileStatus', 'Delete failed: ' + e.message, 'error');
  }
}

async function saveRoiDefaults() {
  _roiPrefs.defaults = collectRoiSettings();
  _roiPrefs.updated_at = new Date().toISOString();
  await saveViewPreferences('fluorescence_roi', _roiPrefs);
  setStatus('roiPrefsStatus', 'Defaults saved.', 'ok');
  toast('ROI defaults saved');
}

async function resetRoiDefaults() {
  _roiPrefs = {contexts: _roiPrefs.contexts || {}};
  await saveViewPreferences('fluorescence_roi', _roiPrefs);
  setStatus('roiPrefsStatus', 'Defaults reset.', 'ok');
  toast('ROI defaults reset');
}

async function saveRoiContext() {
  const settings = collectRoiSettings();
  const suggested = (settings.folderPath ? settings.folderPath.split(/[\\/]/).filter(Boolean).pop() : 'ROI context') || 'ROI context';
  const name = window.prompt('Context name', suggested);
  if (!name) return;
  _roiPrefs.contexts = _roiPrefs.contexts || {};
  const key = roiContextKey(name, settings);
  _roiPrefs.contexts[key] = {
    name,
    settings,
    analysisPairs: _analysisPairs,
    rois: _rois,
    updated_at: new Date().toISOString(),
  };
  await saveViewPreferences('fluorescence_roi', _roiPrefs);
  renderRoiContextOptions(key);
  setStatus('roiPrefsStatus', 'Context saved.', 'ok');
  toast('ROI context saved');
}

async function restoreRoiContext() {
  const key = document.getElementById('roiContextSelect').value;
  if (!key || !_roiPrefs.contexts || !_roiPrefs.contexts[key]) return;
  const ctx = _roiPrefs.contexts[key];
  applyRoiSettings(ctx.settings || {});
  _analysisPairs = Array.isArray(ctx.analysisPairs) ? ctx.analysisPairs.slice() : [];
  _rois = Array.isArray(ctx.rois) ? JSON.parse(JSON.stringify(ctx.rois)) : [];
  renumberRois();
  renderAnalysisList();
  renderRoiList();
  if (_analysisPairs.length) selectAnalysisPair(0);
  setStatus('roiPrefsStatus', 'Context restored.', 'ok');
}

function updateAnalysisCount() {
  const n = _analysisPairs.length;
  document.getElementById('analysisCount').textContent = n + ' selected for analysis';
}

function renderPairList() {
  const el = document.getElementById('pairList');
  if (!_allPairs.length) {
    el.innerHTML = '<div class="file-list-empty">No TIFF stacks found</div>';
    return;
  }

  el.innerHTML = _allPairs.map((p, i) => {
    const checked = _checkedPairIndices.has(i) ? 'checked' : '';
    const active = i === _currentPairIndex ? 'active' : '';
    return `
      <div class="file-item ${active}" style="display:flex;align-items:center;gap:6px" onclick="selectPair(${i})">
        <input type="checkbox" ${checked} onclick="event.stopPropagation()" onchange="togglePairCheck(${i}, this.checked)">
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis">${escapeHtml(p.base || ('Pair ' + (i + 1)))}</span>
        <button class="btn-secondary" style="font-size:11px;padding:1px 7px;min-height:22px" onclick="event.stopPropagation();addPairByIndex(${i})">Add</button>
      </div>`;
  }).join('');
}

function renderAnalysisList() {
  const el = document.getElementById('analysisList');
  if (!_analysisPairs.length) {
    el.innerHTML = '<div class="file-list-empty">No files selected</div>';
    updateAnalysisCount();
    return;
  }

  el.innerHTML = _analysisPairs.map((p, i) => {
    const active = i === _analysisActiveIndex ? 'active' : '';
    return `
      <div class="file-item ${active}" style="display:flex;align-items:center;gap:6px" onclick="selectAnalysisPair(${i})">
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis">${escapeHtml(p.base || ('Pair ' + (i + 1)))}</span>
        <button class="btn-secondary" style="font-size:11px;padding:1px 7px;min-height:22px" onclick="event.stopPropagation();removeAnalysisPair(${i})">X</button>
      </div>`;
  }).join('');

  updateAnalysisCount();
}

function togglePairCheck(i, checked) {
  if (checked) _checkedPairIndices.add(i);
  else _checkedPairIndices.delete(i);
}

function clearPairChecks() {
  _checkedPairIndices.clear();
  renderPairList();
}

function ensurePreviewStackAvailable() {
  const sel = document.getElementById('stackSelect');
  if (!sel) return;
  const which = sel.value;
  if (which === 'stack2' && !_stack2Path && _stack1Path) {
    sel.value = 'stack1';
  } else if (which === 'stack1' && !_stack1Path && _stack2Path) {
    sel.value = 'stack2';
  }
}

function selectPair(i) {
  if (i < 0 || i >= _allPairs.length) return;
  _currentPairIndex = i;
  _currentPair = _allPairs[i];
  _stack1Path = _currentPair.stack1 || '';
  _stack2Path = _currentPair.stack2 || '';
  ensurePreviewStackAvailable();
  document.getElementById('stackSection').style.display = '';
  renderPairList();
  loadStack();
  loadRoiProfileForCurrent(true, false);
}

function selectAnalysisPair(i) {
  if (i < 0 || i >= _analysisPairs.length) return;
  _analysisActiveIndex = i;
  _currentPair = _analysisPairs[i];
  _stack1Path = _currentPair.stack1 || '';
  _stack2Path = _currentPair.stack2 || '';
  const k = pairKey(_currentPair);
  _currentPairIndex = _allPairs.findIndex(p => pairKey(p) === k);
  ensurePreviewStackAvailable();
  document.getElementById('stackSection').style.display = '';
  renderPairList();
  renderAnalysisList();
  loadStack();
  loadRoiProfileForCurrent(true, false);
}

function addPairByIndex(i) {
  if (i < 0 || i >= _allPairs.length) return;
  const p = _allPairs[i];
  const k = pairKey(p);
  if (_analysisPairs.some(x => pairKey(x) === k)) return;
  _analysisPairs.push({ base: p.base || '', stack1: p.stack1 || '', stack2: p.stack2 || '' });
  renderAnalysisList();
}

function addCheckedPairs() {
  if (!_checkedPairIndices.size) {
    setStatus('status', 'No checked files to add', 'error');
    return;
  }
  const before = _analysisPairs.length;
  Array.from(_checkedPairIndices).sort((a, b) => a - b).forEach(i => addPairByIndex(i));
  const added = _analysisPairs.length - before;
  setStatus('status', 'Added ' + added + ' file(s) to analysis list', 'ok');
}

function removeAnalysisPair(i) {
  if (i < 0 || i >= _analysisPairs.length) return;
  _analysisPairs.splice(i, 1);
  if (_analysisActiveIndex >= _analysisPairs.length) _analysisActiveIndex = _analysisPairs.length - 1;
  renderAnalysisList();
}

function removeActiveAnalysisPair() {
  if (_analysisActiveIndex < 0 || _analysisActiveIndex >= _analysisPairs.length) {
    setStatus('status', 'Select one file in the analysis list first', 'error');
    return;
  }
  removeAnalysisPair(_analysisActiveIndex);
}

function clearAnalysisList() {
  _analysisPairs = [];
  _analysisActiveIndex = -1;
  renderAnalysisList();
}

function scanFolder() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) {
    setStatus('status', 'Please enter a folder path', 'error');
    return;
  }

  setStatus('status', 'Scanning folder...', 'loading');
  api('/api/fluorescence/roi/browse', { folder })
    .then(d => {
      if (d.error) throw new Error(d.error);
      _allPairs = d.pairs || [];
      _checkedPairIndices.clear();
      _analysisPairs = [];
      _analysisActiveIndex = -1;
      _currentPairIndex = -1;
      _currentPair = null;
      _stack1Path = '';
      _stack2Path = '';
      renderPairList();
      renderAnalysisList();

      if (_allPairs.length) {
        selectPair(0);
      } else {
        document.getElementById('stackSection').style.display = 'none';
      }

      setStatus('status', _allPairs.length + ' stack pair(s) found', 'ok');
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function loadStack() {
  if (!_currentPair) return;
  ensurePreviewStackAvailable();
  const which = document.getElementById('stackSelect').value;
  let path = which === 'stack2' ? _stack2Path : _stack1Path;
  if (!path) {
    const fallback = which === 'stack2' ? _stack1Path : _stack2Path;
    if (fallback) {
      document.getElementById('stackSelect').value = which === 'stack2' ? 'stack1' : 'stack2';
      path = fallback;
    }
  }
  if (!path) {
    setStatus('status', 'Selected stack path is empty', 'error');
    return;
  }

  const lut = document.getElementById('lutSelect').value;
  const frame = parseInt(document.getElementById('frameSlider').value, 10) || 0;
  setStatus('status', 'Loading frame...', 'loading');

  api('/api/fluorescence/roi/load_stack', { stack_path: path, frame, lut })
    .then(d => {
      if (d.error) throw new Error(d.error);
      _nFrames = d.n_frames;
      _imgW = d.width;
      _imgH = d.height;

      const slider = document.getElementById('frameSlider');
      slider.max = Math.max(0, _nFrames - 1);
      slider.value = Math.min(frame, _nFrames - 1);
      document.getElementById('frameLabel').textContent = (parseInt(slider.value, 10) + 1) + ' / ' + _nFrames;

      const img = document.getElementById('tiffImg');
      img.onload = () => {
        applyPreviewScale(false);
        initCanvas();
      };
      img.src = 'data:image/png;base64,' + d.img;

      const stackTag = which === 'stack2' ? 'Stack 2' : 'Stack 1';
      const pairName = _currentPair.base || 'Selected pair';
      document.getElementById('stackInfo').textContent =
        pairName + ' | ' + stackTag + ' | ' + _nFrames + ' frames | ' + _imgW + 'x' + _imgH;
      setStatus('status', 'Ready', 'ok');
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function onFrameSlide() {
  const idx = parseInt(document.getElementById('frameSlider').value, 10) || 0;
  document.getElementById('frameLabel').textContent = (idx + 1) + ' / ' + _nFrames;
  loadStack();
}

function applyPreviewScale(redraw = true) {
  if (!_imgW) return;
  const scale = parseFloat(document.getElementById('previewScale').value) || 0.35;
  const wrap = document.getElementById('previewWrap');
  const targetW = Math.max(260, Math.round(_imgW * scale));
  wrap.style.width = Math.min(760, targetW) + 'px';
  if (redraw) requestAnimationFrame(initCanvas);
}

function renumberRois() {
  _rois.forEach((roi, i) => {
    roi.label = 'ROI ' + (i + 1);
    roi.color = ROI_COLORS[i % ROI_COLORS.length];
  });
  _nextRoiIdx = _rois.length + 1;
}

function addRoi() {
  renumberRois();
  const newLabel = 'ROI ' + _nextRoiIdx;
  const color = ROI_COLORS[(_nextRoiIdx - 1) % ROI_COLORS.length];
  _rois.push({
    label: newLabel,
    color,
    type: 'rect',
    x1: 0,
    y1: 0,
    x2: 0,
    y2: 0,
    cx: 0,
    cy: 0,
    radius: 0,
    ring_width_px: resolveRingWidthPx(),
    ring_width_um: getRingWidthUm(),
    ring_count: getRingCount(),
    drawn: false,
  });
  renumberRois();
  renderRoiList();
  const drawTarget = document.getElementById('drawTarget');
  if (drawTarget) drawTarget.value = newLabel;
  drawRects();
}

function removeRoi(i) {
  if (i < 0 || i >= _rois.length) return;
  const drawTarget = document.getElementById('drawTarget');
  const bgSel = document.getElementById('bgRoiSelect');
  const oldDrawIndex = _rois.findIndex(r => r.label === drawTarget.value);
  const oldBgIndex = _rois.findIndex(r => r.label === bgSel.value);
  _rois.splice(i, 1);
  renumberRois();
  renderRoiList();
  if (_rois.length) {
    const nextDrawIndex = Math.max(0, Math.min(i <= oldDrawIndex ? oldDrawIndex - 1 : oldDrawIndex, _rois.length - 1));
    const nextBgIndex = Math.max(0, Math.min(i <= oldBgIndex ? oldBgIndex - 1 : oldBgIndex, _rois.length - 1));
    drawTarget.value = _rois[nextDrawIndex].label;
    bgSel.value = _rois[nextBgIndex].label;
  }
  drawRects();
}

function getRingWidthPx() {
  const v = parseInt(document.getElementById('ringWidthPx')?.value || '10', 10);
  return Number.isFinite(v) && v > 0 ? v : 10;
}

function getRingCount() {
  const raw = document.getElementById('ringCount')?.value?.trim() || '';
  const v = raw ? parseInt(raw, 10) : NaN;
  return Number.isFinite(v) && v > 0 ? v : null;
}

function getPixelSizeUmInput() {
  const raw = document.getElementById('pixelSizeUm')?.value?.trim() || '';
  const v = raw ? parseFloat(raw) : NaN;
  return Number.isFinite(v) && v > 0 ? v : null;
}

function getRingWidthUm() {
  const raw = document.getElementById('ringWidthUm')?.value?.trim() || '';
  const v = raw ? parseFloat(raw) : NaN;
  return Number.isFinite(v) && v > 0 ? v : null;
}

function resolveRingWidthPx(radiusPx) {
  const count = getRingCount();
  if (count && Number.isFinite(radiusPx) && radiusPx > 0) {
    return Math.max(1, radiusPx / count);
  }
  const ringUm = getRingWidthUm();
  const pixelSizeUm = getPixelSizeUmInput();
  if (ringUm && pixelSizeUm) {
    return Math.max(1, Math.round(ringUm / pixelSizeUm));
  }
  return getRingWidthPx();
}

function currentDrawShape() {
  return document.getElementById('drawShape')?.value === 'concentric' ? 'concentric' : 'rect';
}

function isActiveBackgroundTarget(label) {
  return document.getElementById('bgMode')?.value === 'roi' &&
    !!label &&
    label === document.getElementById('bgRoiSelect')?.value;
}

function effectiveDrawShape(label) {
  return isActiveBackgroundTarget(label) ? 'rect' : currentDrawShape();
}

function roiTypeLabel(roi) {
  return roi && roi.type === 'concentric' ? 'Rings' : 'Rect';
}

function roiSummary(roi) {
  if (!roi || !roi.drawn) return '';
  if (roi.type === 'concentric') {
    const layers = roi.ring_count ? `, ${roi.ring_count} layers` : '';
    const ringUm = roi.ring_width_um ? `, ${roi.ring_width_um} um` : '';
    return `${roiTypeLabel(roi)} c=(${roi.cx},${roi.cy}) r=${roi.radius} w=${Number(roi.ring_width_px || 10).toFixed(2)}px${ringUm}${layers}`;
  }
  return `${roiTypeLabel(roi)} (${roi.x1},${roi.y1})-(${roi.x2},${roi.y2})`;
}

function roiToPayload(roi) {
  if (!roi) return null;
  if (roi.type === 'concentric') {
    const radius = Math.max(0, parseInt(roi.radius || 0, 10));
    const cx = parseInt(roi.cx || 0, 10);
    const cy = parseInt(roi.cy || 0, 10);
    const ringWidthUm = Number(roi.ring_width_um || 0);
    const ringCount = parseInt(roi.ring_count || 0, 10);
    return {
      label: roi.label,
      color: roi.color,
      type: 'concentric',
      cx,
      cy,
      radius,
      ring_width_px: Math.max(1, Number(roi.ring_width_px || 10)),
      ring_width_um: Number.isFinite(ringWidthUm) && ringWidthUm > 0 ? ringWidthUm : undefined,
      ring_count: Number.isFinite(ringCount) && ringCount > 0 ? ringCount : undefined,
      x1: cx - radius,
      y1: cy - radius,
      x2: cx + radius,
      y2: cy + radius,
    };
  }
  return {
    label: roi.label,
    color: roi.color,
    type: 'rect',
    x1: roi.x1,
    y1: roi.y1,
    x2: roi.x2,
    y2: roi.y2,
  };
}

function renderRoiList() {
  renumberRois();
  const el = document.getElementById('roiList');
  el.innerHTML = _rois.map((r, i) => `
    <div style="display:flex;align-items:center;gap:6px;background:var(--surface2);border-radius:4px;padding:4px 6px">
      <span style="width:12px;height:12px;border-radius:2px;background:${r.color};flex-shrink:0;display:inline-block"></span>
      <span style="flex:1;font-size:12px;color:var(--pewter)">${escapeHtml(r.label)}</span>
      ${r.drawn ? `<span style="font-size:10px;color:var(--silver)">${escapeHtml(roiSummary(r))}</span>` : ''}
      <button onclick="removeRoi(${i})" style="background:none;border:none;color:var(--silver);cursor:pointer;font-size:13px;padding:0 2px;line-height:1">X</button>
    </div>`).join('');

  const drawTarget = document.getElementById('drawTarget');
  const prevTarget = drawTarget.value;
  drawTarget.innerHTML = _rois.map(r => `<option value="${escapeHtml(r.label)}">${escapeHtml(r.label)}</option>`).join('');
  if (_rois.some(r => r.label === prevTarget)) drawTarget.value = prevTarget;

  const bgSel = document.getElementById('bgRoiSelect');
  const prevBg = bgSel.value;
  bgSel.innerHTML = _rois.map(r => `<option value="${escapeHtml(r.label)}">${escapeHtml(r.label)}</option>`).join('');
  if (_rois.some(r => r.label === prevBg)) bgSel.value = prevBg;
}

function onBgModeChange() {
  const mode = document.getElementById('bgMode').value;
  document.getElementById('bgRoiRow').style.display = mode === 'roi' ? 'flex' : 'none';
  updateCanvasHint();
  drawRects();
}

function onBgRoiChange() {
  updateCanvasHint();
  drawRects();
}

function onDrawTargetChange() {
  updateCanvasHint();
}

function updateCanvasHint() {
  const label = document.getElementById('drawTarget')?.value || '';
  const shape = effectiveDrawShape(label);
  const text = isActiveBackgroundTarget(label)
    ? 'Selected background ROI is drawn as a rectangle reference'
    : shape === 'concentric'
    ? 'Choose draw target, then drag from stimulation center to outer radius'
    : 'Choose draw target, then left-drag to draw ROI';
  document.getElementById('canvasHint').textContent = text;
}

function onDrawShapeChange() {
  const shape = currentDrawShape();
  document.getElementById('ringWidthRow').style.display = shape === 'concentric' ? 'flex' : 'none';
  document.getElementById('ringWidthUmRow').style.display = shape === 'concentric' ? 'flex' : 'none';
  updateCanvasHint();
  drawRects();
}

function onRingWidthChange() {
  const label = document.getElementById('drawTarget').value;
  const roi = _rois.find(r => r.label === label);
  if (roi && roi.drawn && roi.type === 'concentric') {
    roi.ring_count = getRingCount();
    roi.ring_width_px = resolveRingWidthPx(roi.radius);
    roi.ring_width_um = getRingWidthUm();
    renderRoiList();
    drawRects();
  }
}

let _drawing = false;
let _drawStartX = 0;
let _drawStartY = 0;
let _activeDrawLabel = null;

function initCanvas() {
  const img = document.getElementById('tiffImg');
  if (!img || !img.clientWidth || !img.clientHeight) return;
  const cvs = document.getElementById('roiCanvas');
  cvs.width = img.clientWidth;
  cvs.height = img.clientHeight;
  cvs.style.width = img.clientWidth + 'px';
	  cvs.style.height = img.clientHeight + 'px';
	  cvs.style.display = '';
	  updateCanvasHint();
	  document.getElementById('coordHint').textContent = 'ROI coordinates are saved in native image pixels';
	  drawRects();
	}

function imgToNative(cx, cy) {
  const img = document.getElementById('tiffImg');
  return {
    x: Math.round(cx / img.clientWidth * _imgW),
    y: Math.round(cy / img.clientHeight * _imgH),
  };
}

function nativeToImg(nx, ny) {
  const img = document.getElementById('tiffImg');
  return {
    x: nx / _imgW * img.clientWidth,
    y: ny / _imgH * img.clientHeight,
  };
}

(function setupCanvas() {
  const cvs = document.getElementById('roiCanvas');

  cvs.addEventListener('mousedown', e => {
    e.preventDefault();
    const label = document.getElementById('drawTarget').value;
    if (!label) return;
    _activeDrawLabel = label;
    const r = cvs.getBoundingClientRect();
    _drawStartX = e.clientX - r.left;
    _drawStartY = e.clientY - r.top;
    _drawing = true;
  });

	  cvs.addEventListener('mousemove', e => {
	    if (!_drawing || !_activeDrawLabel) return;
	    const r = cvs.getBoundingClientRect();
    drawRects({
      label: _activeDrawLabel,
      type: effectiveDrawShape(_activeDrawLabel),
      x1: _drawStartX,
      y1: _drawStartY,
      x2: e.clientX - r.left,
	      y2: e.clientY - r.top,
    });
  });

  cvs.addEventListener('mouseup', e => {
    if (!_drawing || !_activeDrawLabel) return;
    _drawing = false;
	    const r = cvs.getBoundingClientRect();
	    const ex = e.clientX - r.left;
	    const ey = e.clientY - r.top;
    const roi = _rois.find(r0 => r0.label === _activeDrawLabel);
    if (roi) {
      const shape = effectiveDrawShape(_activeDrawLabel);
      if (shape === 'concentric') {
	        const c = imgToNative(_drawStartX, _drawStartY);
	        const edge = imgToNative(ex, ey);
	        c.x = Math.max(0, Math.min(_imgW - 1, c.x));
	        c.y = Math.max(0, Math.min(_imgH - 1, c.y));
	        const dx = edge.x - c.x;
	        const dy = edge.y - c.y;
	        const radius = Math.round(Math.sqrt(dx * dx + dy * dy));
	        if (radius > 2) {
	          roi.type = 'concentric';
          roi.cx = c.x;
          roi.cy = c.y;
          roi.radius = radius;
          roi.ring_count = getRingCount();
          roi.ring_width_px = resolveRingWidthPx(radius);
          roi.ring_width_um = getRingWidthUm();
	          roi.x1 = c.x - radius;
	          roi.y1 = c.y - radius;
	          roi.x2 = c.x + radius;
	          roi.y2 = c.y + radius;
	          roi.drawn = true;
	          renderRoiList();
	        }
	      } else {
	        const n1 = imgToNative(Math.min(_drawStartX, ex), Math.min(_drawStartY, ey));
	        const n2 = imgToNative(Math.max(_drawStartX, ex), Math.max(_drawStartY, ey));
	        if (n2.x - n1.x > 2 && n2.y - n1.y > 2) {
	          roi.type = 'rect';
	          roi.x1 = n1.x;
	          roi.y1 = n1.y;
	          roi.x2 = n2.x;
	          roi.y2 = n2.y;
	          roi.drawn = true;
	          renderRoiList();
	        }
	      }
	    }

    drawRects();
    _activeDrawLabel = null;
  });

  cvs.addEventListener('mouseleave', () => {
    if (_drawing) {
      _drawing = false;
      drawRects();
      _activeDrawLabel = null;
    }
  });

  cvs.addEventListener('contextmenu', e => e.preventDefault());
})();

function drawRects(liveRect) {
  const cvs = document.getElementById('roiCanvas');
  if (!cvs.width) return;
  const ctx = cvs.getContext('2d');
  ctx.clearRect(0, 0, cvs.width, cvs.height);

  const bgMode = document.getElementById('bgMode').value;
  const bgLabel = document.getElementById('bgRoiSelect').value;

  for (const roi of _rois) {
    if (!roi.drawn) continue;
    const isBg = bgMode === 'roi' && roi.label === bgLabel;

    ctx.strokeStyle = roi.color;
    ctx.lineWidth = 2;
    ctx.setLineDash(isBg ? [5, 3] : []);
    if (roi.type === 'concentric') {
      drawConcentricOnCanvas(ctx, roi, isBg);
    } else {
      const p1 = nativeToImg(roi.x1, roi.y1);
      const p2 = nativeToImg(roi.x2, roi.y2);
      ctx.strokeRect(p1.x, p1.y, p2.x - p1.x, p2.y - p1.y);
      ctx.fillStyle = roi.color;
      ctx.font = 'bold 11px sans-serif';
      ctx.fillText(roi.label + (isBg ? ' (BG)' : ''), p1.x + 4, p1.y + 13);
    }
    ctx.setLineDash([]);
  }

  if (liveRect) {
    const roi = _rois.find(r => r.label === liveRect.label);
    if (roi) {
      ctx.strokeStyle = roi.color;
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 2]);
      if (liveRect.type === 'concentric') {
        const radius = Math.hypot(liveRect.x2 - liveRect.x1, liveRect.y2 - liveRect.y1);
        drawConcentricOnCanvas(ctx, {
          label: liveRect.label,
          color: roi.color,
          type: 'concentric',
          cx: liveRect.x1 / cvs.width * _imgW,
          cy: liveRect.y1 / cvs.height * _imgH,
          radius: radius / cvs.width * _imgW,
          ring_width_px: resolveRingWidthPx(radius / cvs.width * _imgW),
          ring_width_um: getRingWidthUm(),
          ring_count: getRingCount(),
        }, false);
      } else {
        const lx1 = Math.min(liveRect.x1, liveRect.x2);
        const ly1 = Math.min(liveRect.y1, liveRect.y2);
        const lx2 = Math.max(liveRect.x1, liveRect.x2);
        const ly2 = Math.max(liveRect.y1, liveRect.y2);
        ctx.strokeRect(lx1, ly1, lx2 - lx1, ly2 - ly1);
      }
      ctx.setLineDash([]);
    }
  }

  if ((bgMode === 'corner_br' || bgMode === 'corner_tl') && _imgW > 0 && _imgH > 0) {
    const sz = Math.min(40, _imgH / 4, _imgW / 4);
    let nx1 = 0;
    let ny1 = 0;
    if (bgMode === 'corner_br') {
      nx1 = _imgW - sz;
      ny1 = _imgH - sz;
    }
    const cp1 = nativeToImg(nx1, ny1);
    const cp2 = nativeToImg(nx1 + sz, ny1 + sz);
    ctx.strokeStyle = '#22c55e';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 2]);
    ctx.strokeRect(cp1.x, cp1.y, cp2.x - cp1.x, cp2.y - cp1.y);
    ctx.fillStyle = '#22c55e';
    ctx.font = 'bold 10px sans-serif';
    ctx.fillText('BG', cp1.x + 3, cp1.y + 11);
    ctx.setLineDash([]);
  }
}

function drawConcentricOnCanvas(ctx, roi, isBg) {
  const center = nativeToImg(roi.cx || 0, roi.cy || 0);
  const img = document.getElementById('tiffImg');
  const scaleX = img.clientWidth / Math.max(1, _imgW);
  const radius = Math.max(0, Number(roi.radius || 0)) * scaleX;
  const count = parseInt(roi.ring_count || 0, 10);
  const ringStep = count > 0 ? radius / count : Math.max(1, Number(roi.ring_width_px || resolveRingWidthPx())) * scaleX;
  const label = String(roi.label || 'ROI') + (isBg ? ' (BG)' : '');
  if (radius <= 0) return;

  ctx.strokeStyle = roi.color;
  ctx.fillStyle = roi.color;
  ctx.font = 'bold 11px sans-serif';
  for (let r = ringStep; r < radius; r += ringStep) {
    ctx.globalAlpha = 0.6;
    ctx.beginPath();
    ctx.arc(center.x, center.y, r, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.globalAlpha = 1.0;
  ctx.beginPath();
  ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
  ctx.stroke();
  const cross = Math.max(4, Math.min(10, radius * 0.08));
  ctx.beginPath();
  ctx.moveTo(center.x - cross, center.y);
  ctx.lineTo(center.x + cross, center.y);
  ctx.moveTo(center.x, center.y - cross);
  ctx.lineTo(center.x, center.y + cross);
  ctx.stroke();
  ctx.fillText(label, center.x + 5, Math.max(12, center.y - radius - 4));
}

function nowStamp() {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, '_');
}

function sanitizePrefix(s) {
  const raw = String(s || '').trim();
  const use = raw || 'roi_sequence_analysis';
  return use.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '') || 'roi_sequence_analysis';
}

function buildExportPrefix() {
  const base = sanitizePrefix(document.getElementById('outPrefix').value);
  return base + '_' + nowStamp();
}

function activeRecordsForExport() {
  if (_analysisPairs && _analysisPairs.length) return _analysisPairs.slice();
  if (_currentPair) return [_currentPair];
  return [];
}

function roiInputFileRecords(records) {
  const out = [];
  (records || []).forEach(rec => {
    if (rec.stack1) out.push({path: rec.stack1, role: 'stack1', pair: rec.base || ''});
    if (rec.stack2) out.push({path: rec.stack2, role: 'stack2', pair: rec.base || ''});
  });
  return out;
}

function saveSequenceOutputs(opts) {
  const options = Object.assign({
    saveCsv: true,
    savePlot: true,
    savePreview: true,
    saveRadialCsv: true,
    saveRadialPlot: true,
  }, opts || {});
  const records = activeRecordsForExport();
  if (!records.length) {
    setStatus('status', 'No records available for export path resolution', 'error');
    return;
  }

	  const hasContent =
	    (options.saveCsv && !!_csvContent) ||
	    (options.savePlot && !!_lastPlotB64) ||
	    (options.savePreview && !!_lastPreviewB64) ||
	    (options.saveRadialCsv && !!_radialCsvContent) ||
	    (options.saveRadialPlot && !!_lastRadialPlotB64);
  if (!hasContent) {
    setStatus('status', 'No export content available', 'error');
    return;
  }

  const prefix = buildExportPrefix();
  _lastExportPrefix = prefix;
  setStatus('status', 'Exporting outputs to disk...', 'loading');

  dpRunJobEndpoint('/api/fluorescence/roi/export_sequence_job', {
    records,
    output_dir: _defaultOutputDir || undefined,
    prefix,
    save_csv: !!options.saveCsv,
    save_plot: !!options.savePlot,
	    save_preview: !!options.savePreview,
	    save_radial_csv: !!options.saveRadialCsv,
	    save_radial_plot: !!options.saveRadialPlot,
	    csv: _csvContent || '',
	    plot_png_b64: _lastPlotB64 || '',
	    roi_preview_png_b64: _lastPreviewB64 || '',
	    radial_csv: _radialCsvContent || '',
	    radial_plot_png_b64: _lastRadialPlotB64 || ''
	  }, {
	    interval_ms: 1000,
	    on_update: job => {
	      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
	      const msg = job.message ? ` · ${job.message}` : '';
	      setStatus('status', `Exporting outputs to disk${pct}${msg}`, 'loading');
	    },
	  }).then(d => {
    if (d.error) throw new Error(d.error);
    _defaultOutputDir = d.output_dir || _defaultOutputDir;
    const saved = (d.saved_paths || []).join(' | ');
    setStatus('status', 'Exported: ' + (saved || 'ok'), 'ok');
    toast('Exported to disk');
    recordRunHistory({
      view: 'fluorescence_roi',
      title: 'ROI Sequence Export',
      status: 'ok',
      project_root: roiProjectRoot(),
      input_files: roiInputFileRecords(records),
      outputs: dpAsPathRecords(d.saved_paths || [], 'roi_sequence_output'),
      parameters: {
        settings: collectRoiSettings(),
        export_options: options,
        rois: _rois.filter(r => r.drawn).map(roiToPayload).filter(Boolean),
        prefix,
      },
      metadata: {
        output_dir: d.output_dir || '',
        records: records.length,
      },
    });
  }).catch(e => {
    setStatus('status', 'Error: ' + e.message, 'error');
    toast('Export failed: ' + e.message, true);
  });
}

function upsertResultCard(cardId, headerHtml, bodyHtml) {
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

function runAnalysis() {
  const bgMode = document.getElementById('bgMode').value;
  const bgLabel = document.getElementById('bgRoiSelect').value;
  const metric = document.getElementById('metric').value;
  const plotMetric = document.getElementById('plotMetric').value;
  const refSequence = document.getElementById('refSequence').value.trim();
  const scaleBarUm = parseFloat(document.getElementById('scaleBarUm').value) || 0;
  const pixelSizeRaw = document.getElementById('pixelSizeUm').value.trim();
  const pixelSizeUm = pixelSizeRaw ? parseFloat(pixelSizeRaw) : undefined;
  const scaleBarLabel = document.getElementById('scaleLabel').value.trim();
  const labelScale = parseFloat(document.getElementById('labelScale').value) || 1.0;
  const showPreviewName = document.getElementById('showPreviewName').checked;
  const showScaleBar = document.getElementById('showScaleBar').checked;

  if (pixelSizeRaw && (!Number.isFinite(pixelSizeUm) || pixelSizeUm <= 0)) {
    setStatus('status', 'Pixel size must be > 0 or left blank for auto', 'error');
    return;
  }

  let signalRois = _rois.filter(r => r.drawn);
	  let bgRoiPayload = null;
	  if (bgMode === 'roi' && bgLabel) {
	    const bgRoi = signalRois.find(r => r.label === bgLabel);
	    if (bgRoi) {
	      bgRoiPayload = roiToPayload(bgRoi);
	      signalRois = signalRois.filter(r => r.label !== bgLabel);
	    }
	  }

  if (!signalRois.length) {
    setStatus('status', 'Draw at least one signal ROI before analysis', 'error');
    return;
  }

  let records = _analysisPairs.slice();
  if (!records.length && _currentPair) records = [_currentPair];
  if (!records.length) {
    setStatus('status', 'No file selected: add files to analysis list or select one pair', 'error');
    return;
  }

	  const rois = signalRois.map(roiToPayload).filter(Boolean);

  btnBusy('btnAnalyze', true, 'Analyzing...');
  setStatus('status', 'Running sequence analysis on ' + records.length + ' file(s)...', 'loading');

  const previewStack = document.getElementById('stackSelect').value;
  const previewPath = previewStack === 'stack2' ? _stack2Path : _stack1Path;

  api('/api/fluorescence/roi/analyze_sequence', {
    records,
    rois,
    metric,
    plot_metric: plotMetric,
    bg_mode: bgMode,
    bg_roi: bgRoiPayload || undefined,
    ref_sequence: refSequence || undefined,
    preview_stack: previewStack,
    preview_path: previewPath || undefined,
    scale_bar_um: scaleBarUm,
    pixel_size_um: pixelSizeUm,
    scale_bar_label: scaleBarLabel || undefined,
    label_scale: labelScale,
    show_preview_name: showPreviewName,
    show_scale_bar: showScaleBar,
    img_width: _imgW,
    img_height: _imgH,
  }).then(d => {
    btnBusy('btnAnalyze', false, 'Run Analysis');
    if (d.error) {
      setStatus('status', 'Error: ' + d.error, 'error');
      return;
    }

	    _csvContent = d.csv || null;
	    _lastPlotB64 = d.img || null;
	    _lastPreviewB64 = d.roi_preview_img || null;
	    _radialCsvContent = d.radial_csv || null;
	    _lastRadialPlotB64 = d.radial_img || null;
	    _lastRefSequence = d.ref_sequence_applied || '';
	    _defaultOutputDir = d.default_output_dir || _defaultOutputDir;

    const refText = d.ref_sequence_applied ? ` | ref=${d.ref_sequence_applied}` : '';
    const header =
      `ROI Sequence Result <span style="font-weight:400;color:var(--silver)">` +
      `${d.n_records} files | ${d.n_rois} ROI | ${d.metric} | ${d.plot_metric}${refText}</span>`;
    const body = `<img src="data:image/png;base64,${d.img}" style="max-width:100%;border-radius:4px"/>`;
    upsertResultCard('roiSequenceResultCard', header, body);

	    if (_lastPreviewB64) {
	      const pxInfo = d.roi_preview_pixel_size_um ? ` | pixel=${Number(d.roi_preview_pixel_size_um).toFixed(5)} um/px` : '';
	      const prevHeader =
	        `ROI Reference Preview <span style="font-weight:400;color:var(--silver)">` +
	        `${d.roi_preview_path ? escapeHtml(d.roi_preview_path.split('/').pop()) : 'selected preview'}${pxInfo}</span>`;
	      const prevBody = `<img src="data:image/png;base64,${_lastPreviewB64}" style="max-width:100%;border-radius:4px"/>`;
	      upsertResultCard('roiPreviewResultCard', prevHeader, prevBody);
	    }

	    if (_lastRadialPlotB64) {
	      const radialHeader =
	        `Concentric ROI Ring Sequence <span style="font-weight:400;color:var(--silver)">` +
	        `${d.n_radial_rows || 0} ring measurements</span>`;
	      const radialBody = `<img src="data:image/png;base64,${_lastRadialPlotB64}" style="max-width:100%;border-radius:4px"/>`;
	      upsertResultCard('roiRadialResultCard', radialHeader, radialBody);
	    } else {
	      const oldRadial = document.getElementById('roiRadialResultCard');
	      if (oldRadial) oldRadial.remove();
	    }

    document.getElementById('exportSection').style.display = '';
    setStatus('status', 'Analysis complete: ' + d.n_records + ' files processed', 'ok');
    toast('ROI sequence analysis complete');
  }).catch(e => {
    btnBusy('btnAnalyze', false, 'Run Analysis');
    const msg = String((e && e.message) || e || 'Unknown error');
    if (/failed to fetch|networkerror|network error/i.test(msg)) {
      setStatus('status', 'Error: Backend service is not running (start web_app.py first)', 'error');
      return;
    }
    setStatus('status', 'Error: ' + msg, 'error');
  });
}

function exportCSV() {
  if (!_csvContent) {
    setStatus('status', 'No analysis CSV to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: true, savePlot: false, savePreview: false, saveRadialCsv: false, saveRadialPlot: false });
}

function exportPlotPNG() {
  if (!_lastPlotB64) {
    setStatus('status', 'No analysis plot to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: false, savePlot: true, savePreview: false, saveRadialCsv: false, saveRadialPlot: false });
}

function exportPreviewPNG() {
  if (!_lastPreviewB64) {
    setStatus('status', 'No ROI preview to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: false, savePlot: false, savePreview: true, saveRadialCsv: false, saveRadialPlot: false });
}

function exportRadialCSV() {
  if (!_radialCsvContent) {
    setStatus('status', 'No radial CSV to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: false, savePlot: false, savePreview: false, saveRadialCsv: true, saveRadialPlot: false });
}

function exportRadialPlotPNG() {
  if (!_lastRadialPlotB64) {
    setStatus('status', 'No radial plot to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: false, savePlot: false, savePreview: false, saveRadialCsv: false, saveRadialPlot: true });
}

function exportGif() {
  const records = activeRecordsForExport();
  if (!records.length) {
    setStatus('status', 'No records selected for GIF export', 'error');
    return;
  }

  const frameMs = parseInt(document.getElementById('gifFrameMs').value, 10) || 2000;
  const scaleBarUm = parseFloat(document.getElementById('scaleBarUm').value) || 0;
  const pixelSizeRaw = document.getElementById('pixelSizeUm').value.trim();
  const pixelSizeUm = pixelSizeRaw ? parseFloat(pixelSizeRaw) : undefined;
  const scaleBarLabel = document.getElementById('scaleLabel').value.trim();
  const labelScale = parseFloat(document.getElementById('labelScale').value) || 1.0;
  const showPreviewName = document.getElementById('showPreviewName').checked;
  const showScaleBar = document.getElementById('showScaleBar').checked;

  if (frameMs < 20) {
    setStatus('status', 'GIF frame ms must be >= 20', 'error');
    return;
  }
  if (pixelSizeRaw && (!Number.isFinite(pixelSizeUm) || pixelSizeUm <= 0)) {
    setStatus('status', 'Pixel size must be > 0 or left blank for auto', 'error');
    return;
  }

	  const rois = _rois.filter(r => r.drawn).map(roiToPayload).filter(Boolean);

  const prefix = buildExportPrefix();
  _lastExportPrefix = prefix;
  setStatus('status', 'Exporting GIF to disk...', 'loading');
  dpRunJobEndpoint('/api/fluorescence/roi/export_sequence_gif_job', {
    records,
    rois,
    preview_stack: document.getElementById('stackSelect').value,
    output_dir: _defaultOutputDir || undefined,
    prefix,
    frame_ms: frameMs,
    scale_bar_um: scaleBarUm,
    pixel_size_um: pixelSizeUm,
    scale_bar_label: scaleBarLabel || undefined,
    label_scale: labelScale,
    show_preview_name: showPreviewName,
    show_scale_bar: showScaleBar,
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Exporting GIF to disk${pct}${msg}`, 'loading');
    },
  }).then(d => {
    if (d.error) throw new Error(d.error);
    if (d.gif_path) {
      const pos = d.gif_path.lastIndexOf('/');
      if (pos > 0) _defaultOutputDir = d.gif_path.slice(0, pos);
    }
    setStatus('status', 'Exported GIF: ' + (d.gif_path || 'ok'), 'ok');
    toast('GIF exported to disk');
    recordRunHistory({
      view: 'fluorescence_roi',
      title: 'ROI Sequence GIF',
      status: 'ok',
      project_root: roiProjectRoot(),
      input_files: roiInputFileRecords(records),
      outputs: d.gif_path ? [{path: d.gif_path, type: 'roi_sequence_gif'}] : [],
      parameters: {
        settings: collectRoiSettings(),
        frame_ms: frameMs,
        preview_stack: document.getElementById('stackSelect').value,
        rois,
        prefix,
      },
      metadata: {
        n_frames: d.n_frames || records.length,
        pixel_size_um: d.pixel_size_um || null,
      },
    });
  }).catch(e => {
    setStatus('status', 'Error: ' + e.message, 'error');
    toast('GIF export failed: ' + e.message, true);
  });
}

function exportAllOutputs() {
  if (!_csvContent && !_lastPlotB64 && !_lastPreviewB64 && !_radialCsvContent && !_lastRadialPlotB64) {
    setStatus('status', 'No analysis outputs to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: true, savePlot: true, savePreview: true, saveRadialCsv: true, saveRadialPlot: true });
}

window.addEventListener('load', async () => {
  setStatus('status', 'Ready', 'ok');
  try {
    _roiPrefs = await loadViewPreferences('fluorescence_roi');
    if (!_roiPrefs || typeof _roiPrefs !== 'object') _roiPrefs = {};
    _roiPrefs.contexts = _roiPrefs.contexts || {};
    applyRoiSettings(_roiPrefs.defaults || {});
    renderRoiContextOptions();
    if (_roiPrefs.defaults) setStatus('roiPrefsStatus', 'Defaults loaded.', 'ok');
  } catch (e) {
    _roiPrefs = {contexts: {}};
    setStatus('roiPrefsStatus', 'Defaults not loaded.', 'warning');
  }
  addRoi();
  renderPairList();
  renderAnalysisList();
  onBgModeChange();
  onDrawShapeChange();
  if (new URLSearchParams(window.location.search).get('demo') === 'fluorescence') {
    document.getElementById('folderPath').value = DEFAULT_EXAMPLES_DIR || 'examples';
    scanFolder();
  }
	});

document.addEventListener('dp:prefs-saved', event => {
  if (!event.detail || event.detail.view !== 'fluorescence_roi') return;
  _roiPrefs = event.detail.data || {};
  _roiPrefs.contexts = _roiPrefs.contexts || {};
  applyRoiSettings(_roiPrefs.defaults || {});
  renderRoiContextOptions();
  setStatus('roiPrefsStatus', 'Defaults updated from Settings.', 'ok');
});

window.addEventListener('resize', () => {
  const img = document.getElementById('tiffImg');
  if (img && img.src) requestAnimationFrame(initCanvas);
});
