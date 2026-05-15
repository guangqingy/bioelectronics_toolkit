let _samples = [];
let _selected = new Set();
let _avgData = null;
let _avgB64 = null;
let _yLimits = null;
let _page = 0;
const _perPage = 4;

function baseName(path) {
  return (path || '').split('/').pop() || path;
}

function parseNum(id, fallback) {
  const el = document.getElementById(id);
  const value = el ? Number(el.value) : NaN;
  return Number.isFinite(value) ? value : fallback;
}

function axisState() {
  const x0 = parseNum('cropT0', -0.005);
  const x1 = parseNum('cropT1', 0.020);
  const yMinText = (document.getElementById('yMin')?.value || '').trim();
  const yMaxText = (document.getElementById('yMax')?.value || '').trim();
  return {
    crop_t0: x0,
    crop_t1: x1 > x0 ? x1 : x0 + 0.001,
    x_offset: parseNum('xOffset', 0),
    y_min: yMinText === '' ? null : Number(yMinText),
    y_max: yMaxText === '' ? null : Number(yMaxText),
  };
}

function currentMetadata() {
  const sourcePath = document.getElementById('sourcePath').value.trim();
  return {
    source_path: sourcePath,
    base_dir: document.getElementById('baseDir').value.trim(),
    material: document.getElementById('material').value.trim(),
    index_k: parseInt(document.getElementById('indexK').value, 10) || 1,
    kind: document.getElementById('kind').value,
    chambers: document.getElementById('chambers').value.trim(),
  };
}

function browseBase() {
  const baseDir = document.getElementById('baseDir').value.trim();
  if (!baseDir) return;
  api('/api/echem/lineshape/browse', {base_dir: baseDir}).then(d => {
    if (d.error) {
      toast(d.error, true);
      return;
    }
    if (Array.isArray(d.materials) && d.materials.length) {
      document.getElementById('material').value = d.materials[0];
      setStatus('status', 'Found ' + d.materials.length + ' material folder(s)', 'ok');
    }
  });
}

function loadSamples() {
  const meta = currentMetadata();
  if (!meta.source_path && (!meta.base_dir || !meta.material)) {
    setStatus('status', 'Choose a source file or set advanced directory scan fields', 'error');
    return;
  }
  btnBusy('btnLoad', true, 'Loading...');
  setStatus('status', 'Loading pairs...', 'loading');
  api('/api/echem/lineshape/load', Object.assign({}, meta, axisState()))
    .then(d => {
      btnBusy('btnLoad', false, 'Load Pairs');
      if (d.error) {
        setStatus('status', 'Error: ' + d.error, 'error');
        return;
      }
      _samples = Array.isArray(d.samples) ? d.samples : [];
      _selected = new Set();
      _avgData = null;
      _avgB64 = null;
      _yLimits = Array.isArray(d.y_limits) ? d.y_limits : null;
      _page = 0;
      if (_yLimits && !document.getElementById('yMin').value && !document.getElementById('yMax').value) {
        document.getElementById('yMin').placeholder = _yLimits[0].toPrecision(4);
        document.getElementById('yMax').placeholder = _yLimits[1].toPrecision(4);
      }
      document.getElementById('sampleListSection').style.display = '';
      const sourceLabel = d.source_path ? baseName(d.source_path) : `${meta.material} · index ${meta.index_k}`;
      if (d.kind) document.getElementById('kind').value = d.kind;
      document.getElementById('fileInfo').textContent =
        `${sourceLabel} · ${d.kind || meta.kind} · ${_samples.length} pair segment(s)`;
      renderSampleList();
      renderPreviewPage();
      clearAverage();
      const warningSuffix = Array.isArray(d.warnings) && d.warnings.length ? ` · ${d.warnings.length} skipped` : '';
      setStatus('status', `${_samples.length} pair segment(s) loaded${warningSuffix}`, 'ok');
    })
    .catch(e => {
      btnBusy('btnLoad', false, 'Load Pairs');
      setStatus('status', 'Error: ' + e.message, 'error');
    });
}

function renderSampleList() {
  const el = document.getElementById('sampleList');
  document.getElementById('sampleCount').textContent = `${_selected.size} / ${_samples.length}`;
  if (!_samples.length) {
    el.innerHTML = '<div class="file-list-empty">No samples loaded</div>';
    return;
  }
  el.innerHTML = _samples.map((s, i) => {
    const active = _selected.has(i);
    return `<div class="file-item${active ? ' active' : ''}" onclick="DP.page.toggleSample(${i})" data-idx="${i}" title="${escHtml(s.file || '')}">${active ? '✓ ' : ''}${escHtml(s.label || s.device || 'sample')}</div>`;
  }).join('');
}

