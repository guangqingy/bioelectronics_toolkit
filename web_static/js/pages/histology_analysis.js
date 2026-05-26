let _histologyProject = null;
let _histologyProjectEntries = [];
let _histologyProjectEntryId = '';
let _histologyAnalysisImage = null;
let _histologyAnalysisRois = [];
let _histologyActivePolygon = null;
let _histologyPolygonCounter = 1;

const HISTOLOGY_ROI_COLOR_TOKENS = ['--blue', '--success', '--error', '--warning', '--blue-hover', '--error-text-strong'];

function histologyThemeColor(token, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  return value || fallback || '#3E6AE1';
}

function histologyRoiFallbackColor() {
  return histologyThemeColor('--blue', '#3E6AE1');
}

function histologySetButtonDisabled(id, disabled) {
  const el = document.getElementById(id);
  if (el) el.disabled = !!disabled;
}

function updateHistologyActionState() {
  const hasFolder = !!histologyDataProjectPath();
  const hasImage = !!_histologyAnalysisImage;
  const drawing = !!_histologyActivePolygon;
  const hasRois = _histologyAnalysisRois.length > 0;
  histologySetButtonDisabled('btnLoadHistologyAnalysisProject', !hasFolder);
  histologySetButtonDisabled('btnStartHistologyRoi', !hasImage || drawing);
  histologySetButtonDisabled('btnFinishHistologyRoi', !drawing);
  histologySetButtonDisabled('btnUndoHistologyRoi', !hasImage || (!drawing && !hasRois));
  histologySetButtonDisabled('btnClearHistologyRois', !hasImage || (!drawing && !hasRois));
  histologySetButtonDisabled('btnSaveHistologyRois', !hasImage || drawing || !hasRois);
  histologySetButtonDisabled('btnAnalyzeHistology', !hasImage || drawing || !hasRois);
}

function histologyAnalysisProjectPath() {
  return (document.getElementById('projectPath')?.value || '').trim();
}

function histologyDataProjectPath() {
  return histologyAnalysisProjectPath();
}

function histologyProjectRoot() {
  return histologyDataProjectPath();
}

function histologyRoiLabelElements() {
  return ['histologyRoiLabelInline']
    .map(id => document.getElementById(id))
    .filter(el => !!el);
}

function histologyCurrentRoiLabel() {
  for (const el of histologyRoiLabelElements()) {
    const value = (el.value || '').trim();
    if (value) return value;
  }
  return `ROI ${_histologyPolygonCounter}`;
}

function setHistologyRoiLabel(value) {
  histologyRoiLabelElements().forEach(el => {
    el.value = value;
  });
}

function bindHistologyRoiLabels() {
  histologyRoiLabelElements().forEach(el => {
    if (el.dataset.bound === '1') return;
    el.dataset.bound = '1';
    el.addEventListener('input', () => setHistologyRoiLabel(el.value || ''));
  });
}

function histologySetAnalysisStatus(message, kind) {
  setStatus('status', message, kind || 'ok');
  const hint = document.getElementById('histologyAnalysisHint');
  if (hint && message) hint.textContent = message;
}

