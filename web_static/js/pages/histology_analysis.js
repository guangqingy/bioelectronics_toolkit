let _histologyProject = null;
let _histologyProjectEntries = [];
let _histologyProjectEntryId = '';
let _histologyViewSelectedChannels = [];
let _histologyAnalysisImage = null;
let _histologyAnalysisRois = [];
let _histologyLatestBatchAnalysis = null;
let _histologyActivePolygon = null;
let _histologyPolygonCounter = 1;
let _histologyView = {
  zoom: 1,
  rotation: 0,
  panX: 0,
  panY: 0,
  panMode: false,
  spacePan: false,
  pointerInside: false,
  pointerId: null,
  dragging: false,
  dragMoved: false,
  dragStartX: 0,
  dragStartY: 0,
  dragPanX: 0,
  dragPanY: 0,
  pinchStartZoom: 0,
  pinchAnchor: null,
};
let _histologyDetailPreview = {
  seq: 0,
  timer: null,
  loading: false,
  key: '',
};
let _histologyRoiDebug = {
  seq: 0,
  timer: null,
  loading: false,
  baselineParameters: null,
};

const HISTOLOGY_ROI_COLOR_TOKENS = ['--blue', '--success', '--error', '--warning', '--blue-hover', '--error-text-strong'];
const HISTOLOGY_ZOOM_MIN = 0.05;
const HISTOLOGY_ZOOM_MAX = 8;
const HISTOLOGY_DRAG_THRESHOLD_PX = 3;
const HISTOLOGY_WHEEL_ZOOM_SENSITIVITY = 0.003;
const HISTOLOGY_DETAIL_ZOOM_MIN = 1.35;
const HISTOLOGY_CHANNEL_ORDER = ['Hoechst', 'DAPI', 'FITC', 'Cy5', 'Mito', 'Brightfield', 'BF', 'Transmitted', 'Overview'];
const HISTOLOGY_FLUORESCENCE_CHANNELS = new Set(['hoechst', 'dapi', 'fitc', 'cy5', 'mito']);

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
  histologySetButtonDisabled('btnAnalyzeHistologyProject', !hasFolder);
  histologySetButtonDisabled('btnPreviewHistologyRoiDebug', !isProjectImage || drawing || !hasRois);
  const debugSelect = document.getElementById('histologyDebugRoiSelect');
  if (debugSelect) debugSelect.disabled = !isProjectImage || drawing || !hasRois;
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
  document.querySelectorAll('#histologyChannelView input[type="checkbox"]').forEach(input => {
    input.disabled = !isProjectImage || drawing;
  });
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

function histologyCurrentProjectEntry() {
  return _histologyProjectEntries.find(entry => String(entry.entry_id) === String(_histologyProjectEntryId)) || null;
}

function histologyOrderedChannels(channels) {
  const clean = (Array.isArray(channels) ? channels : [])
    .map(channel => String(channel || '').trim())
    .filter(Boolean);
  const ordered = [];
  HISTOLOGY_CHANNEL_ORDER.forEach(name => {
    clean.forEach(channel => {
      if (channel === name && !ordered.includes(channel)) ordered.push(channel);
    });
  });
  clean.forEach(channel => {
    if (!ordered.includes(channel)) ordered.push(channel);
  });
  return ordered;
}

function histologyProjectEntryChannels(entry) {
  const imageFiles = entry?.image_files && typeof entry.image_files === 'object' ? entry.image_files : {};
  return histologyOrderedChannels(
    Object.entries(imageFiles)
      .filter(([, path]) => String(path || '').trim())
      .map(([channel]) => channel)
  );
}

function histologyDefaultViewChannels(channels) {
  const ordered = histologyOrderedChannels(channels);
  const preferred = ['FITC', 'Cy5', 'Hoechst', 'DAPI', 'Mito'].find(channel => ordered.includes(channel));
  if (preferred) return [preferred];
  const fluorescence = ordered.find(channel => HISTOLOGY_FLUORESCENCE_CHANNELS.has(channel.toLowerCase()));
  return fluorescence ? [fluorescence] : ordered.slice(0, 1);
}

function histologySelectedViewChannels() {
  const inputs = Array.from(document.querySelectorAll('#histologyChannelView input[type="checkbox"]'));
  const checked = inputs
    .filter(input => input.checked)
    .map(input => String(input.value || '').trim())
    .filter(Boolean);
  return checked.length ? checked : _histologyViewSelectedChannels.slice();
}

function renderHistologyChannelView(channels, selected) {
  const el = document.getElementById('histologyChannelView');
  if (!el) return;
  const ordered = histologyOrderedChannels(channels);
  if (ordered.length <= 1) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  const selectedSet = new Set((Array.isArray(selected) ? selected : []).map(channel => String(channel)));
  el.hidden = false;
  el.innerHTML = ordered.map(channel => {
    const checked = selectedSet.has(channel) ? ' checked' : '';
    return `
      <label class="histology-channel-chip" title="Show ${escHtml(channel)}">
        <input type="checkbox" value="${escHtml(channel)}" onchange="DP.page.histologyHandleViewChannelChanged(event)"${checked}>
        <span>${escHtml(channel)}</span>
      </label>`;
  }).join('');
}

function histologySetViewChannelsForEntry(entry, preserveExisting) {
  const channels = histologyProjectEntryChannels(entry);
  const available = new Set(channels);
  const preserved = preserveExisting
    ? _histologyViewSelectedChannels.filter(channel => available.has(channel))
    : [];
  _histologyViewSelectedChannels = preserved.length ? preserved : histologyDefaultViewChannels(channels);
  renderHistologyChannelView(channels, _histologyViewSelectedChannels);
}

function histologyHideChannelView() {
  _histologyViewSelectedChannels = [];
  renderHistologyChannelView([], []);
}

