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
let _lastExportPrefix = 'roi_analysis';
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
    name = await promptProfileName(name);
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
  if (!file || !name) return;
  const confirmed = await DP.dom.confirm({
    title: 'Delete file profile?',
    message: `Delete file profile "${name}"?`,
    confirmText: 'Delete',
    danger: true,
  });
  if (!confirmed) return;
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
  const name = await DP.dom.prompt({
    title: 'Context name',
    label: 'Context name',
    defaultValue: suggested,
    confirmText: 'Save',
  });
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

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'applyRoiSettings',
  'collectRoiFilePayload',
  'collectRoiSettings',
  'deleteSelectedRoiFileProfile',
  'escapeHtml',
  'loadRoiProfileForCurrent',
  'loadSelectedRoiFileProfile',
  'pairKey',
  'renderRoiContextOptions',
  'renderRoiFileProfileOptions',
  'resetRoiDefaults',
  'restoreRoiContext',
  'restoreRoiPayload',
  'roiContextKey',
  'roiPrimaryFile',
  'roiProjectRoot',
  'saveRoiContext',
  'saveRoiDefaults',
  'saveRoiFileProfile',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