function loadHistologyDataProject() {
  const projectPath = histologyDataProjectPath();
  if (!projectPath) {
    histologySetAnalysisStatus('Select a histology project first', 'error');
    return;
  }
  _histologyAnalysisImage = null;
  _histologyAnalysisRois = [];
  _histologyActivePolygon = null;
  renderHistologyRoiList();
  clearHistologyAnalysisImage('Select a project image to preview it here');
  btnBusy('btnLoadHistologyAnalysisProject', true, 'Loading...');
  histologySetAnalysisStatus('Loading histology project...', 'loading');
  api('/api/histology/project/load', {project_path: projectPath})
    .then(d => {
      btnBusy('btnLoadHistologyAnalysisProject', false, 'Load Project');
      if (d.error) throw new Error(d.error);
      _histologyProject = d;
      _histologyProjectEntries = d.entries || [];
      renderHistologyAnalysisProjectImageList();
      document.getElementById('histologyAnalysisMeta').textContent =
        `${_histologyProjectEntries.length} project image(s)`;
      if (d.project_path && d.project_path !== projectPath) {
        document.getElementById('projectPath').value = d.project_path;
      }
      histologySetAnalysisStatus(`Loaded ${_histologyProjectEntries.length} project image(s)`, 'ok');
      if (_histologyProjectEntries.length) selectHistologyProjectImage(_histologyProjectEntries[0].entry_id);
      else updateHistologyActionState();
    })
    .catch(e => {
      btnBusy('btnLoadHistologyAnalysisProject', false, 'Load Project');
      histologySetAnalysisStatus('Error: ' + e.message, 'error');
      updateHistologyActionState();
    });
}

function loadHistologyAnalysisProject() {
  return loadHistologyDataProject();
}

function renderHistologyAnalysisProjectImageList() {
  const el = document.getElementById('histologyProjectAnalysisImageList');
  if (!el) return;
  if (!_histologyProjectEntries.length) {
    el.innerHTML = '<div class="file-list-empty">No project images loaded</div>';
    return;
  }
  el.innerHTML = _histologyProjectEntries.map(entry => {
    const active = String(entry.entry_id) === String(_histologyProjectEntryId) ? ' active' : '';
    const counts = `${entry.roi_count || 0} ROI · ${entry.analysis_count || 0} analyses`;
    const missing = entry.exists ? '' : ' · missing source';
    const role = entry.role && entry.role !== 'image' ? ` · ${entry.role}` : '';
    const detail = [entry.case_name || '', entry.case_relative_path || entry.relative_path || entry.source_path || '']
      .filter(Boolean).join(' · ');
    return `
      <div class="file-item${active}" data-entry-id="${escHtml(entry.entry_id)}" onclick="DP.page.selectHistologyProjectImage('${escHtml(entry.entry_id)}')">
        <div class="histology-file-title">${escHtml(entry.image_name || entry.entry_id)}</div>
        <div class="histology-file-subline">${escHtml(counts + role + missing)}</div>
        <div class="histology-file-path">${escHtml(detail)}</div>
      </div>`;
  }).join('');
}

function selectHistologyProjectImage(entryId) {
  if (!entryId) return;
  const projectPath = histologyDataProjectPath();
  if (!projectPath) {
    histologySetAnalysisStatus('Select a histology project first', 'error');
    return;
  }
  _histologyProjectEntryId = String(entryId);
  _histologyActivePolygon = null;
  _histologyAnalysisImage = null;
  _histologyAnalysisRois = [];
  renderHistologyAnalysisProjectImageList();
  renderHistologyRoiList();
  clearHistologyAnalysisImage('Loading project image preview...');
  histologySetAnalysisStatus('Loading project image preview...', 'loading');
  api('/api/histology/project/image_preview', {
    project_path: projectPath,
    entry_id: _histologyProjectEntryId,
    max_side: 1600,
  }).then(d => {
    if (d.error) throw new Error(d.error);
    _histologyAnalysisImage = d;
    _histologyAnalysisRois = Array.isArray(d.rois) ? d.rois : [];
    _histologyPolygonCounter = Math.max(1, _histologyAnalysisRois.length + 1);
    renderHistologyRoiList();
    const latestAnalysis = (d.analyses || []).slice(-1)[0] || null;
    applyHistologyAnalysisParameters(latestAnalysis?.parameters || null);
    renderHistologyAnalysisResults(latestAnalysis);
    loadHistologyAnalysisImage(d);
    document.getElementById('histologyAnalysisMeta').textContent =
      `${d.image_name || entryId} · ${d.preview_width}x${d.preview_height} · ${d.backend}`;
    histologySetAnalysisStatus('Image preview loaded', 'ok');
    updateHistologyActionState();
  }).catch(e => {
    histologySetAnalysisStatus('Error: ' + e.message, 'error');
    clearHistologyAnalysisImage('Preview failed');
    updateHistologyActionState();
  });
}

