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
  sel.innerHTML = polys.map(p => `<option value="${dpEscapeHtml(p.label)}">${dpEscapeHtml(p.label)}</option>`).join('');
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
  sel.innerHTML = polys.map(p => `<option value="${dpEscapeHtml(p.label)}">${dpEscapeHtml(p.label)}</option>`).join('');
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
  sel.innerHTML = rects.map(r => `<option value="${dpEscapeHtml(r.label)}">${dpEscapeHtml(r.label)}</option>`).join('');
  if (rects.some(r => r.label === prev)) sel.value = prev;
  else if (rects.length) sel.value = rects[0].label;
}

function gifLabelMode() {
  return gifValue('gifLabelMode', 'frame') || 'frame';
}

function gifShowRoiOverlay() {
  return gifChecked('gifShowRoiOverlay');
}

function gifCropPayload() {
  const mode = gifValue('gifCropMode', 'full') || 'full';
  const rectLabel = gifValue('gifCropRoiSelect');
  const padding = gifInteger('gifCropPadding', 0);
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
  gifSetText('polygonHint', 'Click preview to add polygon points, then Done Drawing.');
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
  gifSetText('polygonHint', `${_roiPolygons.length} polygon ROI marker(s) ready · drawing complete`);
  renderPolygonList();
  drawPolygons();
}

function finishPolygonDrawing() {
  if (_draftPolygon) {
    closePolygon();
    return;
  }
  if (_roiPolygons.length) {
    gifSetText('polygonHint', `${_roiPolygons.length} polygon ROI marker(s) ready · drawing complete`);
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
  gifSetText('polygonHint', 'Click + Polygon, then click the preview image to add points.');
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
      <span class="gif-roi-name">${dpEscapeHtml(p.label)}${p.label === bgLabel ? ' · BG' : ''} · ${p.points.length} pts</span>
      <button class="btn-icon btn-danger" style="width:24px;min-width:24px;height:24px;padding:0" data-dp-click="removePolygon(${i})">×</button>
    </div>`);
  if (_draftPolygon) {
    rows.push(`
      <div class="gif-roi-item">
        <span class="gif-roi-swatch" style="background:${_draftPolygon.color}"></span>
        <span class="gif-roi-name">${dpEscapeHtml(_draftPolygon.label)} · drawing · ${_draftPolygon.points.length} pts</span>
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
  gifSetText('cropRectHint', 'Drag on the preview image to define a rectangular crop ROI2.');
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
    gifSetText('cropRectHint', 'ROI2 rectangle was too small; drag a larger rectangle.');
    renderCropRectList();
    drawPolygons();
    return;
  }
  _cropRects.push(rect);
  const modeEl = document.getElementById('gifCropMode');
  if (modeEl) modeEl.value = 'selected_rect';
  gifSetText('cropRectHint', `${_cropRects.length} ROI2 crop rectangle(s) ready`);
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
  gifSetText('cropRectHint', 'Click + Rectangle, then drag on the preview image.');
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
      <span class="gif-roi-name">${dpEscapeHtml(r.label)} · ${Math.round(r.width)}×${Math.round(r.height)} px</span>
      <button class="btn-icon btn-danger" style="width:24px;min-width:24px;height:24px;padding:0" data-dp-click="removeCropRect(${i})">×</button>
    </div>`);
  if (_draftCropRect) {
    const r = normalizeRectObject(_draftCropRect);
    rows.push(`
      <div class="gif-roi-item">
        <span class="gif-roi-swatch" style="background:${_draftCropRect.color}"></span>
        <span class="gif-roi-name">${dpEscapeHtml(_draftCropRect.label)} · drawing · ${Math.round(r.width)}×${Math.round(r.height)} px</span>
      </div>`);
  }
  el.innerHTML = rows.join('');
  updateGifCropControls();
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'clearCropRects',
  'clearPolygons',
  'closePolygon',
  'finishCropRect',
  'finishPolygonDrawing',
  'getClosedRoiPolygons',
  'getCropRects',
  'gifBgLabel',
  'gifCropPayload',
  'gifLabelMode',
  'gifShowRoiOverlay',
  'normalizeRectObject',
  'removeCropRect',
  'removePolygon',
  'renderCropRectList',
  'renderPolygonList',
  'renumberCropRects',
  'renumberGifPolygons',
  'startCropRect',
  'startPolygon',
  'undoPolygonPoint',
  'updateGifBgControls',
  'updateGifCropControls',
  'updateGifKymoControls',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