function histologyHandleViewChannelChanged(event) {
  const inputs = Array.from(document.querySelectorAll('#histologyChannelView input[type="checkbox"]'));
  if (!inputs.length) return;
  let selected = inputs
    .filter(input => input.checked)
    .map(input => String(input.value || '').trim())
    .filter(Boolean);
  if (!selected.length) {
    const target = event?.target && inputs.includes(event.target) ? event.target : inputs[0];
    target.checked = true;
    selected = [String(target.value || '').trim()].filter(Boolean);
  }
  const previous = _histologyViewSelectedChannels.join('\u0001');
  _histologyViewSelectedChannels = selected;
  if (previous === selected.join('\u0001')) return;
  if (_histologyProjectEntryId) {
    selectHistologyProjectImage(_histologyProjectEntryId, {preserveChannels: true, preserveLocalRois: true});
  }
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

function histologyPreviewWarningText(payload) {
  const warnings = (payload?.warnings || [])
    .map(item => String(item || '').trim())
    .filter(Boolean);
  if (!warnings.length) return '';
  const shown = warnings.slice(0, 2).join(' · ');
  return warnings.length > 2 ? `${shown} · ${warnings.length - 2} more warning(s)` : shown;
}

function histologyPreviewMetaText(payload, fallbackName, suffix) {
  const parts = [
    fallbackName,
    `${payload.preview_width}x${payload.preview_height}`,
    payload.backend,
  ];
  const channels = Array.isArray(payload.preview_channels) && payload.preview_channels.length
    ? `channels: ${payload.preview_channels.join(', ')}`
    : '';
  if (channels) parts.push(channels);
  if (suffix) parts.push(suffix);
  return parts.filter(Boolean).join(' · ');
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

function histologyNativeSize() {
  return {
    width: Math.max(1, Number(_histologyAnalysisImage?.width || _histologyAnalysisImage?.preview_width || 1)),
    height: Math.max(1, Number(_histologyAnalysisImage?.height || _histologyAnalysisImage?.preview_height || 1)),
  };
}

function histologyPreviewToNative(point) {
  const preview = histologyPreviewSize();
  const native = histologyNativeSize();
  return {
    x: histologyClamp(Number(point?.x || 0) * native.width / Math.max(1, preview.width), 0, native.width),
    y: histologyClamp(Number(point?.y || 0) * native.height / Math.max(1, preview.height), 0, native.height),
  };
}

function histologyNativeToPreview(point) {
  const preview = histologyPreviewSize();
  const native = histologyNativeSize();
  return {
    x: histologyClamp(Number(point?.x || 0) * preview.width / Math.max(1, native.width), 0, preview.width),
    y: histologyClamp(Number(point?.y || 0) * preview.height / Math.max(1, native.height), 0, preview.height),
  };
}

function histologyPreviewScale() {
  const preview = histologyPreviewSize();
  const native = histologyNativeSize();
  return {
    x: native.width / Math.max(1, preview.width),
    y: native.height / Math.max(1, preview.height),
  };
}

function histologyNormalizeIncomingRois(rois) {
  const items = Array.isArray(rois) ? rois : [];
  const preview = histologyPreviewSize();
  const native = histologyNativeSize();
  const downsampled = native.width > preview.width + 1 || native.height > preview.height + 1;
  return items.map(roi => {
    const copy = Object.assign({}, roi || {});
    const points = Array.isArray(copy.points) ? copy.points : [];
    const maxX = points.reduce((m, p) => Math.max(m, Number(p?.x || 0)), 0);
    const maxY = points.reduce((m, p) => Math.max(m, Number(p?.y || 0)), 0);
    const space = String(copy.coordinate_space || copy.coordinateSpace || '').toLowerCase();
    const shouldScale = downsampled && (space === 'preview' || (!space && maxX <= preview.width + 1 && maxY <= preview.height + 1));
    copy.coordinate_space = 'native';
    copy.points = points.map(point => {
      const p = {x: Number(point?.x || 0), y: Number(point?.y || 0)};
      return shouldScale ? histologyPreviewToNative(p) : {
        x: histologyClamp(p.x, 0, native.width),
        y: histologyClamp(p.y, 0, native.height),
      };
    });
    return copy;
  });
}

function histologyRoisForApi() {
  return _histologyAnalysisRois.map(roi => Object.assign({}, roi, {
    coordinate_space: 'native',
    points: (Array.isArray(roi.points) ? roi.points : []).map(point => ({
      x: Number(point.x || 0),
      y: Number(point.y || 0),
    })),
  }));
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
  return {minX, maxX, minY, maxY, width: maxX - minX, height: maxY - minY};
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
  _histologyView.panX = (area.clientWidth - bounds.width) / 2 - bounds.minX;
  _histologyView.panY = (area.clientHeight - bounds.height) / 2 - bounds.minY;
}

function histologyClampPan() {
  const area = document.getElementById('histologyAnalysisPlotArea');
  if (!area || !_histologyAnalysisImage) return;
  const bounds = histologyRotatedBounds(_histologyView.zoom, _histologyView.rotation);
  const margin = Math.max(48, Math.min(160, Math.min(area.clientWidth, area.clientHeight) * 0.22));
  const minPanX = -bounds.maxX + margin;
  const maxPanX = area.clientWidth - bounds.minX - margin;
  const minPanY = -bounds.maxY + margin;
  const maxPanY = area.clientHeight - bounds.minY - margin;
  _histologyView.panX = histologyClamp(_histologyView.panX, Math.min(minPanX, maxPanX), Math.max(minPanX, maxPanX));
  _histologyView.panY = histologyClamp(_histologyView.panY, Math.min(minPanY, maxPanY), Math.max(minPanY, maxPanY));
}

function histologyPanBy(dx, dy) {
  if (!_histologyAnalysisImage) return;
  _histologyView.panX += Number(dx || 0);
  _histologyView.panY += Number(dy || 0);
  histologyApplyViewTransform();
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
  histologyClampPan();
  const size = histologyPreviewSize();
  const cursor = histologyHandToolActive() || !_histologyActivePolygon
    ? (_histologyView.dragging ? 'grabbing' : 'grab')
    : 'crosshair';
  surface.style.width = `${size.width}px`;
  surface.style.height = `${size.height}px`;
  surface.style.transform = `translate(${_histologyView.panX}px, ${_histologyView.panY}px) rotate(${_histologyView.rotation}deg) scale(${_histologyView.zoom})`;
  surface.style.cursor = cursor;
  img.style.width = `${size.width}px`;
  img.style.height = `${size.height}px`;
  canvas.width = size.width;
  canvas.height = size.height;
  canvas.style.width = `${size.width}px`;
  canvas.style.height = `${size.height}px`;
  canvas.style.cursor = cursor;
  canvas.hidden = false;
  drawHistologyRois();
  updateHistologyViewControls();
  histologyScheduleDetailPreview(_histologyView.dragging ? 360 : 220);
}

function updateHistologyViewControls() {
  const slider = document.getElementById('histologyZoomSlider');
  const label = document.getElementById('histologyZoomLabel');
  const pan = document.getElementById('btnHistologyPan');
  if (slider) slider.value = String(Math.round(_histologyView.zoom * 100));
  if (label) label.textContent = `${Math.round(_histologyView.zoom * 100)}%`;
  if (pan) {
    pan.classList.toggle('active', !!_histologyView.panMode);
    pan.setAttribute('aria-pressed', _histologyView.panMode ? 'true' : 'false');
  }
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
    _histologyView.panX = target.x - mapped.x;
    _histologyView.panY = target.y - mapped.y;
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
  _histologyView.dragging = false;
  _histologyView.pointerId = null;
  histologyApplyViewTransform();
}

function histologyResetView(fit) {
  histologyClearDetailPreview();
  _histologyView.zoom = fit ? histologyFitScale() : 1;
  _histologyView.rotation = 0;
  _histologyView.panMode = false;
  _histologyView.spacePan = false;
  _histologyView.pointerId = null;
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
    histologyHideChannelView();
    _histologyRoiDebug.baselineParameters = null;
    _histologyAnalysisImage = null;
    _histologyAnalysisRois = [];
    _histologyLatestBatchAnalysis = null;
    _histologyActivePolygon = null;
    renderHistologyAnalysisProjectImageList();
    renderHistologyRoiList();
    clearHistologyRoiDebug('Load a project image and ROI to tune parameters.');
    renderHistologyAnalysisResults(null);
    renderHistologyProjectBatchResults(null);
    clearHistologyAnalysisImage('Load a DataProcess histology project to begin');
    histologySetAnalysisStatus(pathError, 'error');
    updateHistologyActionState();
    return;
  }
  histologyHideChannelView();
  _histologyRoiDebug.baselineParameters = null;
  _histologyAnalysisImage = null;
  _histologyAnalysisRois = [];
  _histologyLatestBatchAnalysis = null;
  _histologyActivePolygon = null;
  renderHistologyRoiList();
  clearHistologyRoiDebug('Select a project image and ROI to tune parameters.');
  renderHistologyProjectBatchResults(null);
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
      if (_histologyProjectEntries.length) {
        selectHistologyProjectImage(_histologyProjectEntries[0].entry_id, {preserveChannels: false});
      }
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
  histologyHideChannelView();
  _histologyRoiDebug.baselineParameters = null;
  _histologyActivePolygon = null;
  _histologyAnalysisImage = null;
  _histologyAnalysisRois = [];
  _histologyLatestBatchAnalysis = null;
  renderHistologyAnalysisProjectImageList();
  renderHistologyRoiList();
  clearHistologyRoiDebug('ROI tuning is available for DataProcess project images.');
  renderHistologyAnalysisResults(null);
  renderHistologyProjectBatchResults(null);
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
      histologyPreviewMetaText(d, d.image_name || 'Single image', 'test file');
    const warning = histologyPreviewWarningText(d);
    histologySetAnalysisStatus(warning || 'Single image loaded for quick ROI testing', warning ? 'warning' : 'ok');
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
    const imageFiles = entry.image_files && typeof entry.image_files === 'object' ? entry.image_files : {};
    const channels = Object.entries(imageFiles)
      .filter(([, path]) => String(path || '').trim())
      .map(([channel]) => channel);
    const channelText = channels.length ? ` · channels: ${channels.join(', ')}` : '';
    const warnings = Array.isArray(entry.warnings) ? entry.warnings : [];
    const needsRebuild = warnings.some(item => String(item || '').includes('Legacy ETS conversion'));
    const rebuild = needsRebuild ? ' · channel rebuild needed' : '';
    const detail = [entry.case_name || '', entry.case_relative_path || entry.relative_path || entry.source_path || '']
      .filter(Boolean).join(' · ');
    return `
      <div class="file-item${active}" data-entry-id="${escHtml(entry.entry_id)}" onclick="DP.page.selectHistologyProjectImage('${escHtml(entry.entry_id)}')">
        <div class="histology-file-title">${escHtml(entry.image_name || entry.entry_id)}</div>
        <div class="histology-file-subline">${escHtml(counts + role + missing + rebuild + channelText)}</div>
        <div class="histology-file-path">${escHtml(detail)}</div>
      </div>`;
  }).join('');
}