function clearHistologyAnalysisImage(message) {
  const surface = document.getElementById('histologyAnalysisSurface');
  const empty = document.getElementById('histologyAnalysisEmpty');
  const img = document.getElementById('histologyAnalysisImg');
  const canvas = document.getElementById('histologyAnalysisCanvas');
  if (surface) surface.hidden = true;
  if (empty) {
    empty.hidden = false;
    if (message) empty.textContent = message;
  }
  if (img) {
    img.hidden = true;
    img.removeAttribute('src');
  }
  if (canvas) canvas.hidden = true;
}

function loadHistologyAnalysisImage(payload) {
  const img = document.getElementById('histologyAnalysisImg');
  if (!img || !payload.img) return;
  const surface = document.getElementById('histologyAnalysisSurface');
  const empty = document.getElementById('histologyAnalysisEmpty');
  if (surface) surface.hidden = false;
  if (empty) empty.hidden = true;
  img.onload = () => resizeHistologyAnalysisCanvas();
  img.hidden = false;
  img.src = 'data:image/png;base64,' + payload.img;
}

function resizeHistologyAnalysisCanvas() {
  const img = document.getElementById('histologyAnalysisImg');
  const canvas = document.getElementById('histologyAnalysisCanvas');
  if (!img || !canvas || !img.clientWidth || !img.clientHeight) return;
  canvas.width = img.clientWidth;
  canvas.height = img.clientHeight;
  canvas.style.width = img.clientWidth + 'px';
  canvas.style.height = img.clientHeight + 'px';
  canvas.hidden = false;
  drawHistologyRois();
}

function histologyCanvasPoint(event) {
  const canvas = document.getElementById('histologyAnalysisCanvas');
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const nativeW = Math.max(1, Number(_histologyAnalysisImage?.preview_width || canvas.width));
  const nativeH = Math.max(1, Number(_histologyAnalysisImage?.preview_height || canvas.height));
  return {
    x: Math.max(0, Math.min(nativeW, x / Math.max(1, canvas.width) * nativeW)),
    y: Math.max(0, Math.min(nativeH, y / Math.max(1, canvas.height) * nativeH)),
  };
}

function nativeToHistologyCanvas(point) {
  const canvas = document.getElementById('histologyAnalysisCanvas');
  const nativeW = Math.max(1, Number(_histologyAnalysisImage?.preview_width || canvas.width));
  const nativeH = Math.max(1, Number(_histologyAnalysisImage?.preview_height || canvas.height));
  return {
    x: Number(point.x || 0) / nativeW * canvas.width,
    y: Number(point.y || 0) / nativeH * canvas.height,
  };
}

function setupHistologyAnalysisCanvas() {
  const canvas = document.getElementById('histologyAnalysisCanvas');
  if (!canvas || canvas.dataset.bound === '1') return;
  canvas.dataset.bound = '1';
  canvas.addEventListener('click', event => {
    if (!_histologyActivePolygon) return;
    const point = histologyCanvasPoint(event);
    _histologyActivePolygon.points.push(point);
    drawHistologyRois();
  });
  canvas.addEventListener('dblclick', event => {
    event.preventDefault();
    finishHistologyPolygon();
  });
  window.addEventListener('resize', () => requestAnimationFrame(resizeHistologyAnalysisCanvas));
}

function nextHistologyRoiColor(index) {
  const token = HISTOLOGY_ROI_COLOR_TOKENS[index % HISTOLOGY_ROI_COLOR_TOKENS.length];
  return histologyThemeColor(token, histologyRoiFallbackColor());
}