function svgPathForSample(sample, axes, yLimits) {
  const width = 300;
  const height = 150;
  const pad = 10;
  const x0 = axes.crop_t0;
  const x1 = axes.crop_t1;
  const y0 = yLimits[0];
  const y1 = yLimits[1];
  const t = Array.isArray(sample.t) ? sample.t : [];
  const y = Array.isArray(sample.y) ? sample.y : [];
  const points = [];
  for (let i = 0; i < Math.min(t.length, y.length); i += 1) {
    const tx = Number(t[i]) + axes.x_offset;
    const yy = Number(y[i]);
    if (!Number.isFinite(tx) || !Number.isFinite(yy) || tx < x0 || tx > x1) continue;
    const px = pad + ((tx - x0) / (x1 - x0 || 1)) * (width - pad * 2);
    const py = height - pad - ((yy - y0) / (y1 - y0 || 1)) * (height - pad * 2);
    points.push(`${px.toFixed(2)},${py.toFixed(2)}`);
  }
  if (!points.length) return '';
  return 'M' + points.join(' L');
}

function renderPreviewPage() {
  const grid = document.getElementById('previewGrid');
  const pageCount = Math.max(1, Math.ceil(_samples.length / _perPage));
  _page = Math.max(0, Math.min(_page, pageCount - 1));
  document.getElementById('pageLabel').textContent = `Page ${_page + 1} / ${pageCount}`;
  const axes = axisState();
  const manualY = Number.isFinite(axes.y_min) && Number.isFinite(axes.y_max) && axes.y_max > axes.y_min;
  const yLimits = manualY ? [axes.y_min, axes.y_max] : (_yLimits || [-1, 1]);
  const start = _page * _perPage;
  const cards = [];
  for (let slot = 0; slot < _perPage; slot += 1) {
    const idx = start + slot;
    const sample = _samples[idx];
    if (!sample) {
      cards.push('<div class="lineshape-mini empty"><div class="lineshape-mini-title">Empty</div></div>');
      continue;
    }
    const active = _selected.has(idx);
    const path = svgPathForSample(sample, axes, yLimits);
    const zeroX = 10 + ((0 - axes.crop_t0) / (axes.crop_t1 - axes.crop_t0 || 1)) * 280;
    cards.push(`
      <button class="lineshape-mini${active ? ' active' : ''}" type="button" onclick="DP.page.toggleSample(${idx})">
        <div class="lineshape-mini-title">${active ? '✓ ' : ''}${escHtml(sample.label || 'sample')}</div>
        <svg viewBox="0 0 300 150" role="img" aria-label="${escHtml(sample.label || 'sample')}">
          <line x1="${zeroX.toFixed(2)}" x2="${zeroX.toFixed(2)}" y1="8" y2="142" stroke="#D0D1D2" stroke-width="1" stroke-dasharray="3 3"></line>
          <path d="${path}" fill="none" stroke="${active ? '#3E6AE1' : '#A9ADB5'}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
        </svg>
      </button>
    `);
  }
  grid.innerHTML = cards.join('');
}

function clearAverage(message) {
  _avgData = null;
  _avgB64 = null;
  document.getElementById('avgPlotArea').innerHTML = `<div class="plot-placeholder">${escHtml(message || 'No samples selected')}</div>`;
  document.getElementById('avgInfo').textContent = '';
  document.getElementById('btnExportFiles').style.display = 'none';
  document.getElementById('btnDownloadPNG').style.display = 'none';
}

function toggleSample(i) {
  if (_selected.has(i)) _selected.delete(i);
  else _selected.add(i);
  renderSampleList();
  renderPreviewPage();
  updatePlot();
}

function selectAll(val) {
  if (val) _samples.forEach((_, i) => _selected.add(i));
  else _selected.clear();
  renderSampleList();
  renderPreviewPage();
  updatePlot();
}

function prevPage() {
  if (_page > 0) {
    _page -= 1;
    renderPreviewPage();
  }
}

function nextPage() {
  const pageCount = Math.max(1, Math.ceil(_samples.length / _perPage));
  if (_page < pageCount - 1) {
    _page += 1;
    renderPreviewPage();
  }
}

function applyAutoY() {
  if (!_yLimits) return;
  document.getElementById('yMin').value = _yLimits[0].toPrecision(8);
  document.getElementById('yMax').value = _yLimits[1].toPrecision(8);
  renderPreviewPage();
  updatePlot();
}

