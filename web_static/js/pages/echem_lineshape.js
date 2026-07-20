let _samples = [];
let _selected = new Set();
let _avgData = null;
let _avgB64 = null;
let _yLimits = null;
let _page = 0;
let _sourceFiles = [];
let _selectedSources = [];
let _lastSourceIndex = null;
let _lastSampleIndex = null;
const _perPage = 4;

function baseName(path) {
  return (path || '').split('/').pop() || path;
}

function normalizePath(path) {
  return String(path || '').trim();
}

function sourceKey(path) {
  return normalizePath(path);
}

function updateSourceCount() {
  const el = document.getElementById('sourceCount');
  if (el) el.textContent = `${_selectedSources.length} selected`;
}

function renderSourceFiles() {
  const el = document.getElementById('sourceFileList');
  if (!el) return;
  if (!_sourceFiles.length) {
    el.innerHTML = '<div class="file-list-empty">No exported pair/pulse segments found. '
      + 'Run detection and “Summary + Pair/Pulse Files” in Photocurrent or Photovoltage first.</div>';
    updateSourceCount();
    if (typeof dpApplyFileListFilter === 'function') dpApplyFileListFilter('sourceFileList');
    return;
  }
  const selected = new Set(_selectedSources.map(item => sourceKey(item.path)));
  el.innerHTML = _sourceFiles.map((file, i) => {
    const active = selected.has(sourceKey(file.path));
    const count = Number(file.segment_count || 0);
    const suffix = count ? ` · ${count}` : '';
    return `<div class="file-item${active ? ' active' : ''}" data-idx="${i}" data-path="${dpEscapeHtml(file.path)}" title="${dpEscapeHtml(file.path)}" data-dp-click="DP.page.toggleSourceIndex(${i}, event)">${active ? '✓ ' : ''}${dpEscapeHtml(file.name || baseName(file.path))}${suffix}</div>`;
  }).join('');
  updateSourceCount();
  if (typeof dpApplyFileListFilter === 'function') dpApplyFileListFilter('sourceFileList');
}

function renderSelectedSources() {
  updateSourceCount();
}

function addSourceRecord(file) {
  const path = normalizePath(file && file.path ? file.path : file);
  if (!path) return;
  if (!_sourceFiles.some(item => sourceKey(item.path) === sourceKey(path))) {
    _sourceFiles.push(Object.assign({name: baseName(path), path}, file && typeof file === 'object' ? file : {}));
  }
  if (_selectedSources.some(item => sourceKey(item.path) === sourceKey(path))) {
    renderSourceFiles();
    renderSelectedSources();
    return;
  }
  _selectedSources.push(Object.assign({name: baseName(path), path}, file && typeof file === 'object' ? file : {}));
  document.getElementById('sourcePath').value = '';
  renderSourceFiles();
  renderSelectedSources();
  setStatus('status', `${_selectedSources.length} source file(s) selected`, 'ok');
}

function toggleSourceIndex(i, event) {
  const file = _sourceFiles[i];
  if (!file) return;
  if (event && event.shiftKey && _lastSourceIndex !== null && _lastSourceIndex >= 0 && _lastSourceIndex < _sourceFiles.length) {
    const start = Math.min(_lastSourceIndex, i);
    const end = Math.max(_lastSourceIndex, i);
    const selected = new Set(_selectedSources.map(item => sourceKey(item.path)));
    for (let j = start; j <= end; j += 1) {
      const candidate = _sourceFiles[j];
      if (candidate && !selected.has(sourceKey(candidate.path))) {
        _selectedSources.push(candidate);
        selected.add(sourceKey(candidate.path));
      }
    }
    _lastSourceIndex = i;
    renderSourceFiles();
    renderSelectedSources();
    setStatus('status', `${_selectedSources.length} source file(s) selected`, 'ok');
    return;
  }
  const existing = _selectedSources.findIndex(item => sourceKey(item.path) === sourceKey(file.path));
  if (existing >= 0) _selectedSources.splice(existing, 1);
  else _selectedSources.push(file);
  _lastSourceIndex = i;
  renderSourceFiles();
  renderSelectedSources();
  setStatus('status', `${_selectedSources.length} source file(s) selected`, 'ok');
}

function addSourceFromPath() {
  const path = normalizePath(document.getElementById('sourcePath').value);
  if (!path) {
    setStatus('status', 'Choose or paste a source file first', 'error');
    return;
  }
  addSourceRecord({name: baseName(path), path});
}

function removeSource(i) {
  if (i < 0 || i >= _selectedSources.length) return;
  _selectedSources.splice(i, 1);
  renderSourceFiles();
  renderSelectedSources();
  setStatus('status', `${_selectedSources.length} source file(s) selected`, 'ok');
}

function clearSources() {
  _selectedSources = [];
  _lastSourceIndex = null;
  renderSourceFiles();
  renderSelectedSources();
  setStatus('status', 'Selected files cleared', 'ok');
}

