function renderAvailableTiffList() {
  const el = document.getElementById('availableTiffList');
  const count = document.getElementById('availableCount');
  if (count) count.textContent = `${_availableTiffs.length} files`;
  if (!el) return;
  if (!_availableTiffs.length) {
    el.innerHTML = '<div class="file-list-empty">No TIFF stacks scanned</div>';
    return;
  }
  el.innerHTML = _availableTiffs.map((f, i) => {
    const info = f.info || {};
    const active = i === _availableActiveIndex ? 'active' : '';
    const stackTag = info.can_3d ? '3D' : 'flat';
    const meta = info.error ? info.error : `${formatDimText(info)} · ${info.axes || '?'}`;
    return `
      <div class="file-item stack-available-item ${active}" onclick="selectAvailableTiff(${i})">
        <span class="stack-available-name">${escHtml(f.name || fileBasename(f.path))}</span>
        <span class="stack-available-meta">${escHtml(meta)}</span>
        <span class="stack-pill ${info.can_3d ? '' : 'stack-pill-muted'}">${stackTag}</span>
      </div>`;
  }).join('');
}

async function scanTiffFolder() {
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
    _availableActiveIndex = _availableTiffs.length ? 0 : -1;
    renderAvailableTiffList();
    if (_availableTiffs.length) await scanAvailableInfo();
    setStatus('status', `${_availableTiffs.length} TIFF file(s) found`, _availableTiffs.length ? 'ok' : 'error');
  } catch (e) {
    setStatus('status', 'Folder scan failed: ' + e.message, 'error');
  }
}

async function scanAvailableInfo() {
  const paths = _availableTiffs.map(f => f.path).filter(Boolean);
  if (!paths.length) return;
  setStatus('status', 'Scanning TIFF stack metadata...', 'loading');
  try {
    const d = await api('/api/fluorescence/3d/tiff_info_batch', {paths});
    if (d.error) throw new Error(d.error);
    const infoMap = d.info || {};
    _availableTiffs.forEach(f => { f.info = infoMap[f.path] || null; });
    renderAvailableTiffList();
  } catch (e) {
    setStatus('status', 'Metadata scan failed: ' + e.message, 'error');
  }
}

function selectAvailableTiff(i) {
  if (i < 0 || i >= _availableTiffs.length) return;
  _availableActiveIndex = i;
  const f = _availableTiffs[i];
  document.getElementById('tiffPath').value = f.path || '';
  renderAvailableTiffList();
  loadTiffStack(f.info || null);
}

function clearLoadedInfo() {
  _currentInfo = null;
  updateSelectedSummary(null);
  document.getElementById('sliceSection').style.display = 'none';
  document.getElementById('renderSection').style.display = 'none';
  document.getElementById('channelRangeControls').innerHTML = '';
  document.getElementById('stackDetailBody').innerHTML = '<div class="file-list-empty">No TIFF stack loaded</div>';
  document.getElementById('slicePreview').innerHTML = '<div class="plot-placeholder">Selected slice preview appears here</div>';
  document.getElementById('rotationPreview').innerHTML = '<div class="plot-placeholder">Rotation GIF preview appears here</div>';
  clearVolume3D();
}

function updateSelectedSummary(info) {
  const path = document.getElementById('tiffPath').value.trim();
  document.getElementById('selectedName').textContent = info?.name || fileBasename(path) || 'No TIFF selected';
  document.getElementById('selectedSub').textContent = path ? compactPath(path) : 'Use folder scan or choose a .tif/.tiff file';
  document.getElementById('selectedBadge').textContent = info ? (info.can_3d ? 'Z-stack' : 'flat TIFF') : 'not loaded';
  const pills = document.getElementById('selectedPills');
  if (!info) {
    pills.innerHTML = '<span class="stack-pill stack-pill-muted">not scanned</span>';
    return;
  }
  const d = stackDims(info);
  pills.innerHTML = `
    <span class="stack-pill">${escHtml(info.axes || '?')}</span>
    <span class="stack-pill">${d.z || 1} Z</span>
    <span class="stack-pill ${Number(d.c || 1) > 1 ? '' : 'stack-pill-muted'}">${d.c || 1} C</span>`;
}

