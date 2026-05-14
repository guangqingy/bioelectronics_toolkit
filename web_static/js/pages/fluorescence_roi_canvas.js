let _drawing = false;
let _drawStartX = 0;
let _drawStartY = 0;
let _activeDrawLabel = null;

function initCanvas() {
  const img = document.getElementById('tiffImg');
  if (!img || !img.clientWidth || !img.clientHeight) return;
  const cvs = document.getElementById('roiCanvas');
  cvs.width = img.clientWidth;
  cvs.height = img.clientHeight;
  cvs.style.width = img.clientWidth + 'px';
	  cvs.style.height = img.clientHeight + 'px';
	  cvs.style.display = '';
	  updateCanvasHint();
	  document.getElementById('coordHint').textContent = 'ROI coordinates are saved in native image pixels';
	  drawRects();
	}

function imgToNative(cx, cy) {
  const img = document.getElementById('tiffImg');
  return {
    x: Math.round(cx / img.clientWidth * _imgW),
    y: Math.round(cy / img.clientHeight * _imgH),
  };
}

function nativeToImg(nx, ny) {
  const img = document.getElementById('tiffImg');
  return {
    x: nx / _imgW * img.clientWidth,
    y: ny / _imgH * img.clientHeight,
  };
}

(function setupCanvas() {
  const cvs = document.getElementById('roiCanvas');

  cvs.addEventListener('mousedown', e => {
    e.preventDefault();
    const label = document.getElementById('drawTarget').value;
    if (!label) return;
    _activeDrawLabel = label;
    const r = cvs.getBoundingClientRect();
    _drawStartX = e.clientX - r.left;
    _drawStartY = e.clientY - r.top;
    _drawing = true;
  });

	  cvs.addEventListener('mousemove', e => {
	    if (!_drawing || !_activeDrawLabel) return;
	    const r = cvs.getBoundingClientRect();
    drawRects({
      label: _activeDrawLabel,
      type: effectiveDrawShape(_activeDrawLabel),
      x1: _drawStartX,
      y1: _drawStartY,
      x2: e.clientX - r.left,
	      y2: e.clientY - r.top,
    });
  });

  cvs.addEventListener('mouseup', e => {
    if (!_drawing || !_activeDrawLabel) return;
    _drawing = false;
	    const r = cvs.getBoundingClientRect();
	    const ex = e.clientX - r.left;
	    const ey = e.clientY - r.top;
    const roi = _rois.find(r0 => r0.label === _activeDrawLabel);
    if (roi) {
      const shape = effectiveDrawShape(_activeDrawLabel);
      if (shape === 'concentric') {
	        const c = imgToNative(_drawStartX, _drawStartY);
	        const edge = imgToNative(ex, ey);
	        c.x = Math.max(0, Math.min(_imgW - 1, c.x));
	        c.y = Math.max(0, Math.min(_imgH - 1, c.y));
	        const dx = edge.x - c.x;
	        const dy = edge.y - c.y;
	        const radius = Math.round(Math.sqrt(dx * dx + dy * dy));
	        if (radius > 2) {
	          roi.type = 'concentric';
          roi.cx = c.x;
          roi.cy = c.y;
          roi.radius = radius;
          roi.ring_count = getRingCount();
          roi.ring_width_px = resolveRingWidthPx(radius);
          roi.ring_width_um = getRingWidthUm();
	          roi.x1 = c.x - radius;
	          roi.y1 = c.y - radius;
	          roi.x2 = c.x + radius;
	          roi.y2 = c.y + radius;
	          roi.drawn = true;
	          renderRoiList();
	        }
	      } else {
	        const n1 = imgToNative(Math.min(_drawStartX, ex), Math.min(_drawStartY, ey));
	        const n2 = imgToNative(Math.max(_drawStartX, ex), Math.max(_drawStartY, ey));
	        if (n2.x - n1.x > 2 && n2.y - n1.y > 2) {
	          roi.type = 'rect';
	          roi.x1 = n1.x;
	          roi.y1 = n1.y;
	          roi.x2 = n2.x;
	          roi.y2 = n2.y;
	          roi.drawn = true;
	          renderRoiList();
	        }
	      }
	    }

    drawRects();
    _activeDrawLabel = null;
  });

  cvs.addEventListener('mouseleave', () => {
    if (_drawing) {
      _drawing = false;
      drawRects();
      _activeDrawLabel = null;
    }
  });

  cvs.addEventListener('contextmenu', e => e.preventDefault());
})();

