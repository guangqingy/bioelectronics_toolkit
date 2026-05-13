function initRoiCanvas(showCanvas = true) {
  const img = document.getElementById('gifPreviewImg');
  const canvas = document.getElementById('gifRoiCanvas');
  if (!img || !canvas || !img.clientWidth || !img.clientHeight) return;
  canvas.width = img.clientWidth;
  canvas.height = img.clientHeight;
  canvas.style.width = img.clientWidth + 'px';
  canvas.style.height = img.clientHeight + 'px';
  canvas.style.display = showCanvas ? '' : 'none';
  if (showCanvas) drawPolygons();
}

function nativeToCanvas(pt) {
  const canvas = document.getElementById('gifRoiCanvas');
  return {
    x: pt.x / Math.max(1, _imgW) * canvas.width,
    y: pt.y / Math.max(1, _imgH) * canvas.height,
  };
}

function canvasToNative(cx, cy) {
  const canvas = document.getElementById('gifRoiCanvas');
  return {
    x: Math.round(cx / Math.max(1, canvas.width) * _imgW),
    y: Math.round(cy / Math.max(1, canvas.height) * _imgH),
  };
}

function drawOnePolygon(ctx, poly, closed, isBg = false) {
  if (!poly || !poly.points || !poly.points.length) return;
  const pts = poly.points.map(nativeToCanvas);
  ctx.strokeStyle = poly.color;
  ctx.fillStyle = poly.color;
  ctx.lineWidth = 2;
  ctx.setLineDash(isBg || !closed ? [5, 3] : []);
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (const p of pts.slice(1)) ctx.lineTo(p.x, p.y);
  if (closed && pts.length >= 3) ctx.closePath();
  ctx.stroke();
  ctx.setLineDash([]);
  for (const p of pts) {
    ctx.beginPath();
    ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.font = 'bold 12px sans-serif';
  ctx.fillText(poly.label + (isBg ? ' (BG)' : ''), pts[0].x + 6, pts[0].y + 14);
}

function drawOneCropRect(ctx, rect, isDraft = false) {
  if (!rect) return;
  const r = normalizeRectObject(rect);
  if (r.width <= 0 || r.height <= 0) return;
  const p0 = nativeToCanvas({x: r.x, y: r.y});
  const p1 = nativeToCanvas({x: r.x + r.width, y: r.y + r.height});
  const x = Math.min(p0.x, p1.x);
  const y = Math.min(p0.y, p1.y);
  const w = Math.abs(p1.x - p0.x);
  const h = Math.abs(p1.y - p0.y);
  ctx.save();
  ctx.strokeStyle = rect.color || '#38bdf8';
  ctx.fillStyle = rect.color || '#38bdf8';
  ctx.lineWidth = 2;
  ctx.setLineDash(isDraft ? [6, 4] : [3, 2]);
  ctx.strokeRect(x, y, w, h);
  ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(0, 0, 0, 0.68)';
  ctx.fillRect(x + 4, y + 4, 64, 18);
  ctx.fillStyle = rect.color || '#38bdf8';
  ctx.font = 'bold 12px sans-serif';
  ctx.fillText(rect.label || 'ROI2', x + 8, y + 17);
  ctx.restore();
}

function drawPolygons() {
  const canvas = document.getElementById('gifRoiCanvas');
  if (!canvas || !canvas.width || canvas.style.display === 'none') return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if ((document.getElementById('gifCropMode')?.value || 'full') !== 'full') return;
  const bgLabel = gifBgLabel();
  _roiPolygons.forEach(p => drawOnePolygon(ctx, p, true, p.label === bgLabel));
  if (_draftPolygon) drawOnePolygon(ctx, _draftPolygon, false);
  _cropRects.forEach(r => drawOneCropRect(ctx, r, false));
  if (_draftCropRect) drawOneCropRect(ctx, _draftCropRect, true);
}

/* ---------- Scan frame counts ---------- */