async function loadTiffStack(prefetchedInfo) {
  const path = document.getElementById('tiffPath').value.trim();
  if (!path) {
    setStatus('status', 'Choose a TIFF stack', 'error');
    return;
  }
  btnBusy('btnLoadTiff', true, 'Loading...');
  setStatus('status', 'Loading TIFF stack metadata...', 'loading');
  try {
    let info = prefetchedInfo;
    if (!info || info.error) {
      const d = await api('/api/fluorescence/3d/tiff_info', {path});
      if (d.error) throw new Error(d.error);
      info = d.info;
    }
    _currentInfo = info;
    updateSelectedSummary(info);
    renderStackDetails();
    updatePreviewControls();
    renderChannelRangeControls();
    renderDistributionControls();
    document.getElementById('sliceSection').style.display = '';
    document.getElementById('renderSection').style.display = '';
    document.getElementById('btnPreview3D').disabled = !info.can_3d;
    document.getElementById('btnExport3D').disabled = !info.can_3d;
    document.getElementById('btnPreviewGif').disabled = !info.can_3d;
    document.getElementById('btnExportGif').disabled = !info.can_3d;
    document.getElementById('btnAnalyzeDistribution').disabled = !info.can_3d;
    document.getElementById('viewerName').textContent = info.name || fileBasename(path);
    document.getElementById('outputName').value = (info.name || fileBasename(path)).replace(/\.(tif|tiff)$/i, '');
    clearVolume3D(info.can_3d ? 'Click Preview 3D to build the volume' : 'This TIFF has only one readable Z/slice plane');
    refreshSlicePreview();
    setStatus('status', info.can_3d ? 'TIFF stack loaded' : 'TIFF loaded, but it is not a Z-stack', info.can_3d ? 'ok' : 'error');
  } catch (e) {
    setStatus('status', 'TIFF load failed: ' + e.message, 'error');
    showLog('TIFF Load Error', e.message || String(e));
  } finally {
    btnBusy('btnLoadTiff', false, 'Load');
  }
}

function renderStackDetails() {
  const body = document.getElementById('stackDetailBody');
  const info = _currentInfo;
  if (!info) {
    body.innerHTML = '<div class="file-list-empty">No TIFF stack loaded</div>';
    return;
  }
  const d = stackDims(info);
  const cal = info.calibration || {};
  body.innerHTML = `
    <div class="lif-detail-title">${escHtml(info.name || fileBasename(info.path))}</div>
    <div class="lif-detail-muted">${escHtml(compactPath(info.path || ''))}</div>
    <div class="lif-detail-grid">
      <span>Axes</span><strong>${escHtml(info.axes || '')}</strong>
      <span>Shape</span><strong>${escHtml((info.shape || []).join(' × '))}</strong>
      <span>Dimensions</span><strong>${escHtml(formatDimText(info))}</strong>
      <span>Dtype</span><strong>${escHtml(info.dtype || '')}</strong>
      <span>Pixel size</span><strong>${formatNumber(cal.pixel_width_um, 4)} um/px</strong>
      <span>Z spacing</span><strong>${formatNumber(cal.z_spacing_um, 4)} um</strong>
    </div>
    <div class="lif-export-note">Scale: ${escHtml(cal.pixel_source || 'fallback')}<br>Z: ${escHtml(cal.z_source || 'fallback')}</div>
  `;
  document.getElementById('stackMeta').textContent =
    `${info.name || 'TIFF stack'} · ${d.z || 1} Z · ${d.c || 1} channel(s) · ${formatNumber(cal.pixel_width_um, 4)} um/px`;
}

function renderChannelRangeControls() {
  const el = document.getElementById('channelRangeControls');
  if (!el) return;
  if (!_currentInfo) {
    el.innerHTML = '';
    return;
  }
  const d = stackDims(_currentInfo);
  const cCount = Math.max(1, Number(d.c || 1));
  const signalDefault = Math.max(0, Math.min(99.95, parseFloat(document.getElementById('volumeThreshold')?.value || '98.8') || 98.8));
  const colors = ['#3b82f6', '#22c55e', '#ef4444', '#d946ef', '#06b6d4', '#eab308'];
  const rows = [
    `<div class="stack-channel-range-row stack-channel-range-head">
      <span>C</span><span>Use</span><span>Color</span><span>Low %</span><span>High %</span><span>Signal %</span>
    </div>`
  ];
  for (let i = 0; i < cCount; i += 1) {
    const color = colors[i % colors.length];
    rows.push(`
      <div class="stack-channel-range-row">
        <span class="stack-channel-label"><i class="stack-channel-swatch" id="chanSwatch_${i}" style="background:${color}"></i>C${i + 1}</span>
        <input type="checkbox" id="chanEnabled_${i}" checked title="Render/export C${i + 1}">
        <input type="color" id="chanColor_${i}" value="${color}" oninput="document.getElementById('chanSwatch_${i}').style.background=this.value">
        <input type="number" id="chanLow_${i}" value="1" min="0" max="99" step="0.1">
        <input type="number" id="chanHigh_${i}" value="99.7" min="0.1" max="100" step="0.1">
        <input type="number" id="chanSignal_${i}" value="${signalDefault.toFixed(1)}" min="0" max="99.95" step="0.1">
      </div>`);
  }
  el.innerHTML = rows.join('');
}