function drawRects(liveRect) {
  const cvs = document.getElementById('roiCanvas');
  if (!cvs.width) return;
  const ctx = cvs.getContext('2d');
  ctx.clearRect(0, 0, cvs.width, cvs.height);

  const bgMode = document.getElementById('bgMode').value;
  const bgLabel = document.getElementById('bgRoiSelect').value;

  for (const roi of _rois) {
    if (!roi.drawn) continue;
    const isBg = bgMode === 'roi' && roi.label === bgLabel;

    ctx.strokeStyle = roi.color;
    ctx.lineWidth = 2;
    ctx.setLineDash(isBg ? [5, 3] : []);
    if (roi.type === 'concentric') {
      drawConcentricOnCanvas(ctx, roi, isBg);
    } else {
      const p1 = nativeToImg(roi.x1, roi.y1);
      const p2 = nativeToImg(roi.x2, roi.y2);
      ctx.strokeRect(p1.x, p1.y, p2.x - p1.x, p2.y - p1.y);
      ctx.fillStyle = roi.color;
      ctx.font = 'bold 11px sans-serif';
      ctx.fillText(roi.label + (isBg ? ' (BG)' : ''), p1.x + 4, p1.y + 13);
    }
    ctx.setLineDash([]);
  }

  if (liveRect) {
    const roi = _rois.find(r => r.label === liveRect.label);
    if (roi) {
      ctx.strokeStyle = roi.color;
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 2]);
      if (liveRect.type === 'concentric') {
        const radius = Math.hypot(liveRect.x2 - liveRect.x1, liveRect.y2 - liveRect.y1);
        drawConcentricOnCanvas(ctx, {
          label: liveRect.label,
          color: roi.color,
          type: 'concentric',
          cx: liveRect.x1 / cvs.width * _imgW,
          cy: liveRect.y1 / cvs.height * _imgH,
          radius: radius / cvs.width * _imgW,
          ring_width_px: resolveRingWidthPx(radius / cvs.width * _imgW),
          ring_width_um: getRingWidthUm(),
          ring_count: getRingCount(),
        }, false);
      } else {
        const lx1 = Math.min(liveRect.x1, liveRect.x2);
        const ly1 = Math.min(liveRect.y1, liveRect.y2);
        const lx2 = Math.max(liveRect.x1, liveRect.x2);
        const ly2 = Math.max(liveRect.y1, liveRect.y2);
        ctx.strokeRect(lx1, ly1, lx2 - lx1, ly2 - ly1);
      }
      ctx.setLineDash([]);
    }
  }

  if ((bgMode === 'corner_br' || bgMode === 'corner_tl') && _imgW > 0 && _imgH > 0) {
    const sz = Math.min(40, _imgH / 4, _imgW / 4);
    let nx1 = 0;
    let ny1 = 0;
    if (bgMode === 'corner_br') {
      nx1 = _imgW - sz;
      ny1 = _imgH - sz;
    }
    const cp1 = nativeToImg(nx1, ny1);
    const cp2 = nativeToImg(nx1 + sz, ny1 + sz);
    ctx.strokeStyle = '#22c55e';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 2]);
    ctx.strokeRect(cp1.x, cp1.y, cp2.x - cp1.x, cp2.y - cp1.y);
    ctx.fillStyle = '#22c55e';
    ctx.font = 'bold 10px sans-serif';
    ctx.fillText('BG', cp1.x + 3, cp1.y + 11);
    ctx.setLineDash([]);
  }
}

function drawConcentricOnCanvas(ctx, roi, isBg) {
  const center = nativeToImg(roi.cx || 0, roi.cy || 0);
  const img = document.getElementById('tiffImg');
  const scaleX = img.clientWidth / Math.max(1, _imgW);
  const radius = Math.max(0, Number(roi.radius || 0)) * scaleX;
  const count = parseInt(roi.ring_count || 0, 10);
  const ringStep = count > 0 ? radius / count : Math.max(1, Number(roi.ring_width_px || resolveRingWidthPx())) * scaleX;
  const label = String(roi.label || 'ROI') + (isBg ? ' (BG)' : '');
  if (radius <= 0) return;

  ctx.strokeStyle = roi.color;
  ctx.fillStyle = roi.color;
  ctx.font = 'bold 11px sans-serif';
  for (let r = ringStep; r < radius; r += ringStep) {
    ctx.globalAlpha = 0.6;
    ctx.beginPath();
    ctx.arc(center.x, center.y, r, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.globalAlpha = 1.0;
  ctx.beginPath();
  ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
  ctx.stroke();
  const cross = Math.max(4, Math.min(10, radius * 0.08));
  ctx.beginPath();
  ctx.moveTo(center.x - cross, center.y);
  ctx.lineTo(center.x + cross, center.y);
  ctx.moveTo(center.x, center.y - cross);
  ctx.lineTo(center.x, center.y + cross);
  ctx.stroke();
  ctx.fillText(label, center.x + 5, Math.max(12, center.y - radius - 4));
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'drawConcentricOnCanvas',
  'drawRects',
  'imgToNative',
  'initCanvas',
  'nativeToImg',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
