function updatePreviewControls(rec) {
  const d = rec.dimensions || {};
  setSliderDim('cSlider', 'cLabel', rec.channels || 1);
  setSliderDim('zSlider', 'zLabel', d.z || 1);
  setSliderDim('tSlider', 'tLabel', d.t || 1);
  setSliderDim('mSlider', 'mLabel', d.m || 1);
  renderExtraDimControls(rec);
  extraPreviewDims(rec).forEach(dim => {
    setSliderDim(`extraDimSlider_${Number(dim.id)}`, `extraDimLabel_${Number(dim.id)}`, dim.count || 1);
  });
}

function onLifDimSlide() {
  const rec = findRecord(_lifActiveIndex);
  if (rec) updatePreviewControls(rec);
  refreshLifPreview();
}

function currentPreviewPayload() {
  const rec = findRecord(_lifActiveIndex);
  const requestedDims = {};
  extraPreviewDims(rec || {}).forEach(dim => {
    const id = Number(dim.id);
    const slider = document.getElementById(`extraDimSlider_${id}`);
    requestedDims[String(id)] = parseInt((slider && slider.value) || '0', 10);
  });
  return {
    path: _lifPath,
    image_index: _lifActiveIndex,
    c: parseInt(document.getElementById('cSlider').value || '0', 10),
    z: parseInt(document.getElementById('zSlider').value || '0', 10),
    t: parseInt(document.getElementById('tSlider').value || '0', 10),
    m: parseInt(document.getElementById('mSlider').value || '0', 10),
    requested_dims: requestedDims,
    lut: document.getElementById('lutSelect').value,
    p_low: parseFloat(document.getElementById('pLow').value || '1'),
    p_high: parseFloat(document.getElementById('pHigh').value || '99'),
  };
}

function lifRunParameters(extra) {
  return Object.assign({
    path: _lifPath,
    sort_mode: document.getElementById('sortMode').value,
    rename_map: _lifRenameMap,
    order_indices: _lifDisplayRecords.map(r => r.index),
    preview: currentPreviewPayload(),
  }, extra || {});
}

window.dpApplyRunManifest = manifest => {
  dpApplyRunManifestFallback(manifest);
  const params = manifest.parameters || {};
  const inputPath = params.path || dpFirstManifestInput(manifest);
  if (inputPath) {
    _lifPath = inputPath;
    document.getElementById('lifPath').value = inputPath;
    document.getElementById('folderPath').value = manifest.project_root || dpPathDir(inputPath);
  }
  if (params.sort_mode) document.getElementById('sortMode').value = params.sort_mode;
  if (params.rename_map && typeof params.rename_map === 'object') {
    _lifRenameMap = params.rename_map;
    saveRenameMap();
  }
  if (inputPath && HAS_READLIF && HAS_PIL) loadLifProject();
  setStatus('status', 'LIF parameters loaded from run manifest', 'ok');
};

function refreshLifPreview() {
  if (!_lifPath || _lifActiveIndex === null) return;
  clearTimeout(_lifPreviewDebounce);
  _lifPreviewDebounce = setTimeout(loadLifPreview, 120);
}

function loadLifPreview() {
  const preview = document.getElementById('lifPreview');
  preview.innerHTML = '<div class="plot-placeholder">Loading frame...</div>';
  api('/api/fluorescence/lif/preview', currentPreviewPayload()).then(d => {
    if (d.error) throw new Error(d.error);
    preview.innerHTML = `<img src="data:image/png;base64,${d.img}" alt="LIF frame"/>`;
  }).catch(e => {
    preview.innerHTML = `<div class="plot-placeholder">Preview error: ${escapeHtml(e.message)}</div>`;
    setStatus('status', 'Preview error', 'error');
  });
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'currentPreviewPayload',
  'lifRunParameters',
  'loadLifPreview',
  'onLifDimSlide',
  'refreshLifPreview',
  'updatePreviewControls',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
