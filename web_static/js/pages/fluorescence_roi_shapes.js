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
      <button data-dp-click="removeRoi(${i})" style="background:none;border:none;color:var(--silver);cursor:pointer;font-size:13px;padding:0 2px;line-height:1">X</button>
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

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'addRoi',
  'currentDrawShape',
  'effectiveDrawShape',
  'getPixelSizeUmInput',
  'getRingCount',
  'getRingWidthPx',
  'getRingWidthUm',
  'isActiveBackgroundTarget',
  'onBgModeChange',
  'onBgRoiChange',
  'onDrawShapeChange',
  'onDrawTargetChange',
  'onRingWidthChange',
  'removeRoi',
  'renderRoiList',
  'renumberRois',
  'resolveRingWidthPx',
  'roiSummary',
  'roiToPayload',
  'roiTypeLabel',
  'updateCanvasHint',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