function renderDistributionControls() {
  const select = document.getElementById('distributionChannel');
  if (!select) return;
  const d = stackDims(_currentInfo);
  const cCount = Math.max(1, Number(d.c || 1));
  const current = select.value || String(parseInt(document.getElementById('cSlider')?.value || '0', 10));
  select.innerHTML = Array.from({length: cCount}, (_v, i) => `<option value="${i}">C${i + 1}</option>`).join('');
  select.value = String(Math.max(0, Math.min(parseInt(current || '0', 10) || 0, cCount - 1)));
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

function updateSliderLabel(id, labelId) {
  const slider = document.getElementById(id);
  const label = document.getElementById(labelId);
  if (!slider || !label) return;
  label.textContent = `${Number(slider.value || 0) + 1} / ${Number(slider.max || 0) + 1}`;
}

function updateExtraControls() {
  const el = document.getElementById('extraDimControls');
  if (!el) return;
  const extras = stackDims(_currentInfo).extras || [];
  if (!extras.length) {
    el.innerHTML = '';
    return;
  }
  el.innerHTML = extras.map(extra => {
    const count = Math.max(1, Number(extra.count || 1));
    const label = escHtml(extra.axis || ('D' + extra.index));
    return `
      <div class="lif-slider-row lif-extra-dim-row">
        <span>${label}</span>
        <input type="range" id="extraAxis_${extra.index}" min="0" max="${count - 1}" value="0" oninput="onDimSlide()">
        <span id="extraAxisLabel_${extra.index}">1 / ${count}</span>
      </div>`;
  }).join('');
}

function updatePreviewControls() {
  const d = stackDims(_currentInfo);
  setSliderDim('cSlider', 'cLabel', d.c || 1);
  setSliderDim('zSlider', 'zLabel', d.z || 1);
  setSliderDim('tSlider', 'tLabel', d.t || 1);
  updateExtraControls();
}

function extraIndicesPayload() {
  const extras = stackDims(_currentInfo).extras || [];
  const out = {};
  extras.forEach(extra => {
    const slider = document.getElementById(`extraAxis_${extra.index}`);
    const label = document.getElementById(`extraAxisLabel_${extra.index}`);
    const value = parseInt((slider && slider.value) || '0', 10);
    out[String(extra.index)] = value;
    if (label) label.textContent = `${value + 1} / ${Math.max(1, Number(extra.count || 1))}`;
  });
  return out;
}

function onDimSlide() {
  updateSliderLabel('cSlider', 'cLabel');
  updateSliderLabel('zSlider', 'zLabel');
  updateSliderLabel('tSlider', 'tLabel');
  extraIndicesPayload();
  refreshSlicePreview();
}

function currentPreviewPayload() {
  return {
    path: document.getElementById('tiffPath').value.trim(),
    c: parseInt(document.getElementById('cSlider').value || '0', 10),
    z: parseInt(document.getElementById('zSlider').value || '0', 10),
    t: parseInt(document.getElementById('tSlider').value || '0', 10),
    extra_indices: extraIndicesPayload(),
    lut: document.getElementById('lutSelect').value,
    p_low: 1,
    p_high: 99,
  };
}

function refreshSlicePreview() {
  if (!_currentInfo) return;
  clearTimeout(_previewTimer);
  _previewTimer = setTimeout(loadSlicePreview, 120);
}

async function loadSlicePreview() {
  const preview = document.getElementById('slicePreview');
  preview.innerHTML = '<div class="plot-placeholder">Loading slice...</div>';
  try {
    const d = await api('/api/fluorescence/3d/preview_slice', currentPreviewPayload());
    if (d.error) throw new Error(d.error);
    preview.innerHTML = `<img src="data:image/png;base64,${d.img}" alt="TIFF slice"/>`;
  } catch (e) {
    preview.innerHTML = `<div class="plot-placeholder">Preview error: ${escHtml(e.message)}</div>`;
    setStatus('status', 'Preview error', 'error');
  }
}