function startHistologyPolygon() {
  if (!_histologyAnalysisImage) {
    histologySetAnalysisStatus('Load a project image first', 'error');
    return;
  }
  const label = histologyCurrentRoiLabel();
  setHistologyRoiLabel(label);
  _histologyActivePolygon = {
    id: `roi_${Date.now()}`,
    label,
    classification: 'Annotation',
    color: nextHistologyRoiColor(_histologyAnalysisRois.length),
    points: [],
  };
  renderHistologyRoiList();
  drawHistologyRois();
  histologySetAnalysisStatus('Click the image to add polygon vertices. Double-click or Finish to close.', 'ok');
  updateHistologyActionState();
}

function finishHistologyPolygon() {
  if (!_histologyActivePolygon) return;
  if ((_histologyActivePolygon.points || []).length < 3) {
    histologySetAnalysisStatus('A polygon ROI needs at least 3 points', 'error');
    return;
  }
  _histologyAnalysisRois.push(_histologyActivePolygon);
  _histologyActivePolygon = null;
  _histologyPolygonCounter += 1;
  setHistologyRoiLabel(`ROI ${_histologyPolygonCounter}`);
  renderHistologyRoiList();
  drawHistologyRois();
  histologySetAnalysisStatus('ROI added. Save or analyze to write it into the DataProcess project.', 'ok');
  updateHistologyActionState();
}

function undoHistologyPoint() {
  if (_histologyActivePolygon && _histologyActivePolygon.points.length) {
    _histologyActivePolygon.points.pop();
  } else if (_histologyAnalysisRois.length) {
    _histologyAnalysisRois[_histologyAnalysisRois.length - 1].points.pop();
    if (_histologyAnalysisRois[_histologyAnalysisRois.length - 1].points.length < 3) {
      _histologyAnalysisRois.pop();
    }
    renderHistologyRoiList();
  }
  drawHistologyRois();
  updateHistologyActionState();
}

function clearHistologyRois() {
  _histologyActivePolygon = null;
  _histologyAnalysisRois = [];
  renderHistologyRoiList();
  drawHistologyRois();
  renderHistologyAnalysisResults(null);
  histologySetAnalysisStatus('ROI cleared for the current image', 'ok');
  updateHistologyActionState();
}

function deleteHistologyRoi(index) {
  if (index < 0 || index >= _histologyAnalysisRois.length) return;
  _histologyAnalysisRois.splice(index, 1);
  renderHistologyRoiList();
  drawHistologyRois();
  updateHistologyActionState();
}

function renderHistologyRoiList() {
  const el = document.getElementById('histologyRoiList');
  const countEl = document.getElementById('histologyRoiInlineCount');
  if (countEl) {
    const drawing = _histologyActivePolygon ? ' · drawing' : '';
    countEl.textContent = `${_histologyAnalysisRois.length} ROI${drawing}`;
  }
  if (!el) return;
  if (!_histologyAnalysisRois.length && !_histologyActivePolygon) {
    el.innerHTML = '<div class="file-list-empty">No ROI for current image</div>';
    updateHistologyActionState();
    return;
  }
  const saved = _histologyAnalysisRois.map((roi, i) => `
    <div class="file-item histology-roi-item">
      <span class="histology-roi-dot" style="--roi-color:${escHtml(roi.color || histologyRoiFallbackColor())}"></span>
      <span class="histology-roi-name">${escHtml(roi.label || ('ROI ' + (i + 1)))} · ${(roi.points || []).length} pt</span>
      <button class="btn-secondary histology-roi-delete" type="button" onclick="DP.page.deleteHistologyRoi(${i})">X</button>
    </div>`);
  if (_histologyActivePolygon) {
    saved.push(`<div class="file-item active">${escHtml(_histologyActivePolygon.label)} · drawing ${_histologyActivePolygon.points.length} pt</div>`);
  }
  el.innerHTML = saved.join('');
  updateHistologyActionState();
}