function selectAllSources(value) {
  _selectedSources = value ? _sourceFiles.map(file => Object.assign({}, file)) : [];
  _lastSourceIndex = value && _sourceFiles.length ? 0 : null;
  renderSourceFiles();
  renderSelectedSources();
  setStatus('status', `${_selectedSources.length} source file(s) selected`, 'ok');
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
  const selectedSourcePaths = _selectedSources.map(item => item.path);
  return {
    source_path: selectedSourcePaths[0] || sourcePath,
    source_paths: selectedSourcePaths,
    source_folder: document.getElementById('sourceFolder').value.trim(),
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

function scanSourceFolder() {
  const folder = document.getElementById('sourceFolder').value.trim();
  if (!folder) {
    setStatus('status', 'Choose a source folder first', 'error');
    return;
  }
  setStatus('status', 'Scanning source files...', 'loading');
  api('/api/echem/lineshape/source_browse', {
    folder,
    kind: document.getElementById('kind').value,
  }).then(d => {
    if (d.error) throw new Error(d.error);
    _sourceFiles = Array.isArray(d.files) ? d.files : [];
    _lastSourceIndex = null;
    const available = new Set(_sourceFiles.map(item => sourceKey(item.path)));
    _selectedSources = _selectedSources.filter(item => available.has(sourceKey(item.path)));
    renderSourceFiles();
    setStatus('status', `${_sourceFiles.length} source file(s) found`, 'ok');
  }).catch(e => {
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function loadSamples() {
  const meta = currentMetadata();
  if (!meta.source_paths.length && !meta.source_path && (!meta.base_dir || !meta.material)) {
    setStatus('status', 'Add source files or set advanced directory scan fields', 'error');
    return;
  }
  btnBusy('btnLoad', true, 'Loading...');
  setStatus('status', 'Loading pairs...', 'loading');
  api('/api/echem/lineshape/load', Object.assign({}, meta, axisState()))
    .then(d => {
      btnBusy('btnLoad', false, '3 Load & Average');
      if (d.error) {
        setStatus('status', 'Error: ' + d.error, 'error');
        return;
      }
      _samples = Array.isArray(d.samples) ? d.samples : [];
      _selected = new Set(_samples.map((_, i) => i));
      _lastSampleIndex = _samples.length ? 0 : null;
      _avgData = null;
      _avgB64 = null;
      _yLimits = Array.isArray(d.y_limits) ? d.y_limits : null;
      _page = 0;
      if (_yLimits && !document.getElementById('yMin').value && !document.getElementById('yMax').value) {
        document.getElementById('yMin').placeholder = _yLimits[0].toPrecision(4);
        document.getElementById('yMax').placeholder = _yLimits[1].toPrecision(4);
      }
      document.getElementById('sampleListSection').style.display = '';
      const nSources = Array.isArray(d.source_paths) ? d.source_paths.length : (d.source_path ? 1 : 0);
      const sourceLabel = nSources > 1 ? `${nSources} source files` : (d.source_path ? baseName(d.source_path) : `${meta.material} · index ${meta.index_k}`);
      if (d.kind) document.getElementById('kind').value = d.kind;
      document.getElementById('fileInfo').textContent =
        `${sourceLabel} · ${d.kind || meta.kind} · ${_samples.length} pair segment(s)`;
      renderSampleList();
      renderPreviewPage();
      const warningSuffix = Array.isArray(d.warnings) && d.warnings.length ? ` · ${d.warnings.length} skipped` : '';
      setStatus('status', `${_samples.length} pair segment(s) loaded${warningSuffix}`, 'ok');
      updatePlot();
    })
    .catch(e => {
      btnBusy('btnLoad', false, '3 Load & Average');
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
    return `<div class="file-item${active ? ' active' : ''}" data-dp-click="DP.page.toggleSample(${i}, event)" data-idx="${i}" title="${dpEscapeHtml(s.file || '')}">${active ? '✓ ' : ''}${dpEscapeHtml(s.label || s.device || 'sample')}</div>`;
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
      <button class="lineshape-mini${active ? ' active' : ''}" type="button" data-dp-click="DP.page.toggleSample(${idx}, event)">
        <div class="lineshape-mini-title">${active ? '✓ ' : ''}${dpEscapeHtml(sample.label || 'sample')}</div>
        <svg viewBox="0 0 300 150" role="img" aria-label="${dpEscapeHtml(sample.label || 'sample')}">
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
  if (typeof dpDestroyTrace === 'function') dpDestroyTrace('avgPlotArea');
  document.getElementById('avgPlotArea').innerHTML = `<div class="plot-placeholder">${dpEscapeHtml(message || 'No samples selected')}</div>`;
  document.getElementById('avgInfo').textContent = '';
  document.getElementById('btnExportFiles').style.display = 'none';
  document.getElementById('btnDownloadPNG').style.display = 'none';
}

function toggleSample(i, event) {
  if (event && event.shiftKey && _lastSampleIndex !== null && _lastSampleIndex >= 0 && _lastSampleIndex < _samples.length) {
    const start = Math.min(_lastSampleIndex, i);
    const end = Math.max(_lastSampleIndex, i);
    for (let j = start; j <= end; j += 1) _selected.add(j);
  } else if (_selected.has(i)) {
    _selected.delete(i);
  } else {
    _selected.add(i);
  }
  _lastSampleIndex = i;
  renderSampleList();
  renderPreviewPage();
  updatePlot();
}

function selectAll(val) {
  if (val) _samples.forEach((_, i) => _selected.add(i));
  else _selected.clear();
  _lastSampleIndex = val && _samples.length ? 0 : null;
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
  const payload = {
    samples: _samples.map(s => ({label: s.label, t: s.t, y: s.y, file: s.file})),
    selected,
    kind: document.getElementById('kind').value,
    ...axisState(),
  };
  if (typeof dpUplotAvailable !== 'function' || !dpUplotAvailable()) {
    plotPngAverage(payload);
    return;
  }
  api('/api/echem/lineshape/trace_data', payload).then(d => {
    if (d.error) throw new Error(d.error);
    _avgB64 = null;
    _avgData = d.avg_data;
    dpRenderTrace('avgPlotArea', d);
    document.getElementById('avgInfo').textContent = `n=${d.n_selected}`;
    document.getElementById('btnExportFiles').style.display = '';
    document.getElementById('btnDownloadPNG').style.display = 'none';
    setStatus('status', `Averaged ${d.n_selected} selected segment(s)`, 'ok');
  }).catch(() => plotPngAverage(payload));
}

function plotPngAverage(payload) {
  api('/api/echem/lineshape/plot', payload).then(d => {
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
  const selectedIndices = Array.from(_selected).sort((a, b) => a - b);
  const selectedSegments = selectedIndices.map((idx, order) => {
    const sample = _samples[idx] || {};
    return {
      selected_order: order + 1,
      sample_index: idx,
      label: sample.label || '',
      file: sample.file || '',
      source: sample.source || '',
      device: sample.device || '',
    };
  });
  const outputsBefore = selectedSegments
    .map(s => ({path: s.file, role: 'averaged_segment'}))
    .filter(s => s.path);
  btnBusy('btnExportFiles', true, 'Exporting...');
  dpRunJobEndpoint('/api/echem/lineshape/export_avg_job', {
    ...meta,
    ...axisState(),
    avg_data: _avgData,
    selected_segments: selectedSegments,
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
    btnBusy('btnExportFiles', false, 'Export CSV + PNG + SVG + Sources');
    const outputs = Array.isArray(d.outputs) ? d.outputs : [];
    const outputDir = d.output_dir || '';
    const sourceInputs = meta.source_paths.length
      ? _selectedSources.map(item => ({path: item.path, role: 'source_echem'}))
      : (meta.source_path ? [{path: meta.source_path, role: 'source_echem'}] : []);
    setStatus('status', `Exported ${outputs.length || 3} file(s)${outputDir ? ': ' + outputDir : ''}`, 'ok');
    recordRunHistory({
      view: 'echem_lineshape',
      title: 'EChem Waveform Averager',
      status: 'ok',
      project_root: meta.source_folder || meta.base_dir || dpPathDir(meta.source_path),
      input_files: sourceInputs.concat(outputsBefore),
      outputs,
      parameters: Object.assign({}, meta, axisState(), {
        selected_count: _selected.size,
        selected_segments: selectedSegments,
        output_dir: document.getElementById('outputDir').value.trim() || 'plots_shape_average',
      }),
    });
  }).catch(e => {
    btnBusy('btnExportFiles', false, 'Export CSV + PNG + SVG + Sources');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function downloadPNG() {
  if (!_avgB64) return;
  const meta = currentMetadata();
  let sourceStem = '';
  if (meta.source_paths.length > 1) {
    sourceStem = `${baseName(meta.source_paths[0]).replace(/\.[^.]+$/, '')}_plus${meta.source_paths.length - 1}`;
  } else if (meta.source_paths.length === 1) {
    sourceStem = baseName(meta.source_paths[0]).replace(/\.[^.]+$/, '');
  } else if (meta.source_path) {
    sourceStem = baseName(meta.source_path).replace(/\.[^.]+$/, '');
  }
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
  renderSourceFiles();
  renderSelectedSources();
  renderPreviewPage();
  setStatus('status', 'Ready', 'ok');
  const params = new URLSearchParams(window.location.search);
  const sourceFolder = params.get('folder');
  const sourcePath = params.get('path');
  if (sourceFolder) {
    document.getElementById('sourceFolder').value = sourceFolder;
    scanSourceFolder();
  }
  if (sourcePath) {
    document.getElementById('sourcePath').value = sourcePath;
    addSourceFromPath();
    loadSamples();
  }
});

window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'applyAutoY',
  'addSourceFromPath',
  'browseBase',
  'clearSources',
  'downloadPNG',
  'exportFiles',
  'loadSamples',
  'nextPage',
  'prevPage',
  'removeSource',
  'resetX',
  'scanSourceFolder',
  'selectAllSources',
  'selectAll',
  'toggleSample',
  'toggleSourceIndex',
  'updatePlot',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
