let _currentFile = null;
let _pulses = [];
let _lastWindow = null;
let _lastParams = null;

window.dpCurrentFilePath = () => _currentFile || '';
window.dpCurrentProjectRoot = () => document.getElementById('folderPath').value.trim();
window.dpCollectFileProfilePayload = () => ({
  pulses: _pulses.map(p => ({...p})),
  lastWindow: _lastWindow,
  lastParams: _lastParams,
});
window.dpApplyFileProfilePayload = payload => {
  if (!payload || typeof payload !== 'object') return;
  if (Array.isArray(payload.pulses)) _pulses = payload.pulses.map(p => ({...p}));
  _lastWindow = payload.lastWindow || null;
  _lastParams = payload.lastParams || null;
  updateTable();
};
window.dpAfterFileProfileApplied = () => {
  toggleWindow(document.getElementById('useAll').checked);
  updateTable();
};
window.dpApplyRunManifest = manifest => {
  dpApplyRunManifestFallback(manifest);
  const inputPath = dpFirstManifestInput(manifest);
  if (inputPath) {
    _currentFile = inputPath;
    document.getElementById('folderPath').value = manifest.project_root || dpPathDir(inputPath);
    document.getElementById('fileInfo').textContent = baseName(inputPath) + ' · parameters loaded from run manifest';
  }
  const params = manifest.parameters || {};
  if (Array.isArray(params.pulses)) _pulses = params.pulses.map(p => ({...p}));
  if (params.window) _lastWindow = params.window;
  _lastParams = params.params || params;
  toggleWindow(document.getElementById('useAll').checked);
  updateTable();
};

function baseName(p) { return (p || '').split('/').pop() || p; }
function num(v, d) { return Number(v || 0).toFixed(d); }

function toggleWindow(useAll) {
  const row = document.getElementById('windowRow');
  row.style.opacity = useAll ? '0.35' : '1';
  row.querySelectorAll('input').forEach(el => el.disabled = useAll);
}

function plotTracePreview(path) {
  const payload = {
    path,
    x_min: null,
    x_max: null,
    y_min: null,
    y_max: null,
  };
  if (window.dpUplotAvailable && window.dpUplotAvailable()) {
    return api('/api/echem/photovoltage/trace_data', payload)
      .then(d => {
        if (d.error) throw new Error(d.error);
        if (!window.dpRenderTrace('plotArea', d)) throw new Error('uplot-render-failed');
        return d;
      })
      .catch(() => plotPngPreview(path));
  }
  return plotPngPreview(path);
}

function plotPngPreview(path) {
  if (window.dpDestroyTrace) window.dpDestroyTrace('plotArea');
  return api('/api/echem/photovoltage/load', { path })
    .then(d => {
      if (d.error) throw new Error(d.error);
      setPlot('plotArea', d.img);
      return d;
    });
}