function resetX() {
  document.getElementById('cropT0').value = '-0.005';
  document.getElementById('cropT1').value = '0.020';
  renderPreviewPage();
  updatePlot();
}

function updatePlot() {
  if (!_samples.length) {
    clearAverage('No samples loaded');
    return;
  }
  const selected = Array.from(_selected).sort((a, b) => a - b);
  if (!selected.length) {
    clearAverage('No samples selected');
    setStatus('status', 'Loaded segments; select one or more samples to average', 'ok');
    return;
  }
  setStatus('status', 'Rendering average...', 'loading');
  api('/api/echem/lineshape/plot', {
    samples: _samples.map(s => ({label: s.label, t: s.t, y: s.y, file: s.file})),
    selected,
    kind: document.getElementById('kind').value,
    ...axisState(),
  }).then(d => {
    if (d.error) {
      clearAverage(d.error);
      setStatus('status', 'Error: ' + d.error, 'error');
      return;
    }
    _avgB64 = d.avg_img;
    _avgData = d.avg_data;
    setPlot('avgPlotArea', d.avg_img);
    document.getElementById('avgInfo').textContent = `n=${d.n_selected}`;
    document.getElementById('btnExportFiles').style.display = '';
    document.getElementById('btnDownloadPNG').style.display = '';
    setStatus('status', `Averaged ${d.n_selected} selected segment(s)`, 'ok');
  }).catch(e => {
    clearAverage(e.message);
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function exportFiles() {
  if (!_avgData) {
    setStatus('status', 'No average to export', 'error');
    return;
  }
  const meta = currentMetadata();
  const outputsBefore = _samples.map(s => ({path: s.file, role: 'segment'})).filter(s => s.path);
  btnBusy('btnExportFiles', true, 'Exporting...');
  dpRunJobEndpoint('/api/echem/lineshape/export_avg_job', {
    ...meta,
    ...axisState(),
    avg_data: _avgData,
    output_dir: document.getElementById('outputDir').value.trim(),
    dpi: parseInt(document.getElementById('exportDpi').value, 10) || 300,
    selected_count: _selected.size,
    mode: 'save',
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? Math.round(job.progress * 100) : null;
      setStatus('status', job.message || 'Exporting...', 'loading', pct);
    },
  }).then(d => {
    btnBusy('btnExportFiles', false, 'Export CSV + PNG + SVG');
    const outputs = Array.isArray(d.outputs) ? d.outputs : [];
    const outputDir = d.output_dir || '';
    setStatus('status', `Exported ${outputs.length || 3} file(s)${outputDir ? ': ' + outputDir : ''}`, 'ok');
    recordRunHistory({
      view: 'echem_lineshape',
      title: 'EChem Lineshape Average',
      status: 'ok',
      project_root: meta.base_dir || dpPathDir(meta.source_path),
      input_files: meta.source_path ? [{path: meta.source_path, role: 'source_echem'}].concat(outputsBefore) : outputsBefore,
      outputs,
      parameters: Object.assign({}, meta, axisState(), {
        selected_count: _selected.size,
        output_dir: document.getElementById('outputDir').value.trim() || 'plots_shape_average',
      }),
    });
  }).catch(e => {
    btnBusy('btnExportFiles', false, 'Export CSV + PNG + SVG');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function downloadPNG() {
  if (!_avgB64) return;
  const meta = currentMetadata();
  const sourceStem = meta.source_path ? baseName(meta.source_path).replace(/\.[^.]+$/, '') : '';
  const a = Object.assign(document.createElement('a'), {
    href: 'data:image/png;base64,' + _avgB64,
    download: sourceStem ? `shape_${sourceStem}_avg.png` : `shape_${meta.material || 'material'}_idx${meta.index_k}_${meta.kind}_avg.png`,
  });
  a.click();
}

['cropT0', 'cropT1', 'xOffset', 'yMin', 'yMax'].forEach(id => {
  window.addEventListener('load', () => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', () => {
      renderPreviewPage();
      updatePlot();
    });
  });
});

window.addEventListener('load', () => {
  renderPreviewPage();
  setStatus('status', 'Ready', 'ok');
  const sourcePath = new URLSearchParams(window.location.search).get('path');
  if (sourcePath) {
    document.getElementById('sourcePath').value = sourcePath;
    loadSamples();
  }
});

window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'applyAutoY',
  'browseBase',
  'downloadPNG',
  'exportFiles',
  'loadSamples',
  'nextPage',
  'prevPage',
  'resetX',
  'selectAll',
  'toggleSample',
  'updatePlot',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