function selectHistologyProjectImage(entryId, options) {
  if (!entryId) return;
  const projectPath = histologyDataProjectPath();
  if (!projectPath) {
    histologySetAnalysisStatus('Select a histology project first', 'error');
    return;
  }
  const opts = options || {};
  const previousEntryId = _histologyProjectEntryId;
  const preserveLocalRois = !!opts.preserveLocalRois && String(previousEntryId) === String(entryId);
  const localRois = preserveLocalRois ? _histologyAnalysisRois.map(roi => Object.assign({}, roi)) : null;
  _histologyProjectEntryId = String(entryId);
  _histologyRoiDebug.baselineParameters = null;
  histologySetViewChannelsForEntry(histologyCurrentProjectEntry(), opts.preserveChannels !== false);
  _histologyActivePolygon = null;
  _histologyAnalysisImage = null;
  _histologyAnalysisRois = localRois || [];
  renderHistologyAnalysisProjectImageList();
  renderHistologyRoiList();
  clearHistologyRoiDebug('Loading ROI tuning preview...');
  clearHistologyAnalysisImage('Loading project image preview...');
  histologySetAnalysisStatus('Loading project image preview...', 'loading');
  api('/api/histology/project/image_preview', {
    project_path: projectPath,
    entry_id: _histologyProjectEntryId,
    max_side: 1600,
    selected_channels: histologySelectedViewChannels(),
  }).then(d => {
    if (d.error) throw new Error(d.error);
    _histologyAnalysisImage = Object.assign({}, d, {source_mode: 'project'});
    _histologyAnalysisRois = localRois || histologyNormalizeIncomingRois(d.rois);
    _histologyPolygonCounter = Math.max(1, _histologyAnalysisRois.length + 1);
    renderHistologyRoiList();
    const latestAnalysis = (d.analyses || []).slice(-1)[0] || null;
    applyHistologyAnalysisParameters(latestAnalysis?.parameters || null);
    _histologyRoiDebug.baselineParameters = Object.assign({}, histologyAnalysisParameters());
    renderHistologyAnalysisResults(latestAnalysis);
    loadHistologyAnalysisImage(d);
    document.getElementById('histologyAnalysisMeta').textContent =
      histologyPreviewMetaText(d, d.image_name || entryId, '');
    const warning = histologyPreviewWarningText(d);
    histologySetAnalysisStatus(warning || 'Image preview loaded', warning ? 'warning' : 'ok');
    scheduleHistologyRoiDebugPreview(450);
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
  _histologyView.spacePan = false;
  _histologyView.pointerId = null;
  _histologyView.dragging = false;
  _histologyView.dragMoved = false;
  _histologyView.zoom = 1;
  _histologyView.rotation = 0;
  _histologyView.panX = 0;
  _histologyView.panY = 0;
  histologyClearDetailPreview();
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
  const local = histologyViewportToLocal(event.clientX - rect.left, event.clientY - rect.top, false);
  return local ? histologyPreviewToNative(local) : null;
}

function histologyAreaPointFromEvent(event) {
  const area = document.getElementById('histologyAnalysisPlotArea');
  if (!area || !event) return histologyViewportCenter();
  const rect = area.getBoundingClientRect();
  const x = Number.isFinite(event.clientX) ? event.clientX - rect.left : rect.width / 2;
  const y = Number.isFinite(event.clientY) ? event.clientY - rect.top : rect.height / 2;
  return {
    x: histologyClamp(x, 0, Math.max(1, rect.width)),
    y: histologyClamp(y, 0, Math.max(1, rect.height)),
  };
}

function histologyWheelDeltaPixels(event) {
  const mode = Number(event.deltaMode || 0);
  const scale = mode === 1 ? 16 : (mode === 2 ? 320 : 1);
  return {
    x: Number(event.deltaX || 0) * scale,
    y: Number(event.deltaY || 0) * scale,
  };
}

function histologyIsTypingTarget(target) {
  if (!target) return false;
  const tag = String(target.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || !!target.isContentEditable;
}

function histologyHandToolActive() {
  return !!(_histologyView.panMode || _histologyView.spacePan);
}

function histologyPointerShouldPan(event) {
  if (!_histologyAnalysisImage) return false;
  if (histologyHandToolActive()) return true;
  if (!event) return false;
  if (event.button === 1 || event.button === 2) return true;
  return !_histologyActivePolygon && event.button === 0;
}

function histologyBeginPan(event, target) {
  if (!histologyPointerShouldPan(event)) return false;
  event.preventDefault();
  _histologyView.dragging = true;
  _histologyView.dragMoved = false;
  _histologyView.pointerId = event.pointerId;
  _histologyView.dragStartX = event.clientX;
  _histologyView.dragStartY = event.clientY;
  _histologyView.dragPanX = _histologyView.panX;
  _histologyView.dragPanY = _histologyView.panY;
  if (target && typeof target.setPointerCapture === 'function') {
    try { target.setPointerCapture(event.pointerId); } catch (e) {}
  }
  histologyApplyViewTransform();
  return true;
}

function histologyMovePan(event) {
  if (!_histologyView.dragging || _histologyView.pointerId !== event.pointerId) return;
  event.preventDefault();
  const dx = event.clientX - _histologyView.dragStartX;
  const dy = event.clientY - _histologyView.dragStartY;
  if (Math.abs(dx) + Math.abs(dy) > HISTOLOGY_DRAG_THRESHOLD_PX) _histologyView.dragMoved = true;
  _histologyView.panX = _histologyView.dragPanX + dx;
  _histologyView.panY = _histologyView.dragPanY + dy;
  histologyApplyViewTransform();
}

function histologyEndPan(event) {
  if (!_histologyView.dragging) return;
  if (event && _histologyView.pointerId !== null && _histologyView.pointerId !== event.pointerId) return;
  _histologyView.dragging = false;
  _histologyView.pointerId = null;
  setTimeout(() => {_histologyView.dragMoved = false;}, 0);
  histologyApplyViewTransform();
}

function histologyHandleWheel(event) {
  if (!_histologyAnalysisImage) return;
  event.preventDefault();
  const delta = histologyWheelDeltaPixels(event);
  const anchor = histologyAreaPointFromEvent(event);
  if (event.ctrlKey || event.metaKey || event.altKey) {
    const mainDelta = Math.abs(delta.y) >= Math.abs(delta.x) ? delta.y : delta.x;
    if (!mainDelta) return;
    const factor = histologyClamp(
      Math.exp(-mainDelta * HISTOLOGY_WHEEL_ZOOM_SENSITIVITY),
      0.72,
      1.38
    );
    histologySetZoom(_histologyView.zoom * factor, anchor);
    return;
  }
  let panDx = delta.x;
  let panDy = delta.y;
  if (event.shiftKey && Math.abs(panDx) < Math.abs(panDy)) {
    panDx = panDy;
    panDy = 0;
  }
  histologyPanBy(-panDx, -panDy);
}

function histologyHandleGestureStart(event) {
  if (!_histologyAnalysisImage) return;
  event.preventDefault();
  _histologyView.pinchStartZoom = _histologyView.zoom;
  _histologyView.pinchAnchor = histologyAreaPointFromEvent(event);
}

function histologyHandleGestureChange(event) {
  if (!_histologyAnalysisImage || !_histologyView.pinchStartZoom) return;
  event.preventDefault();
  const scale = Math.max(0.05, Number(event.scale || 1));
  histologySetZoom(_histologyView.pinchStartZoom * scale, _histologyView.pinchAnchor || histologyViewportCenter());
}

function histologyHandleGestureEnd(event) {
  if (event) event.preventDefault();
  _histologyView.pinchStartZoom = 0;
  _histologyView.pinchAnchor = null;
}

function histologyClearDetailPreview() {
  _histologyDetailPreview.seq += 1;
  _histologyDetailPreview.loading = false;
  _histologyDetailPreview.key = '';
  if (_histologyDetailPreview.timer) {
    clearTimeout(_histologyDetailPreview.timer);
    _histologyDetailPreview.timer = null;
  }
  const detail = document.getElementById('histologyAnalysisDetailImg');
  if (detail) {
    detail.hidden = true;
    detail.removeAttribute('src');
  }
}

function histologyVisibleNativeRegion() {
  const area = document.getElementById('histologyAnalysisPlotArea');
  if (!area || !_histologyAnalysisImage) return null;
  const corners = [
    {x: 0, y: 0},
    {x: area.clientWidth, y: 0},
    {x: area.clientWidth, y: area.clientHeight},
    {x: 0, y: area.clientHeight},
  ].map(point => histologyViewportToLocal(point.x, point.y, true)).filter(Boolean);
  if (!corners.length) return null;
  const nativeCorners = corners.map(histologyPreviewToNative);
  const native = histologyNativeSize();
  let x0 = Math.min(...nativeCorners.map(point => point.x));
  let y0 = Math.min(...nativeCorners.map(point => point.y));
  let x1 = Math.max(...nativeCorners.map(point => point.x));
  let y1 = Math.max(...nativeCorners.map(point => point.y));
  const padX = Math.max(32, (x1 - x0) * 0.18);
  const padY = Math.max(32, (y1 - y0) * 0.18);
  x0 = histologyClamp(x0 - padX, 0, native.width - 1);
  y0 = histologyClamp(y0 - padY, 0, native.height - 1);
  x1 = histologyClamp(x1 + padX, x0 + 1, native.width);
  y1 = histologyClamp(y1 + padY, y0 + 1, native.height);
  return {
    x: Math.floor(x0),
    y: Math.floor(y0),
    width: Math.max(1, Math.ceil(x1 - x0)),
    height: Math.max(1, Math.ceil(y1 - y0)),
  };
}

function histologyDetailPreviewNeeded() {
  if (!_histologyAnalysisImage || _histologyView.dragging) return false;
  const scale = histologyPreviewScale();
  return _histologyView.zoom >= HISTOLOGY_DETAIL_ZOOM_MIN && Math.max(scale.x, scale.y) > 1.08;
}

function histologyApplyDetailPreview(payload) {
  const detail = document.getElementById('histologyAnalysisDetailImg');
  if (!detail || !payload?.img) return;
  const preview = histologyPreviewSize();
  const native = histologyNativeSize();
  const x = Number(payload.region_x || 0);
  const y = Number(payload.region_y || 0);
  const width = Number(payload.region_width || 1);
  const height = Number(payload.region_height || 1);
  detail.style.left = `${x * preview.width / Math.max(1, native.width)}px`;
  detail.style.top = `${y * preview.height / Math.max(1, native.height)}px`;
  detail.style.width = `${width * preview.width / Math.max(1, native.width)}px`;
  detail.style.height = `${height * preview.height / Math.max(1, native.height)}px`;
  detail.hidden = false;
  detail.src = 'data:image/png;base64,' + payload.img;
}

function histologyScheduleDetailPreview(delayMs) {
  if (_histologyDetailPreview.timer) {
    clearTimeout(_histologyDetailPreview.timer);
    _histologyDetailPreview.timer = null;
  }
  if (!histologyDetailPreviewNeeded()) {
    const detail = document.getElementById('histologyAnalysisDetailImg');
    if (detail) detail.hidden = true;
    return;
  }
  _histologyDetailPreview.timer = setTimeout(() => {
    const region = histologyVisibleNativeRegion();
    if (!region) return;
    const key = [
      _histologyAnalysisImage?.source_mode || '',
      _histologyProjectEntryId || _histologyAnalysisImage?.image_path || '',
      Math.round(region.x / 8),
      Math.round(region.y / 8),
      Math.round(region.width / 8),
      Math.round(region.height / 8),
      Math.round(_histologyView.rotation),
      _histologyAnalysisImage?.source_mode === 'project' ? histologySelectedViewChannels().join(',') : '',
    ].join(':');
    if (_histologyDetailPreview.key === key) return;
    const seq = ++_histologyDetailPreview.seq;
    _histologyDetailPreview.loading = true;
    const isFileMode = _histologyAnalysisImage?.source_mode === 'file';
    const endpoint = isFileMode
      ? '/api/histology/file/image_region_preview'
      : '/api/histology/project/image_region_preview';
    const body = Object.assign({}, region, {max_side: 2200});
    if (isFileMode) {
      body.image_path = _histologyAnalysisImage?.image_path || histologyAnalysisImagePath();
    } else {
      body.project_path = histologyDataProjectPath();
      body.entry_id = _histologyProjectEntryId;
      body.selected_channels = histologySelectedViewChannels();
    }
    api(endpoint, body).then(d => {
      if (seq !== _histologyDetailPreview.seq) return;
      _histologyDetailPreview.loading = false;
      if (d.error) throw new Error(d.error);
      _histologyDetailPreview.key = key;
      histologyApplyDetailPreview(d);
    }).catch(() => {
      if (seq !== _histologyDetailPreview.seq) return;
      _histologyDetailPreview.loading = false;
    });
  }, Math.max(80, Number(delayMs || 220)));
}

function nativeToHistologyCanvas(point) {
  return histologyNativeToPreview(point);
}

function setupHistologyAnalysisCanvas() {
  const canvas = document.getElementById('histologyAnalysisCanvas');
  if (!canvas || canvas.dataset.bound === '1') return;
  canvas.dataset.bound = '1';
  canvas.addEventListener('click', event => {
    if (histologyHandToolActive() || _histologyView.dragMoved) return;
    if (!_histologyActivePolygon) return;
    const point = histologyCanvasPoint(event);
    if (!point) return;
    _histologyActivePolygon.points.push(point);
    drawHistologyRois();
  });
  canvas.addEventListener('dblclick', event => {
    event.preventDefault();
    if (histologyHandToolActive() || _histologyView.dragMoved) return;
    finishHistologyPolygon();
  });
  canvas.addEventListener('pointerdown', event => histologyBeginPan(event, canvas));
  canvas.addEventListener('pointermove', histologyMovePan);
  canvas.addEventListener('pointerup', histologyEndPan);
  canvas.addEventListener('pointercancel', histologyEndPan);
  canvas.addEventListener('lostpointercapture', () => histologyEndPan(null));
  window.addEventListener('resize', () => requestAnimationFrame(resizeHistologyAnalysisCanvas));
  const area = document.getElementById('histologyAnalysisPlotArea');
  if (area && area.dataset.boundWheel !== '1') {
    area.dataset.boundWheel = '1';
    area.addEventListener('pointerenter', () => {_histologyView.pointerInside = true;});
    area.addEventListener('pointerleave', () => {_histologyView.pointerInside = false;});
    area.addEventListener('contextmenu', event => {
      if (_histologyAnalysisImage) event.preventDefault();
    });
    area.addEventListener('wheel', histologyHandleWheel, {passive: false});
    area.addEventListener('gesturestart', histologyHandleGestureStart, {passive: false});
    area.addEventListener('gesturechange', histologyHandleGestureChange, {passive: false});
    area.addEventListener('gestureend', histologyHandleGestureEnd, {passive: false});
    window.addEventListener('keydown', event => {
      if (event.code !== 'Space' || !_histologyAnalysisImage || !_histologyView.pointerInside || histologyIsTypingTarget(event.target)) return;
      event.preventDefault();
      if (!_histologyView.spacePan) {
        _histologyView.spacePan = true;
        histologyApplyViewTransform();
      }
    });
    window.addEventListener('keyup', event => {
      if (event.code !== 'Space' || !_histologyView.spacePan) return;
      event.preventDefault();
      _histologyView.spacePan = false;
      _histologyView.dragging = false;
      _histologyView.pointerId = null;
      histologyApplyViewTransform();
    });
    window.addEventListener('blur', () => {
      if (!_histologyView.spacePan && !_histologyView.dragging) return;
      _histologyView.spacePan = false;
      _histologyView.dragging = false;
      _histologyView.pointerId = null;
      histologyApplyViewTransform();
    });
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
    coordinate_space: 'native',
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
  scheduleHistologyRoiDebugPreview(180);
  updateHistologyActionState();
}

function clearHistologyRoiDebug(message) {
  if (_histologyRoiDebug.timer) {
    clearTimeout(_histologyRoiDebug.timer);
    _histologyRoiDebug.timer = null;
  }
  const meta = document.getElementById('histologyRoiDebugMeta');
  const preview = document.getElementById('histologyRoiDebugPreview');
  const metrics = document.getElementById('histologyRoiDebugMetrics');
  if (meta) meta.textContent = message || 'Select a project image and ROI to inspect parameter effects.';
  if (preview) {
    preview.classList.add('plot-placeholder');
    preview.innerHTML = 'No ROI tuning preview yet.';
  }
  if (metrics) metrics.innerHTML = '';
}

function renderHistologyRoiDebugSelect() {
  const select = document.getElementById('histologyDebugRoiSelect');
  if (!select) return;
  const previous = select.value;
  if (!_histologyAnalysisRois.length) {
    select.innerHTML = '<option value="">No ROI</option>';
    clearHistologyRoiDebug('Draw or load a ROI to tune parameters.');
    return;
  }
  select.innerHTML = _histologyAnalysisRois.map((roi, index) => {
    const label = roi.label || `ROI ${index + 1}`;
    const points = Array.isArray(roi.points) ? roi.points.length : 0;
    return `<option value="${index}">${escHtml(index + 1)} · ${escHtml(label)} · ${points} pt</option>`;
  }).join('');
  if (previous && Array.from(select.options).some(option => option.value === previous)) {
    select.value = previous;
  } else {
    select.value = '0';
  }
}

function histologyRoiDebugSelectedIndex() {
  const value = Number(document.getElementById('histologyDebugRoiSelect')?.value || 0);
  return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
}

function histologyRoiDebugAutoEnabled() {
  return !!document.getElementById('histologyRoiDebugAuto')?.checked;
}

function scheduleHistologyRoiDebugPreview(delayMs) {
  if (_histologyRoiDebug.timer) {
    clearTimeout(_histologyRoiDebug.timer);
    _histologyRoiDebug.timer = null;
  }
  if (!histologyRoiDebugAutoEnabled()) return;
  if (!_histologyAnalysisImage || _histologyAnalysisImage.source_mode !== 'project') return;
  if (!_histologyProjectEntryId || !_histologyAnalysisRois.length) return;
  _histologyRoiDebug.timer = setTimeout(() => previewHistologyRoiDebug({auto: true}), Math.max(80, Number(delayMs || 420)));
}

function bindHistologyRoiDebugControls() {
  const ids = [
    'dapiChannel',
    'dapiThresholdMethod',
    'dapiThreshold',
    'dapiMaskEnabled',
    'dapiDilatePx',
    'smaChannel',
    'smaThresholdMethod',
    'smaThreshold',
    'macrophageChannel',
    'macrophageThresholdMethod',
    'macrophageThreshold',
    'backgroundMode',
    'backgroundPercentile',
    'smoothSigma',
    'minPositiveArea',
    'thresholdPercentile',
    'thresholdStdK',
    'roiShrinkPercent',
    'histologySummaryGroupBy',
    'histologyNormalizeGroup',
    'histologyExcludeZeroObservations',
    'histologyDebugRoiSelect',
  ];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (!el || el.dataset.histologyDebugBound === '1') return;
    el.dataset.histologyDebugBound = '1';
    const eventName = el.tagName === 'SELECT' || el.type === 'checkbox' ? 'change' : 'input';
    el.addEventListener(eventName, () => scheduleHistologyRoiDebugPreview(id === 'histologyDebugRoiSelect' ? 60 : 420));
  });
  const auto = document.getElementById('histologyRoiDebugAuto');
  if (auto && auto.dataset.histologyDebugBound !== '1') {
    auto.dataset.histologyDebugBound = '1';
    auto.addEventListener('change', () => {
      if (auto.checked) scheduleHistologyRoiDebugPreview(60);
    });
  }
}

function histologyRoiDebugBeforeParameters() {
  return Object.assign({}, _histologyRoiDebug.baselineParameters || histologyAnalysisParameters());
}

function histologyDebugPercent(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '';
  return `${(num * 100).toFixed(2)}%`;
}

function histologyDebugSignedPercent(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '';
  const sign = num > 0 ? '+' : '';
  return `${sign}${(num * 100).toFixed(2)}%`;
}

function histologyDebugMetricCell(value, digits) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '';
  return num.toLocaleString(undefined, {maximumFractionDigits: digits ?? 2});
}

function renderHistologyRoiDebugResult(payload) {
  const meta = document.getElementById('histologyRoiDebugMeta');
  const preview = document.getElementById('histologyRoiDebugPreview');
  const metrics = document.getElementById('histologyRoiDebugMetrics');
  if (meta) {
    const pieces = [
      payload.image_name || payload.display_name || '',
      payload.treatment ? `treatment ${payload.treatment}` : '',
      payload.sample_number ? `image ${payload.sample_number}` : '',
      payload.roi_label || payload.roi_id || '',
      `shrink ${histologyDebugMetricCell(payload.roi_shrink_percent || 0, 1)}%`,
    ].filter(Boolean);
    meta.textContent = pieces.join(' · ');
  }
  if (preview) {
    preview.classList.remove('plot-placeholder');
    preview.innerHTML = payload.img
      ? `<img alt="ROI tuning preview" src="data:image/png;base64,${payload.img}">`
      : 'No preview image returned.';
  }
  if (!metrics) return;
  const before = payload.before || {};
  const after = payload.after || {};
  const delta = payload.delta || {};
  const markerRow = (key, label) => {
    const beforeMarker = before[key] || {};
    const afterMarker = after[key] || {};
    const deltaMarker = delta[key] || {};
    const thresholdText = [
      histologyDebugMetricCell(beforeMarker.threshold, 2),
      histologyDebugMetricCell(afterMarker.threshold, 2),
    ].filter(Boolean).join(' -> ');
    return `
      <tr>
        <td>${escHtml(label)}</td>
        <td>${escHtml(histologyDebugPercent(beforeMarker.positive_area_ratio))}</td>
        <td>${escHtml(histologyDebugPercent(afterMarker.positive_area_ratio))}</td>
        <td>${escHtml(histologyDebugSignedPercent(deltaMarker.positive_area_ratio))}</td>
        <td>${escHtml(histologyDebugMetricCell(afterMarker.positive_px, 0))}</td>
        <td>${escHtml(thresholdText)}</td>
        <td>${escHtml(histologyDebugMetricCell(afterMarker.object_count, 0))}</td>
      </tr>`;
  };
  const warnings = (Array.isArray(payload.warnings) ? payload.warnings : [])
    .filter(Boolean)
    .slice(0, 2)
    .map(item => `<div class="histology-file-subline">${escHtml(item)}</div>`)
    .join('');
  metrics.innerHTML = `
    <div class="histology-results-meta">
      Area ${escHtml(histologyDebugMetricCell(before.area_px, 0))} -> ${escHtml(histologyDebugMetricCell(after.area_px, 0))} px · analysis ${escHtml(histologyDebugMetricCell(before.analysis_area_px, 0))} -> ${escHtml(histologyDebugMetricCell(after.analysis_area_px, 0))} px
    </div>
    <div class="histology-scroll">
      <table class="data-table">
        <thead><tr><th>Marker</th><th>Before</th><th>After</th><th>Delta</th><th>After px</th><th>T</th><th>Obj</th></tr></thead>
        <tbody>${markerRow('sma', 'SMA')}${markerRow('macrophage', 'Mac')}</tbody>
      </table>
    </div>
    ${warnings}`;
}

function previewHistologyRoiDebug(options) {
  const opts = options || {};
  if (!_histologyAnalysisImage || _histologyAnalysisImage.source_mode !== 'project' || !_histologyProjectEntryId) {
    if (!opts.auto) clearHistologyRoiDebug('Select a project image before tuning ROI parameters.');
    return;
  }
  if (!_histologyAnalysisRois.length) {
    if (!opts.auto) clearHistologyRoiDebug('Draw or load a ROI before tuning parameters.');
    return;
  }
  const seq = ++_histologyRoiDebug.seq;
  _histologyRoiDebug.loading = true;
  if (!opts.auto) btnBusy('btnPreviewHistologyRoiDebug', true, 'Previewing...');
  const meta = document.getElementById('histologyRoiDebugMeta');
  if (meta) meta.textContent = 'Updating ROI tuning preview...';
  api('/api/histology/project/analysis/debug_roi', {
    project_path: histologyDataProjectPath(),
    entry_id: _histologyProjectEntryId,
    roi_index: histologyRoiDebugSelectedIndex(),
    parameters: histologyAnalysisParameters(),
    before_parameters: histologyRoiDebugBeforeParameters(),
    max_side: 900,
    selected_channels: histologySelectedViewChannels(),
  }).then(d => {
    if (seq !== _histologyRoiDebug.seq) return;
    _histologyRoiDebug.loading = false;
    if (!opts.auto) btnBusy('btnPreviewHistologyRoiDebug', false, 'Preview ROI tuning');
    if (d.error) throw new Error(d.error);
    renderHistologyRoiDebugResult(d);
  }).catch(e => {
    if (seq !== _histologyRoiDebug.seq) return;
    _histologyRoiDebug.loading = false;
    if (!opts.auto) btnBusy('btnPreviewHistologyRoiDebug', false, 'Preview ROI tuning');
    clearHistologyRoiDebug('ROI tuning preview failed: ' + e.message);
  });
}

function renderHistologyRoiList() {
  const el = document.getElementById('histologyRoiList');
  const countEl = document.getElementById('histologyRoiInlineCount');
  if (countEl) {
    const drawing = _histologyActivePolygon ? ' · drawing' : '';
    countEl.textContent = `${_histologyAnalysisRois.length} ROI${drawing}`;
  }
  renderHistologyRoiDebugSelect();
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
  scheduleHistologyRoiDebugPreview(320);
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
    roi_shrink_percent: num('roiShrinkPercent', 0),
    summary_group_by: document.getElementById('histologySummaryGroupBy')?.value || 'treatment',
    summary_aggregate_rois_by_entry: true,
    exclude_zero_observations: !!document.getElementById('histologyExcludeZeroObservations')?.checked,
    summary_normalize_to_group: document.getElementById('histologyNormalizeGroup')?.value || 'CB',
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
  setValue('roiShrinkPercent', 'roi_shrink_percent');
  setValue('histologySummaryGroupBy', 'summary_group_by');
  setValue('histologyExcludeZeroObservations', 'exclude_zero_observations');
  setValue('histologyNormalizeGroup', 'summary_normalize_to_group');
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
  const rois = histologyRoisForApi();
  api('/api/histology/project/analysis/save_rois', {
    project_path: histologyDataProjectPath(),
    entry_id: _histologyProjectEntryId,
    rois,
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
  const rois = histologyRoisForApi();
  const body = isFileMode ? {
    image_path: _histologyAnalysisImage?.image_path || histologyAnalysisImagePath(),
    rois,
    parameters: histologyAnalysisParameters(),
  } : {
    project_path: histologyDataProjectPath(),
    entry_id: _histologyProjectEntryId,
    rois,
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
    if (!isFileMode) _histologyRoiDebug.baselineParameters = Object.assign({}, d.analysis?.parameters || histologyAnalysisParameters());
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
      parameters: Object.assign({entry_id: _histologyProjectEntryId, image_path: _histologyAnalysisImage?.image_path || '', rois}, histologyAnalysisParameters()),
      metadata: {roi_count: d.roi_count || _histologyAnalysisRois.length, backend: d.backend || ''},
    });
    updateHistologyActionState();
  }).catch(e => {
    btnBusy('btnAnalyzeHistology', false, 'Analyze SMA + Macrophage');
    histologySetAnalysisStatus('Error: ' + e.message, 'error');
    updateHistologyActionState();
  });
}