function scanFolder() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) { setStatus('status', 'Enter a folder path', 'error'); return; }
  setStatus('status', 'Scanning…', 'loading');
  api('/api/echem/photovoltage/browse', { folder })
    .then(d => {
      if (d.error) throw new Error(d.error);
      const files = (d.files || []).map(f => typeof f === 'string' ? {name: baseName(f), path: f} : f);
      buildFileList('fileList', files, el => selectFile(el, el.dataset.path));
      if (files.length) selectFile(document.querySelector('#fileList .file-item'), files[0].path);
      setStatus('status', files.length + ' file(s) found', 'ok');
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function selectFile(el, path) {
  document.querySelectorAll('#fileList .file-item').forEach(e => e.classList.remove('active'));
  if (el) el.classList.add('active');
  _currentFile = path;
  _pulses = [];
  _lastWindow = null;
  _lastParams = null;
  updateTable();
  setStatus('status', 'Loading…', 'loading');
  plotTracePreview(path)
    .then(d => {
      document.getElementById('t1').value = d.duration || 10;
      document.getElementById('fileInfo').textContent =
        baseName(path)
        + (d.n_points ? ' · ' + d.n_points + ' pts' : '')
        + (d.duration ? ' · ' + Number(d.duration).toFixed(1) + ' s' : '');
      loadGenericFileProfileForCurrent(true).finally(() => setStatus('status', 'Ready', 'ok'));
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function detect() {
  if (!_currentFile) { setStatus('status', 'No file selected', 'error'); return; }
  const useAll = document.getElementById('useAll').checked;
  const t0 = parseFloat(document.getElementById('t0').value);
  const t1 = parseFloat(document.getElementById('t1').value);
  const baseline_method = document.getElementById('detrendMethod').value;
  const baseline_win_ms = parseFloat(document.getElementById('baselineWinMs').value);
  const sg_window_ms = parseFloat(document.getElementById('sgWinMs').value);
  const sg_poly = parseInt(document.getElementById('sgPoly').value, 10);
  const peak_min_v = parseFloat(document.getElementById('peakMinV').value);
  const min_width_ms = parseFloat(document.getElementById('minWidthMs').value);
  const min_spacing_ms = parseFloat(document.getElementById('minSpacingMs').value);
  const polarity = document.getElementById('polarity').value;
  const show_detrended = document.getElementById('showDetrended').checked;

  btnBusy('btnDetect', true, 'Detecting…');
  setStatus('status', 'Detecting pulses…', 'loading');
  api('/api/echem/photovoltage/detect', {
    path: _currentFile,
    t0,
    t1,
    use_all: useAll,
    baseline_method,
    baseline_win_ms,
    sg_window_ms,
    sg_poly,
    peak_min_v,
    min_width_ms,
    min_spacing_ms,
    polarity,
    show_detrended
  }).then(d => {
    if (d.error) throw new Error(d.error);
    if (window.dpDestroyTrace) window.dpDestroyTrace('plotArea');
    setPlot('plotArea', d.img);
    _pulses = (d.pulses || []).map(p => Object.assign({}, p, {_removed: false}));
    _lastWindow = d.window || null;
    _lastParams = d.params || null;
    updateTable();
    setStatus('status', 'Detected ' + _pulses.length + ' pulse(s)', 'ok');
  }).catch(e => setStatus('status', 'Error: ' + e.message, 'error'))
    .finally(() => btnBusy('btnDetect', false, 'Detect Pulses'));
}

function activePulses() {
  return _pulses.filter(p => !p._removed);
}

function togglePulseRemoved(idx) {
  if (idx < 0 || idx >= _pulses.length) return;
  _pulses[idx]._removed = !_pulses[idx]._removed;
  updateTable();
}

function restoreAllPulses() {
  if (!_pulses.length) {
    setStatus('status', 'No detection results to restore', 'error');
    return;
  }
  _pulses.forEach(p => { p._removed = false; });
  updateTable();
  setStatus('status', 'All pulses restored', 'ok');
}

function updateTable() {
  const tbody = document.querySelector('#pulsesTable tbody');
  const card  = document.getElementById('pulsesCard');
  if (!_pulses.length) { card.style.display = 'none'; tbody.innerHTML = ''; return; }
  card.style.display = '';
  const nActive = activePulses().length;
  document.getElementById('pulsesCount').textContent = nActive + ' / ' + _pulses.length + ' active';
  tbody.innerHTML = _pulses.map((p, i) => {
    const removed = !!p._removed;
    const rowStyle = removed ? ' style="opacity:0.45;text-decoration:line-through"' : '';
    const btnLabel = removed ? 'Undo' : 'Remove';
    const amp = (p.amp_det_V !== undefined) ? p.amp_det_V : (p.amplitude ?? p.height);
    const width = (p.width_ms !== undefined) ? p.width_ms : p.duration;
    const pol = p.polarity_label ?? ((p.polarity ?? 1) >= 0 ? 'Pos' : 'Neg');
    return `<tr${rowStyle}><td>${i+1}</td><td>${num(p.time ?? p.t, 3)}</td><td>${pol}</td><td>${num(amp, 4)}</td><td>${num(width, 3)}</td><td><button class="btn-secondary" style="padding:2px 8px;min-height:24px" data-dp-click="togglePulseRemoved(${i})">${btnLabel}</button></td></tr>`;
  }).join('');
}

function exportCSV() {
  exportSegments();
}

function figureExportPayload(fmt) {
  return {
    path: _currentFile,
    fmt,
    pulses: activePulses(),
    window: _lastWindow,
    params: _lastParams || {},
    show_detrended: document.getElementById('showDetrended').checked,
    baseline_method: document.getElementById('detrendMethod').value,
    baseline_win_ms: parseFloat(document.getElementById('baselineWinMs').value),
    sg_window_ms: parseFloat(document.getElementById('sgWinMs').value),
    sg_poly: parseInt(document.getElementById('sgPoly').value, 10),
    dpi: 300
  };
}

function exportFigure(fmt) {
  if (!_currentFile) { setStatus('status', 'No file selected', 'error'); return; }
  const payload = figureExportPayload(fmt);
  setStatus('status', 'Saving ' + fmt.toUpperCase() + ' figure...', 'loading');
  dpRunJobEndpoint('/api/echem/photovoltage/export_figure_job', payload, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Saving ${fmt.toUpperCase()} figure${pct}${msg}`, 'loading');
    },
  }).then(d => {
    setStatus('status', 'Saved figure: ' + (d.saved_path || 'ok'), 'ok');
    recordRunHistory({
      view: 'echem_photovoltage',
      title: 'EChem Photovoltage Figure Export',
      status: 'ok',
      project_root: document.getElementById('folderPath').value.trim(),
      input_files: [{path: _currentFile, role: 'source_echem'}],
      outputs: dpAsPathRecords(d.saved_path ? [d.saved_path] : [], 'photovoltage_preview_' + fmt),
      parameters: payload,
      metadata: {format: fmt},
    });
  }).catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function exportSegments() {
  const pulses = activePulses();
  if (!_currentFile || !pulses.length) { setStatus('status', 'No active pulses to export', 'error'); return; }
  setStatus('status', 'Saving CSV...', 'loading');

  const payload = {
    path: _currentFile,
    pulses,
    mode: 'save',
    window: _lastWindow,
    params: _lastParams,
    pulse_window_ms: 50
  };

  if (!_lastParams) {
    payload.baseline_method = document.getElementById('detrendMethod').value;
    payload.baseline_win_ms = parseFloat(document.getElementById('baselineWinMs').value);
    payload.sg_window_ms = parseFloat(document.getElementById('sgWinMs').value);
    payload.sg_poly = parseInt(document.getElementById('sgPoly').value, 10);
    payload.peak_min_v = parseFloat(document.getElementById('peakMinV').value);
    payload.min_width_ms = parseFloat(document.getElementById('minWidthMs').value);
    payload.min_spacing_ms = parseFloat(document.getElementById('minSpacingMs').value);
  }

  dpRunJobEndpoint('/api/echem/photovoltage/export_job', payload, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Saving pulse exports${pct}${msg}`, 'loading');
    },
  }).then(d => {
    setStatus('status', 'Saved summary + ' + (d.saved_count || 0) + ' pulse file(s): ' + (d.saved_path || 'ok'), 'ok');
    recordRunHistory({
      view: 'echem_photovoltage',
      title: 'EChem Photovoltage Pulse Export',
      status: 'ok',
      project_root: document.getElementById('folderPath').value.trim(),
      input_files: [{path: _currentFile, role: 'source_echem'}],
      outputs: dpAsPathRecords(d.saved_paths || (d.summary_path ? [d.summary_path] : []), 'photovoltage_pulse_output'),
      parameters: payload,
      metadata: {
        saved_folder: d.saved_path || '',
        saved_count: d.saved_count || 0,
      },
    });
  }).catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function openWaveformAverager() {
  const params = new URLSearchParams();
  const folder = document.getElementById('folderPath').value.trim() || dpPathDir(_currentFile);
  if (folder) params.set('folder', folder);
  if (_currentFile) params.set('path', _currentFile);
  window.location.href = '/echem/lineshape' + (params.toString() ? `?${params.toString()}` : '');
}

window.addEventListener('load', () => setStatus('status', 'Ready', 'ok'));

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'activePulses',
  'baseName',
  'detect',
  'exportCSV',
  'exportFigure',
  'exportSegments',
  'figureExportPayload',
  'num',
  'openWaveformAverager',
  'plotPngPreview',
  'plotTracePreview',
  'restoreAllPulses',
  'scanFolder',
  'selectFile',
  'togglePulseRemoved',
  'toggleWindow',
  'updateTable',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
