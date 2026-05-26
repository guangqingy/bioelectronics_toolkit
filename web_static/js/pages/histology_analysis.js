let _histologyProject = null;
let _histologyProjectEntries = [];
let _histologyProjectEntryId = '';
let _histologyAnalysisImage = null;
let _histologyAnalysisRois = [];
let _histologyActivePolygon = null;
let _histologyPolygonCounter = 1;
let _histologyView = {
  zoom: 1,
  rotation: 0,
  panX: 0,
  panY: 0,
  panMode: false,
  dragging: false,
  dragMoved: false,
  dragStartX: 0,
  dragStartY: 0,
  dragPanX: 0,
  dragPanY: 0,
};

const HISTOLOGY_ROI_COLOR_TOKENS = ['--blue', '--success', '--error', '--warning', '--blue-hover', '--error-text-strong'];
const HISTOLOGY_ZOOM_MIN = 0.05;
const HISTOLOGY_ZOOM_MAX = 8;

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
  const hasFile = !!histologyAnalysisImagePath();
  const hasImage = !!_histologyAnalysisImage;
  const isProjectImage = hasImage && _histologyAnalysisImage.source_mode === 'project';
  const drawing = !!_histologyActivePolygon;
  const hasRois = _histologyAnalysisRois.length > 0;
  histologySetButtonDisabled('btnLoadHistologyAnalysisProject', !hasFolder);
  histologySetButtonDisabled('btnLoadHistologyFileImage', !hasFile);
  histologySetButtonDisabled('btnStartHistologyRoi', !hasImage || drawing);
  histologySetButtonDisabled('btnFinishHistologyRoi', !drawing);
  histologySetButtonDisabled('btnUndoHistologyRoi', !hasImage || (!drawing && !hasRois));
  histologySetButtonDisabled('btnClearHistologyRois', !hasImage || (!drawing && !hasRois));
  histologySetButtonDisabled('btnSaveHistologyRois', !isProjectImage || drawing || !hasRois);
  histologySetButtonDisabled('btnAnalyzeHistology', !hasImage || drawing || !hasRois);
  [
    'btnHistologyZoomOut',
    'btnHistologyZoomIn',
    'btnHistologyFit',
    'btnHistologyActual',
    'btnHistologyRotateLeft',
    'btnHistologyRotateRight',
    'btnHistologyPan',
  ].forEach(id => histologySetButtonDisabled(id, !hasImage));
  const slider = document.getElementById('histologyZoomSlider');
  if (slider) slider.disabled = !hasImage;
  updateHistologyViewControls();
}

function histologyAnalysisProjectPath() {
  return (document.getElementById('projectPath')?.value || '').trim();
}

function histologyDataProjectPath() {
  return histologyAnalysisProjectPath();
}

function histologyAnalysisImagePath() {
  return (document.getElementById('histologyImagePath')?.value || '').trim();
}

function histologyAnalysisProjectPathError(projectPath) {
  const text = String(projectPath || '').trim();
  const leaf = text.split(/[\\/]/).pop().toLowerCase();
  if (!leaf) return '';
  if (leaf === 'project.json' || leaf.endsWith('.dphistology') || leaf.endsWith('.json')) return '';
  if (/\.(tif|tiff|png|jpg|jpeg|vsi|ets)$/.test(leaf)) {
    return 'That file is an image/raw microscopy file, not a DataProcess project. Load histology_project.dphistology or its containing folder; create it from Histology Naming first.';
  }
  return '';
}

function histologyAnalysisFilePathError(imagePath) {
  const leaf = String(imagePath || '').trim().split(/[\\/]/).pop().toLowerCase();
  if (!leaf) return 'Select an exported image file first';
  if (/\.(vsi|ets)$/.test(leaf)) {
    return 'Open an exported TIFF/PNG/JPG for analysis. Raw VSI/ETS files need to be converted first in Histology Naming.';
  }
  if (!/\.(tif|tiff|png|jpg|jpeg)$/.test(leaf)) {
    return 'Open an exported TIFF, PNG, or JPG image file for quick testing.';
  }
  return '';
}