function analyzeHistologyProjectSavedRois() {
  const projectPath = histologyDataProjectPath();
  if (!projectPath) {
    histologySetAnalysisStatus('Load a DataProcess histology project first', 'error');
    return;
  }
  btnBusy('btnAnalyzeHistologyProject', true, 'Analyzing...');
  histologySetAnalysisStatus('Analyzing saved ROI across project...', 'loading');
  dpRunJobEndpoint('/api/histology/project/analysis/run_saved_job', {
    project_path: projectPath,
    parameters: histologyAnalysisParameters(),
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      histologySetAnalysisStatus(`Analyzing saved histology ROI${pct}${msg}`, 'loading');
    },
  }).then(d => {
    btnBusy('btnAnalyzeHistologyProject', false, 'Analyze Saved ROI (all)');
    if (d.error) throw new Error(d.error);
    _histologyLatestBatchAnalysis = d;
    renderHistologyProjectBatchResults(d);
    const text = `Analyzed ${d.observation_count || 0} image-level sample(s) from ${d.roi_count || 0} ROI across ${d.sample_count || 0} group(s)`;
    histologySetAnalysisStatus(text, 'ok');
    toast('Histology project summary complete');
    recordRunHistory({
      view: 'histology_analysis',
      title: 'Histology Project Saved ROI Summary',
      status: 'ok',
      project_root: histologyProjectRoot(),
      input_files: [{path: d.project_path || projectPath, role: 'histology_project'}],
      outputs: dpAsPathRecords([
        d.roi_table_path,
        d.image_table_path,
        d.summary_table_path,
        d.statistics_path,
        d.manifest_path,
        d.run_dir,
        ...(Array.isArray(d.plots) ? d.plots.flatMap(plot => [plot.path, plot.svg_path]) : []),
      ], 'histology_project_summary_output'),
      parameters: histologyAnalysisParameters(),
      metadata: {roi_count: d.roi_count || 0, observation_count: d.observation_count || 0, sample_count: d.sample_count || 0},
    });
    updateHistologyActionState();
  }).catch(e => {
    btnBusy('btnAnalyzeHistologyProject', false, 'Analyze Saved ROI (all)');
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

function histologyFiniteNumber(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function histologyCompactNumber(value, digits) {
  const num = histologyFiniteNumber(value);
  if (num === null) return '';
  return num.toLocaleString(undefined, {maximumFractionDigits: digits ?? 2});
}

function histologyAreaText(row) {
  const um2 = histologyFiniteNumber(row.area_um2);
  if (um2 === null) return histologyCompactNumber(row.area_px || 0, 0);
  if (um2 >= 1_000_000) return `${histologyCompactNumber(um2 / 1_000_000, 4)} mm^2`;
  return `${histologyCompactNumber(um2, 1)} um^2`;
}

function histologyDensityText(value) {
  const num = histologyFiniteNumber(value);
  if (num === null) return '';
  return histologyCompactNumber(num, num >= 100 ? 0 : 1);
}

function histologyFormatP(value) {
  const num = histologyFiniteNumber(value);
  if (num === null) return 'n/a';
  return num < 0.001 ? num.toExponential(2) : num.toFixed(5).replace(/0+$/, '').replace(/\.$/, '');
}

function renderHistologyProjectBatchResults(batch) {
  const el = document.getElementById('histologyProjectBatchResults');
  if (!el) return;
  if (!batch || !Array.isArray(batch.summary) || !batch.summary.length) {
    el.innerHTML = '<div class="plot-placeholder histology-result-empty">No project-level saved ROI summary yet.</div>';
    return;
  }
  const rows = batch.summary.map(row => `
    <tr>
      <td>${escHtml(row.sample_group || '')}</td>
      <td>${histologyCompactNumber(row.n_entries || row.n_observations || 0, 0)}</td>
      <td>${histologyCompactNumber(row.n_roi || 0, 0)}</td>
      <td>${histologyCompactNumber(row.sma_n_observations || row.n_entries || 0, 0)}</td>
      <td>${histologyCompactNumber(row.sma_normalized_mean || 0, 3)}</td>
      <td>${histologyCompactNumber(row.sma_normalized_sem || 0, 3)}</td>
      <td>${histologyCompactNumber(row.macrophage_n_observations || row.n_entries || 0, 0)}</td>
      <td>${histologyCompactNumber(row.macrophage_normalized_mean || 0, 3)}</td>
      <td>${histologyCompactNumber(row.macrophage_normalized_sem || 0, 3)}</td>
    </tr>`).join('');
  const stat = marker => batch.statistics && batch.statistics[marker] ? batch.statistics[marker] : {};
  const outputs = [batch.roi_table_path, batch.image_table_path, batch.summary_table_path, batch.statistics_path, batch.manifest_path]
    .filter(Boolean)
    .map(path => `<div class="histology-file-path">${escHtml(path)}</div>`)
    .join('');
  const plots = (Array.isArray(batch.plots) ? batch.plots : [])
    .filter(plot => plot && plot.img)
    .map(plot => `
      <div class="plot-image-frame histology-stack-gap">
        <img alt="${escHtml(plot.marker || 'histology')} ${escHtml(plot.kind || 'plot')}" src="data:image/png;base64,${plot.img}">
      </div>`)
    .join('');
  const warnings = (Array.isArray(batch.warnings) ? batch.warnings : [])
    .filter(Boolean)
    .slice(0, 4)
    .map(item => `<div class="histology-file-subline">${escHtml(item)}</div>`)
    .join('');
  el.innerHTML = `
    <div class="histology-results-meta">
      ${escHtml(batch.created_at || '')} · ${escHtml(batch.observation_level || 'image')} level · normalized to group ${escHtml(batch.normalization?.normalize_to_group || '1')} ·
      SMA ANOVA P=${escHtml(histologyFormatP(stat('sma').p))} · Macrophage ANOVA P=${escHtml(histologyFormatP(stat('macrophage').p))}
    </div>
    <div class="histology-scroll">
      <table class="data-table">
        <thead><tr><th>Group</th><th>n image</th><th>n ROI</th><th>n SMA</th><th>SMA mean</th><th>SMA SEM</th><th>n Mac</th><th>Mac mean</th><th>Mac SEM</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    ${warnings}
    ${outputs}
    ${plots}`;
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
      <td>${escHtml(histologyAreaText(row))}</td>
      <td>${(100 * Number(row.sma_positive_fraction || 0)).toFixed(2)}%</td>
      <td>${(100 * Number(row.macrophage_positive_fraction || 0)).toFixed(2)}%</td>
      <td>${(100 * Number(row.double_positive_fraction || 0)).toFixed(2)}%</td>
      <td>${escHtml(histologyDensityText(row.sma_object_density_per_mm2) || histologyCompactNumber(row.sma_object_count || 0, 0))}</td>
      <td>${escHtml(histologyDensityText(row.macrophage_object_density_per_mm2) || histologyCompactNumber(row.macrophage_object_count || 0, 0))}</td>
    </tr>`).join('');
  const calibrated = !!analysis.calibration?.has_physical_scale;
  const areaHead = calibrated ? 'Area' : 'Area px';
  const objectHead = calibrated ? 'obj/mm^2' : 'obj';
  const paramText = [
    `DAPI ${analysis.parameters?.dapi_channel || ''} ${analysis.parameters?.dapi_threshold_method || ''}`,
    `SMA ${analysis.parameters?.sma_channel || ''} ${analysis.parameters?.sma_threshold_method || ''}`,
    `Mac ${analysis.parameters?.macrophage_channel || ''} ${analysis.parameters?.macrophage_threshold_method || ''}`,
    `BC ${analysis.parameters?.background_mode || ''}`,
  ].join(' · ');
  const calibrationText = calibrated
    ? ` · ${Number(analysis.calibration.pixel_width_um).toFixed(4)} x ${Number(analysis.calibration.pixel_height_um).toFixed(4)} um/px`
    : '';
  el.innerHTML = `
    <div class="histology-results-meta">
      ${escHtml(analysis.created_at || '')} · ${escHtml(paramText)}${escHtml(calibrationText)}
    </div>
    <div class="histology-scroll">
      <table class="data-table">
        <thead><tr><th>ROI</th><th>${escHtml(areaHead)}</th><th>SMA+</th><th>Macrophage+</th><th>Double+</th><th>SMA ${escHtml(objectHead)}</th><th>Mac ${escHtml(objectHead)}</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

window.addEventListener('load', () => {
  bindHistologyRoiLabels();
  bindHistologyRoiDebugControls();
  setupHistologyAnalysisCanvas();
  renderHistologyAnalysisProjectImageList();
  renderHistologyRoiList();
  renderHistologyAnalysisResults(null);
  renderHistologyProjectBatchResults(null);
  clearHistologyRoiDebug();
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
  'analyzeHistologyProjectSavedRois',
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
  'histologyHandleViewChannelChanged',
  'histologyHideChannelView',
  'histologyProjectRoot',
  'previewHistologyRoiDebug',
  'scheduleHistologyRoiDebugPreview',
  'histologySelectedViewChannels',
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
  'renderHistologyProjectBatchResults',
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
