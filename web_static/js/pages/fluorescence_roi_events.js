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