function histologyProjectRoot() {
  return histologyDataProjectPath() || histologyAnalysisImagePath();
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

function histologyClamp(value, low, high) {
  return Math.max(low, Math.min(high, Number(value) || 0));
}

function histologyPreviewSize() {
  const img = document.getElementById('histologyAnalysisImg');
  const width = Math.max(1, Number(_histologyAnalysisImage?.preview_width || img?.naturalWidth || 1));
  const height = Math.max(1, Number(_histologyAnalysisImage?.preview_height || img?.naturalHeight || 1));
  return {width, height};
}

function histologyRotatedBounds(zoom, rotation) {
  const size = histologyPreviewSize();
  const rad = (Number(rotation || 0) % 360) * Math.PI / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const points = [
    {x: 0, y: 0},
    {x: size.width * zoom, y: 0},
    {x: size.width * zoom, y: size.height * zoom},
    {x: 0, y: size.height * zoom},
  ].map(p => ({x: p.x * cos - p.y * sin, y: p.x * sin + p.y * cos}));
  const xs = points.map(p => p.x);
  const ys = points.map(p => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  return {minX, minY, width: maxX - minX, height: maxY - minY};
}

function histologyViewportCenter() {
  const area = document.getElementById('histologyAnalysisPlotArea');
  return {
    x: Math.max(1, area?.clientWidth || 1) / 2,
    y: Math.max(1, area?.clientHeight || 1) / 2,
  };
}

function histologyFitScale() {
  const area = document.getElementById('histologyAnalysisPlotArea');
  if (!area) return 1;
  const size = histologyPreviewSize();
  const rotated = Math.abs(_histologyView.rotation) % 180 === 90
    ? {width: size.height, height: size.width}
    : size;
  const availW = Math.max(80, area.clientWidth - 24);
  const availH = Math.max(80, area.clientHeight - 24);
  return histologyClamp(Math.min(availW / rotated.width, availH / rotated.height, 1), HISTOLOGY_ZOOM_MIN, HISTOLOGY_ZOOM_MAX);
}

function histologyCenterPreview() {
  const area = document.getElementById('histologyAnalysisPlotArea');
  if (!area || !_histologyAnalysisImage) return;
  const bounds = histologyRotatedBounds(_histologyView.zoom, _histologyView.rotation);
  _histologyView.panX = Math.round((area.clientWidth - bounds.width) / 2 - bounds.minX);
  _histologyView.panY = Math.round((area.clientHeight - bounds.height) / 2 - bounds.minY);
}

function histologyLocalToViewport(point, zoom, rotation) {
  const rad = (Number(rotation || 0) % 360) * Math.PI / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const x = Number(point.x || 0) * zoom;
  const y = Number(point.y || 0) * zoom;
  return {
    x: x * cos - y * sin + _histologyView.panX,
    y: x * sin + y * cos + _histologyView.panY,
  };
}

function histologyViewportToLocal(viewX, viewY, allowOutside) {
  const size = histologyPreviewSize();
  const dx = Number(viewX || 0) - _histologyView.panX;
  const dy = Number(viewY || 0) - _histologyView.panY;
  const rad = (_histologyView.rotation % 360) * Math.PI / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const x = (dx * cos + dy * sin) / Math.max(HISTOLOGY_ZOOM_MIN, _histologyView.zoom);
  const y = (-dx * sin + dy * cos) / Math.max(HISTOLOGY_ZOOM_MIN, _histologyView.zoom);
  if (!allowOutside && (x < 0 || y < 0 || x > size.width || y > size.height)) return null;
  return {
    x: histologyClamp(x, 0, size.width),
    y: histologyClamp(y, 0, size.height),
  };
}

function histologyApplyViewTransform() {
  const surface = document.getElementById('histologyAnalysisSurface');
  const img = document.getElementById('histologyAnalysisImg');
  const canvas = document.getElementById('histologyAnalysisCanvas');
  if (!surface || !img || !canvas) return;
  const size = histologyPreviewSize();
  surface.style.width = `${size.width}px`;
  surface.style.height = `${size.height}px`;
  surface.style.transform = `translate(${_histologyView.panX}px, ${_histologyView.panY}px) rotate(${_histologyView.rotation}deg) scale(${_histologyView.zoom})`;
  surface.style.cursor = _histologyView.panMode ? (_histologyView.dragging ? 'grabbing' : 'grab') : 'crosshair';
  img.style.width = `${size.width}px`;
  img.style.height = `${size.height}px`;
  canvas.width = size.width;
  canvas.height = size.height;
  canvas.style.width = `${size.width}px`;
  canvas.style.height = `${size.height}px`;
  canvas.hidden = false;
  drawHistologyRois();
  updateHistologyViewControls();
}

function updateHistologyViewControls() {
  const slider = document.getElementById('histologyZoomSlider');
  const label = document.getElementById('histologyZoomLabel');
  const pan = document.getElementById('btnHistologyPan');
  if (slider) slider.value = String(Math.round(_histologyView.zoom * 100));
  if (label) label.textContent = `${Math.round(_histologyView.zoom * 100)}%`;
  if (pan) pan.classList.toggle('active', !!_histologyView.panMode);
}

function histologySetZoom(nextZoom, anchor) {
  if (!_histologyAnalysisImage) return;
  const area = document.getElementById('histologyAnalysisPlotArea');
  const zoom = histologyClamp(nextZoom, HISTOLOGY_ZOOM_MIN, HISTOLOGY_ZOOM_MAX);
  const target = anchor || histologyViewportCenter();
  const local = histologyViewportToLocal(target.x, target.y, true);
  _histologyView.zoom = zoom;
  if (local && area) {
    const previousPan = {x: _histologyView.panX, y: _histologyView.panY};
    _histologyView.panX = 0;
    _histologyView.panY = 0;
    const mapped = histologyLocalToViewport(local, zoom, _histologyView.rotation);
    _histologyView.panX = Math.round(target.x - mapped.x);
    _histologyView.panY = Math.round(target.y - mapped.y);
    if (!Number.isFinite(_histologyView.panX) || !Number.isFinite(_histologyView.panY)) {
      _histologyView.panX = previousPan.x;
      _histologyView.panY = previousPan.y;
    }
  }
  histologyApplyViewTransform();
}

function histologySetZoomFromSlider() {
  const slider = document.getElementById('histologyZoomSlider');
  const value = Number(slider?.value || 100) / 100;
  histologySetZoom(value);
}

function histologyZoomStep(multiplier) {
  histologySetZoom(_histologyView.zoom * Number(multiplier || 1));
}

function histologyFitPreview() {
  if (!_histologyAnalysisImage) return;
  _histologyView.zoom = histologyFitScale();
  histologyCenterPreview();
  histologyApplyViewTransform();
}

function histologyActualSize() {
  if (!_histologyAnalysisImage) return;
  _histologyView.zoom = 1;
  histologyCenterPreview();
  histologyApplyViewTransform();
}

function histologyRotatePreview(delta) {
  if (!_histologyAnalysisImage) return;
  const next = (_histologyView.rotation + Number(delta || 0)) % 360;
  _histologyView.rotation = (next + 360) % 360;
  histologyCenterPreview();
  histologyApplyViewTransform();
}

function histologyTogglePanMode() {
  _histologyView.panMode = !_histologyView.panMode;
  histologyApplyViewTransform();
}

function histologyResetView(fit) {
  _histologyView.zoom = fit ? histologyFitScale() : 1;
  _histologyView.rotation = 0;
  _histologyView.panMode = false;
  _histologyView.dragging = false;
  _histologyView.dragMoved = false;
  histologyCenterPreview();
  histologyApplyViewTransform();
}

function loadHistologyDataProject() {
  const projectPath = histologyDataProjectPath();
  if (!projectPath) {
    histologySetAnalysisStatus('Select a histology project first', 'error');
    return;
  }
  const pathError = histologyAnalysisProjectPathError(projectPath);
  if (pathError) {
    _histologyProject = null;
    _histologyProjectEntries = [];
    _histologyProjectEntryId = '';
    _histologyAnalysisImage = null;
    _histologyAnalysisRois = [];
    _histologyActivePolygon = null;
    renderHistologyAnalysisProjectImageList();
    renderHistologyRoiList();
    renderHistologyAnalysisResults(null);
    clearHistologyAnalysisImage('Load a DataProcess histology project to begin');
    histologySetAnalysisStatus(pathError, 'error');
    updateHistologyActionState();
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

function loadHistologyFileImage() {
  const imagePath = histologyAnalysisImagePath();
  const pathError = histologyAnalysisFilePathError(imagePath);
  if (pathError) {
    histologySetAnalysisStatus(pathError, 'error');
    updateHistologyActionState();
    return;
  }
  _histologyProjectEntryId = '';
  _histologyActivePolygon = null;
  _histologyAnalysisImage = null;
  _histologyAnalysisRois = [];
  renderHistologyAnalysisProjectImageList();
  renderHistologyRoiList();
  renderHistologyAnalysisResults(null);
  clearHistologyAnalysisImage('Loading image preview...');
  btnBusy('btnLoadHistologyFileImage', true, 'Loading...');
  histologySetAnalysisStatus('Loading single-image preview...', 'loading');
  api('/api/histology/file/image_preview', {
    image_path: imagePath,
    max_side: 1600,
  }).then(d => {
    btnBusy('btnLoadHistologyFileImage', false, 'Load Image');
    if (d.error) throw new Error(d.error);
    _histologyAnalysisImage = Object.assign({}, d, {source_mode: 'file'});
    _histologyAnalysisRois = [];
    _histologyPolygonCounter = 1;
    setHistologyRoiLabel('ROI 1');
    renderHistologyRoiList();
    loadHistologyAnalysisImage(_histologyAnalysisImage);
    document.getElementById('histologyAnalysisMeta').textContent =
      `${d.image_name || 'Single image'} · ${d.preview_width}x${d.preview_height} · ${d.backend} · test file`;
    histologySetAnalysisStatus('Single image loaded for quick ROI testing', 'ok');
    updateHistologyActionState();
  }).catch(e => {
    btnBusy('btnLoadHistologyFileImage', false, 'Load Image');
    histologySetAnalysisStatus('Error: ' + e.message, 'error');
    clearHistologyAnalysisImage('Preview failed');
    updateHistologyActionState();
  });
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
    _histologyAnalysisImage = Object.assign({}, d, {source_mode: 'project'});
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
  _histologyView.panMode = false;
  _histologyView.dragging = false;
  _histologyView.zoom = 1;
  _histologyView.rotation = 0;
  _histologyView.panX = 0;
  _histologyView.panY = 0;
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
  updateHistologyViewControls();
}

function loadHistologyAnalysisImage(payload) {
  const img = document.getElementById('histologyAnalysisImg');
  if (!img || !payload.img) return;
  const surface = document.getElementById('histologyAnalysisSurface');
  const empty = document.getElementById('histologyAnalysisEmpty');
  if (surface) surface.hidden = false;
  if (empty) empty.hidden = true;
  img.onload = () => histologyResetView(true);
  img.hidden = false;
  img.src = 'data:image/png;base64,' + payload.img;
}

function resizeHistologyAnalysisCanvas() {
  if (!_histologyAnalysisImage) return;
  histologyCenterPreview();
  histologyApplyViewTransform();
}

function histologyCanvasPoint(event) {
  const area = document.getElementById('histologyAnalysisPlotArea');
  if (!area) return null;
  const rect = area.getBoundingClientRect();
  return histologyViewportToLocal(event.clientX - rect.left, event.clientY - rect.top, false);
}

function nativeToHistologyCanvas(point) {
  const size = histologyPreviewSize();
  return {
    x: histologyClamp(Number(point.x || 0), 0, size.width),
    y: histologyClamp(Number(point.y || 0), 0, size.height),
  };
}

function setupHistologyAnalysisCanvas() {
  const canvas = document.getElementById('histologyAnalysisCanvas');
  if (!canvas || canvas.dataset.bound === '1') return;
  canvas.dataset.bound = '1';
  canvas.addEventListener('click', event => {
    if (_histologyView.panMode || _histologyView.dragMoved) return;
    if (!_histologyActivePolygon) return;
    const point = histologyCanvasPoint(event);
    if (!point) return;
    _histologyActivePolygon.points.push(point);
    drawHistologyRois();
  });
  canvas.addEventListener('dblclick', event => {
    event.preventDefault();
    if (_histologyView.panMode) return;
    finishHistologyPolygon();
  });
  canvas.addEventListener('mousedown', event => {
    if (!_histologyView.panMode || !_histologyAnalysisImage || event.button !== 0) return;
    event.preventDefault();
    _histologyView.dragging = true;
    _histologyView.dragMoved = false;
    _histologyView.dragStartX = event.clientX;
    _histologyView.dragStartY = event.clientY;
    _histologyView.dragPanX = _histologyView.panX;
    _histologyView.dragPanY = _histologyView.panY;
    histologyApplyViewTransform();
  });
  window.addEventListener('mousemove', event => {
    if (!_histologyView.dragging) return;
    const dx = event.clientX - _histologyView.dragStartX;
    const dy = event.clientY - _histologyView.dragStartY;
    if (Math.abs(dx) + Math.abs(dy) > 2) _histologyView.dragMoved = true;
    _histologyView.panX = Math.round(_histologyView.dragPanX + dx);
    _histologyView.panY = Math.round(_histologyView.dragPanY + dy);
    histologyApplyViewTransform();
  });
  window.addEventListener('mouseup', () => {
    if (!_histologyView.dragging) return;
    _histologyView.dragging = false;
    setTimeout(() => {_histologyView.dragMoved = false;}, 0);
    histologyApplyViewTransform();
  });
  window.addEventListener('resize', () => requestAnimationFrame(resizeHistologyAnalysisCanvas));
  const area = document.getElementById('histologyAnalysisPlotArea');
  if (area && area.dataset.boundWheel !== '1') {
    area.dataset.boundWheel = '1';
    area.addEventListener('wheel', event => {
      if (!_histologyAnalysisImage) return;
      event.preventDefault();
      const rect = area.getBoundingClientRect();
      const factor = event.deltaY > 0 ? 0.9 : 1.1;
      histologySetZoom(_histologyView.zoom * factor, {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
    }, {passive: false});
  }
}

function nextHistologyRoiColor(index) {
  const token = HISTOLOGY_ROI_COLOR_TOKENS[index % HISTOLOGY_ROI_COLOR_TOKENS.length];
  return histologyThemeColor(token, histologyRoiFallbackColor());
}

function startHistologyPolygon() {
  if (!_histologyAnalysisImage) {
    histologySetAnalysisStatus('Load an analysis image first', 'error');
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
  const modeText = _histologyAnalysisImage?.source_mode === 'file'
    ? 'ROI added. Analyze to test this file; load a project if you need to save ROI.'
    : 'ROI added. Save or analyze to write it into the DataProcess project.';
  histologySetAnalysisStatus(modeText, 'ok');
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
  if (_histologyAnalysisImage?.source_mode === 'file') {
    histologySetAnalysisStatus('Single-image test mode does not save ROI. Load a DataProcess project to save ROI.', 'error');
    updateHistologyActionState();
    return;
  }
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
  const isFileMode = _histologyAnalysisImage?.source_mode === 'file';
  if (!isFileMode && !_histologyProjectEntryId) {
    histologySetAnalysisStatus('Select a project image first', 'error');
    return;
  }
  if (!_histologyAnalysisRois.length) {
    histologySetAnalysisStatus('Draw at least one ROI first', 'error');
    return;
  }
  btnBusy('btnAnalyzeHistology', true, 'Analyzing...');
  histologySetAnalysisStatus('Analyzing SMA and macrophage thresholds...', 'loading');
  const endpoint = isFileMode
    ? '/api/histology/file/analysis/run_job'
    : '/api/histology/project/analysis/run_job';
  const body = isFileMode ? {
    image_path: _histologyAnalysisImage?.image_path || histologyAnalysisImagePath(),
    rois: _histologyAnalysisRois,
    parameters: histologyAnalysisParameters(),
  } : {
    project_path: histologyDataProjectPath(),
    entry_id: _histologyProjectEntryId,
    rois: _histologyAnalysisRois,
    parameters: histologyAnalysisParameters(),
  };
  dpRunJobEndpoint(endpoint, body, {
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
    if (!isFileMode) refreshLoadedProjectEntryCounts(d);
    const doneText = isFileMode
      ? `Analyzed ${_histologyAnalysisRois.length} ROI in single-image test mode`
      : `Analyzed ${_histologyAnalysisRois.length} ROI; results saved to the DataProcess project`;
    histologySetAnalysisStatus(doneText, 'ok');
    toast(isFileMode ? 'Histology analysis complete' : 'Histology analysis saved to project');
    recordRunHistory({
      view: 'histology_analysis',
      title: isFileMode ? 'Histology File SMA Macrophage Analysis' : 'Histology Project SMA Macrophage Analysis',
      status: 'ok',
      project_root: histologyProjectRoot(),
      input_files: [{path: d.analysis?.image_path || _histologyAnalysisImage?.image_path || '', role: isFileMode ? 'histology_file_image' : 'histology_project_image'}],
      outputs: dpAsPathRecords([d.analysis_path, d.geojson_path, d.summary_path, d.project_path, d.cache_dir], 'histology_analysis_output'),
      parameters: Object.assign({entry_id: _histologyProjectEntryId, image_path: _histologyAnalysisImage?.image_path || '', rois: _histologyAnalysisRois}, histologyAnalysisParameters()),
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
  document.getElementById('histologyImagePath')?.addEventListener('input', updateHistologyActionState);
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
  'histologyAnalysisImagePath',
  'histologyCurrentRoiLabel',
  'histologyDataProjectPath',
  'histologyActualSize',
  'histologyFitPreview',
  'histologyProjectRoot',
  'histologyAnalysisProjectPath',
  'histologyRoiLabelElements',
  'histologyRotatePreview',
  'histologySetZoomFromSlider',
  'histologyTogglePanMode',
  'histologyZoomStep',
  'loadHistologyAnalysisImage',
  'loadHistologyDataProject',
  'loadHistologyFileImage',
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