function drawHistologyRois() {
  const canvas = document.getElementById('histologyAnalysisCanvas');
  if (!canvas || !canvas.width) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const all = _histologyAnalysisRois.concat(_histologyActivePolygon ? [_histologyActivePolygon] : []);
  all.forEach((roi, idx) => {
    const pts = roi.points || [];
    if (!pts.length) return;
    ctx.strokeStyle = roi.color || nextHistologyRoiColor(idx);
    ctx.fillStyle = roi.color || nextHistologyRoiColor(idx);
    ctx.lineWidth = 2;
    ctx.setLineDash(roi === _histologyActivePolygon ? [6, 4] : []);
    ctx.beginPath();
    pts.forEach((point, i) => {
      const p = nativeToHistologyCanvas(point);
      if (i === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    if (roi !== _histologyActivePolygon && pts.length >= 3) ctx.closePath();
    ctx.stroke();
    pts.forEach(point => {
      const p = nativeToHistologyCanvas(point);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
      ctx.fill();
    });
    const first = nativeToHistologyCanvas(pts[0]);
    ctx.font = 'bold 12px sans-serif';
    ctx.fillText(roi.label || 'ROI', first.x + 6, Math.max(12, first.y - 6));
    ctx.setLineDash([]);
  });
}

function histologyAnalysisParameters() {
  const num = (id, fallback) => {
    const value = Number(document.getElementById(id)?.value);
    return Number.isFinite(value) ? value : fallback;
  };
  return {
    dapi_channel: document.getElementById('dapiChannel')?.value || 'dapi',
    dapi_threshold_method: document.getElementById('dapiThresholdMethod')?.value || 'otsu',
    dapi_threshold: num('dapiThreshold', 80),
    dapi_mask_enabled: !!document.getElementById('dapiMaskEnabled')?.checked,
    dapi_dilate_px: num('dapiDilatePx', 2),
    sma_channel: document.getElementById('smaChannel')?.value || 'fitc',
    sma_threshold_method: document.getElementById('smaThresholdMethod')?.value || 'otsu',
    sma_threshold: num('smaThreshold', 120),
    macrophage_channel: document.getElementById('macrophageChannel')?.value || 'cy5',
    macrophage_threshold_method: document.getElementById('macrophageThresholdMethod')?.value || 'otsu',
    macrophage_threshold: num('macrophageThreshold', 120),
    background_mode: document.getElementById('backgroundMode')?.value || 'percentile',
    background_percentile: num('backgroundPercentile', 10),
    smooth_sigma: num('smoothSigma', 1),
    min_positive_area_px: num('minPositiveArea', 12),
    threshold_percentile: num('thresholdPercentile', 97.5),
    threshold_std_k: num('thresholdStdK', 2),
  };
}

function applyHistologyAnalysisParameters(params) {
  if (!params || typeof params !== 'object') return;
  const setValue = (id, key) => {
    const el = document.getElementById(id);
    if (!el || params[key] === undefined || params[key] === null) return;
    if (el.type === 'checkbox') el.checked = !!params[key];
    else el.value = String(params[key]);
  };
  setValue('dapiChannel', 'dapi_channel');
  setValue('dapiThresholdMethod', 'dapi_threshold_method');
  setValue('dapiThreshold', 'dapi_threshold');
  setValue('dapiMaskEnabled', 'dapi_mask_enabled');
  setValue('dapiDilatePx', 'dapi_dilate_px');
  setValue('smaChannel', 'sma_channel');
  setValue('smaThresholdMethod', 'sma_threshold_method');
  setValue('smaThreshold', 'sma_threshold');
  setValue('macrophageChannel', 'macrophage_channel');
  setValue('macrophageThresholdMethod', 'macrophage_threshold_method');
  setValue('macrophageThreshold', 'macrophage_threshold');
  setValue('backgroundMode', 'background_mode');
  setValue('backgroundPercentile', 'background_percentile');
  setValue('smoothSigma', 'smooth_sigma');
  setValue('minPositiveArea', 'min_positive_area_px');
  setValue('thresholdPercentile', 'threshold_percentile');
  setValue('thresholdStdK', 'threshold_std_k');
}

function saveHistologyRois() {
  if (!_histologyProjectEntryId) {
    histologySetAnalysisStatus('Select a project image first', 'error');
    return;
  }
  if (!_histologyAnalysisRois.length) {
    histologySetAnalysisStatus('Draw at least one ROI first', 'error');
    return;
  }
  btnBusy('btnSaveHistologyRois', true, 'Saving...');
  api('/api/histology/project/analysis/save_rois', {
    project_path: histologyDataProjectPath(),
    entry_id: _histologyProjectEntryId,
    rois: _histologyAnalysisRois,
  }).then(d => {
    btnBusy('btnSaveHistologyRois', false, 'Save ROI');
    if (d.error) throw new Error(d.error);
    histologySetAnalysisStatus(`Saved ${d.roi_count || 0} ROI to the DataProcess project`, 'ok');
    toast('Histology ROI saved to project');
    refreshLoadedProjectEntryCounts(d);
    updateHistologyActionState();
  }).catch(e => {
    btnBusy('btnSaveHistologyRois', false, 'Save ROI');
    histologySetAnalysisStatus('Error: ' + e.message, 'error');
    updateHistologyActionState();
  });
}

function analyzeHistologyRois() {
  if (!_histologyProjectEntryId) {
    histologySetAnalysisStatus('Select a project image first', 'error');
    return;
  }
  if (!_histologyAnalysisRois.length) {
    histologySetAnalysisStatus('Draw at least one ROI first', 'error');
    return;
  }
  btnBusy('btnAnalyzeHistology', true, 'Analyzing...');
  histologySetAnalysisStatus('Analyzing SMA and macrophage thresholds...', 'loading');
  dpRunJobEndpoint('/api/histology/project/analysis/run_job', {
    project_path: histologyDataProjectPath(),
    entry_id: _histologyProjectEntryId,
    rois: _histologyAnalysisRois,
    parameters: histologyAnalysisParameters(),
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      histologySetAnalysisStatus(`Analyzing histology ROI${pct}${msg}`, 'loading');
    },
  }).then(d => {
    btnBusy('btnAnalyzeHistology', false, 'Analyze SMA + Macrophage');
    if (d.error) throw new Error(d.error);
    renderHistologyAnalysisResults(d.analysis || d);
    refreshLoadedProjectEntryCounts(d);
    histologySetAnalysisStatus(`Analyzed ${_histologyAnalysisRois.length} ROI; results saved to the DataProcess project`, 'ok');
    toast('Histology analysis saved to project');
    recordRunHistory({
      view: 'histology_analysis',
      title: 'Histology Project SMA Macrophage Analysis',
      status: 'ok',
      project_root: histologyProjectRoot(),
      input_files: [{path: d.analysis?.image_path || _histologyAnalysisImage?.image_path || '', role: 'histology_project_image'}],
      outputs: dpAsPathRecords([d.analysis_path, d.geojson_path, d.summary_path, d.project_path, d.cache_dir], 'histology_analysis_output'),
      parameters: Object.assign({entry_id: _histologyProjectEntryId, rois: _histologyAnalysisRois}, histologyAnalysisParameters()),
      metadata: {roi_count: d.roi_count || _histologyAnalysisRois.length, backend: d.backend || ''},
    });
    updateHistologyActionState();
  }).catch(e => {
    btnBusy('btnAnalyzeHistology', false, 'Analyze SMA + Macrophage');
    histologySetAnalysisStatus('Error: ' + e.message, 'error');
    updateHistologyActionState();
  });
}

function refreshLoadedProjectEntryCounts(payload) {
  const entry = _histologyProjectEntries.find(e => String(e.entry_id) === String(_histologyProjectEntryId));
  if (entry) {
    entry.roi_count = payload.roi_count || _histologyAnalysisRois.length;
    entry.analysis_count = payload.analysis_count || entry.analysis_count || 0;
  }
  renderHistologyAnalysisProjectImageList();
}

function renderHistologyAnalysisResults(analysis) {
  const el = document.getElementById('histologyAnalysisResults');
  if (!el) return;
  const results = analysis && Array.isArray(analysis.results) ? analysis.results : [];
  if (!results.length) {
    el.innerHTML = '<div class="plot-placeholder histology-result-empty">No SMA/macrophage analysis for this image yet.</div>';
    return;
  }
  const rows = results.map(row => `
    <tr>
      <td>${escHtml(row.roi_label || row.roi_id || '')}</td>
      <td>${Number(row.area_px || 0).toLocaleString()}</td>
      <td>${(100 * Number(row.sma_positive_fraction || 0)).toFixed(2)}%</td>
      <td>${(100 * Number(row.macrophage_positive_fraction || 0)).toFixed(2)}%</td>
      <td>${(100 * Number(row.double_positive_fraction || 0)).toFixed(2)}%</td>
      <td>${Number(row.sma_object_count || 0).toLocaleString()}</td>
      <td>${Number(row.macrophage_object_count || 0).toLocaleString()}</td>
    </tr>`).join('');
  const paramText = [
    `DAPI ${analysis.parameters?.dapi_channel || ''} ${analysis.parameters?.dapi_threshold_method || ''}`,
    `SMA ${analysis.parameters?.sma_channel || ''} ${analysis.parameters?.sma_threshold_method || ''}`,
    `Mac ${analysis.parameters?.macrophage_channel || ''} ${analysis.parameters?.macrophage_threshold_method || ''}`,
    `BC ${analysis.parameters?.background_mode || ''}`,
  ].join(' · ');
  el.innerHTML = `
    <div class="histology-results-meta">
      ${escHtml(analysis.created_at || '')} · ${escHtml(paramText)}
    </div>
    <div class="histology-scroll">
      <table class="data-table">
        <thead><tr><th>ROI</th><th>Area px</th><th>SMA+</th><th>Macrophage+</th><th>Double+</th><th>SMA obj</th><th>Mac obj</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

window.addEventListener('load', () => {
  bindHistologyRoiLabels();
  setupHistologyAnalysisCanvas();
  renderHistologyAnalysisProjectImageList();
  renderHistologyRoiList();
  renderHistologyAnalysisResults(null);
  document.getElementById('projectPath')?.addEventListener('input', updateHistologyActionState);
  updateHistologyActionState();
  setTimeout(updateHistologyActionState, 180);
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'analyzeHistologyRois',
  'applyHistologyAnalysisParameters',
  'bindHistologyRoiLabels',
  'clearHistologyRois',
  'clearHistologyAnalysisImage',
  'deleteHistologyRoi',
  'drawHistologyRois',
  'finishHistologyPolygon',
  'histologyAnalysisParameters',
  'histologyCurrentRoiLabel',
  'histologyDataProjectPath',
  'histologyProjectRoot',
  'histologyAnalysisProjectPath',
  'histologyRoiLabelElements',
  'loadHistologyAnalysisImage',
  'loadHistologyDataProject',
  'loadHistologyAnalysisProject',
  'nativeToHistologyCanvas',
  'refreshLoadedProjectEntryCounts',
  'renderHistologyAnalysisResults',
  'renderHistologyAnalysisProjectImageList',
  'renderHistologyRoiList',
  'resizeHistologyAnalysisCanvas',
  'saveHistologyRois',
  'selectHistologyProjectImage',
  'setHistologyRoiLabel',
  'setupHistologyAnalysisCanvas',
  'startHistologyPolygon',
  'undoHistologyPoint',
  'updateHistologyActionState',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
