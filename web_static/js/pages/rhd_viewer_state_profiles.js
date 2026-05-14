let _currentFile = null;
let _currentChannel = null;
let _metadata = {};
let _rhdFiles = [];
let _queueFiles = [];
let _queueSel = -1;
let _fileLoadSeq = 0;
let _plotSeq = 0;
let _activeProfileLoadSeq = 0;

window.dpCurrentFilePath = () => _currentFile || '';
window.dpCurrentProjectRoot = () => document.getElementById('folderPath').value.trim();
window.dpCollectFileProfilePayload = () => ({
  currentChannel: _currentChannel,
  queueFiles: _queueFiles.slice(),
  queueSel: _queueSel,
  previewMergePair: document.getElementById('previewMergePair')?.checked || false,
  previewDownsample: document.getElementById('previewDownsample')?.value || 'auto',
  filterType: document.getElementById('filterType')?.value || 'none',
  filterLowHz: document.getElementById('filterLowHz')?.value || '',
  filterHighHz: document.getElementById('filterHighHz')?.value || '',
  filterNotchHz: document.getElementById('filterNotchHz')?.value || '',
  filterOrder: document.getElementById('filterOrder')?.value || '',
  filterNotchQ: document.getElementById('filterNotchQ')?.value || '',
  processType: document.getElementById('processType')?.value || 'envelope',
  smoothMs: document.getElementById('smoothMs')?.value || '',
  envelopeSmoothMs: document.getElementById('envelopeSmoothMs')?.value || '',
  smoothMethod: document.getElementById('smoothMethod')?.value || 'moving',
  sgPoly: document.getElementById('sgPoly')?.value || '',
  fitDegree: document.getElementById('fitDegree')?.value || '',
  fitShowRaw: document.getElementById('fitShowRaw')?.checked || false,
  fftWindow: document.getElementById('fftWindow')?.value || 'hann',
  fftMaxHz: document.getElementById('fftMaxHz')?.value || '',
  fftLog: document.getElementById('fftLog')?.checked || false,
  stftMs: document.getElementById('stftMs')?.value || '',
  stftOverlapPct: document.getElementById('stftOverlapPct')?.value || '',
  stftMaxHz: document.getElementById('stftMaxHz')?.value || '',
  stftCmap: document.getElementById('stftCmap')?.value || 'viridis',
  stftLog: document.getElementById('stftLog')?.checked || false,
  figWidthIn: document.getElementById('figWidthIn')?.value || '',
  figHeightIn: document.getElementById('figHeightIn')?.value || '',
  figDpi: document.getElementById('figDpi')?.value || '',
  traceLineWidth: document.getElementById('traceLineWidth')?.value || '',
  traceColor: document.getElementById('traceColor')?.value || '#3E6AE1',
  showGrid: document.getElementById('showGrid')?.checked || false,
  showTitle: document.getElementById('showTitle')?.checked || false,
});
window.dpApplyFileProfilePayload = payload => {
  if (!payload || typeof payload !== 'object') return;
  if (_activeProfileLoadSeq && _activeProfileLoadSeq !== _fileLoadSeq) return;
  if (Array.isArray(payload.queueFiles)) {
    _queueFiles = payload.queueFiles.slice();
    _queueSel = Number.isInteger(payload.queueSel) ? payload.queueSel : (_queueFiles.length ? 0 : -1);
    if (_queueSel >= _queueFiles.length) _queueSel = _queueFiles.length - 1;
    queueRender();
  }
  if (typeof payload.previewMergePair === 'boolean') {
    const el = document.getElementById('previewMergePair');
    if (el) el.checked = payload.previewMergePair;
  }
  if (payload.previewDownsample) {
    const el = document.getElementById('previewDownsample');
    if (el) el.value = payload.previewDownsample;
  }
  [
    ['filterType', 'filterType'],
    ['filterLowHz', 'filterLowHz'],
    ['filterHighHz', 'filterHighHz'],
    ['filterNotchHz', 'filterNotchHz'],
    ['filterOrder', 'filterOrder'],
    ['filterNotchQ', 'filterNotchQ'],
    ['processType', 'processType'],
    ['smoothMs', 'smoothMs'],
    ['envelopeSmoothMs', 'envelopeSmoothMs'],
    ['smoothMethod', 'smoothMethod'],
    ['sgPoly', 'sgPoly'],
    ['fitDegree', 'fitDegree'],
    ['fftWindow', 'fftWindow'],
    ['fftMaxHz', 'fftMaxHz'],
    ['stftMs', 'stftMs'],
    ['stftOverlapPct', 'stftOverlapPct'],
    ['stftMaxHz', 'stftMaxHz'],
    ['stftCmap', 'stftCmap'],
    ['figWidthIn', 'figWidthIn'],
    ['figHeightIn', 'figHeightIn'],
    ['figDpi', 'figDpi'],
    ['traceLineWidth', 'traceLineWidth'],
    ['traceColor', 'traceColor'],
  ].forEach(([key, id]) => {
    if (payload[key] !== undefined) {
      const el = document.getElementById(id);
      if (el) el.value = payload[key];
    }
  });
  [
    ['fitShowRaw', 'fitShowRaw'],
    ['fftLog', 'fftLog'],
    ['stftLog', 'stftLog'],
    ['showGrid', 'showGrid'],
    ['showTitle', 'showTitle'],
  ].forEach(([key, id]) => {
    if (typeof payload[key] === 'boolean') {
      const el = document.getElementById(id);
      if (el) el.checked = payload[key];
    }
  });
  updateRhdParameterGroups();
  if (payload.currentChannel) {
    const item = [...document.querySelectorAll('#channelList .file-item')].find(el => el.textContent === payload.currentChannel);
    if (item) selectChannel(item, payload.currentChannel);
  }
};
window.dpAfterFileProfileApplied = () => {
  if (_activeProfileLoadSeq && _activeProfileLoadSeq !== _fileLoadSeq) return;
  queueRender();
  plot();
};
window.dpApplyRunManifest = manifest => {
  dpApplyRunManifestFallback(manifest);
  const inputPath = dpFirstManifestInput(manifest);
  if (inputPath) {
    _currentFile = inputPath;
    document.getElementById('folderPath').value = manifest.project_root || dpPathDir(inputPath);
  }
  const params = manifest.parameters || {};
  if (Array.isArray(params.paths)) {
    _queueFiles = params.paths.slice();
    _queueSel = _queueFiles.length ? 0 : -1;
  } else if ((manifest.input_files || []).length > 1) {
    _queueFiles = manifest.input_files.map(rec => rec.path).filter(Boolean);
    _queueSel = _queueFiles.length ? 0 : -1;
  }
  if (params.channel) _currentChannel = params.channel;
  if (typeof params.preview_merge_pair === 'boolean') document.getElementById('previewMergePair').checked = params.preview_merge_pair;
  if (params.downsample) document.getElementById('previewDownsample').value = params.downsample;
  updateRhdParameterGroups();
  queueRender();
  updateInfoCard();
  if (_currentFile && _currentChannel) plot();
};
