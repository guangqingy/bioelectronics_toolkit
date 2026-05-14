let _currentFile = null;
let _pairs = [];
let _lastWindow = null;
let _lastParams = null;

window.dpCurrentFilePath = () => _currentFile || '';
window.dpCurrentProjectRoot = () => document.getElementById('folderPath').value.trim();
window.dpCollectFileProfilePayload = () => ({
  pairs: _pairs.map(p => ({...p})),
  lastWindow: _lastWindow,
  lastParams: _lastParams,
});
window.dpApplyFileProfilePayload = payload => {
  if (!payload || typeof payload !== 'object') return;
  if (Array.isArray(payload.pairs)) _pairs = payload.pairs.map(p => ({...p}));
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
  if (Array.isArray(params.pairs)) _pairs = params.pairs.map(p => ({...p}));
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

function scanFolder() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) { setStatus('status', 'Enter a folder path', 'error'); return; }
  setStatus('status', 'Scanning…', 'loading');
  api('/api/echem/browse', { folder })
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
  _pairs = [];
  _lastWindow = null;
  _lastParams = null;
  updateTable();
  setStatus('status', 'Loading…', 'loading');
  api('/api/echem/load', { path })
    .then(d => {
      if (d.error) throw new Error(d.error);
      setPlot('plotArea', d.img);
      document.getElementById('t1').value = d.duration || 10;
      document.getElementById('fileInfo').textContent = baseName(path) + (d.n_points ? ' · ' + d.n_points + ' pts' : '') + (d.duration ? ' · ' + Number(d.duration).toFixed(1) + ' s' : '');
      loadGenericFileProfileForCurrent(true).finally(() => setStatus('status', 'Ready', 'ok'));
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function detect() {
  if (!_currentFile) { setStatus('status', 'No file selected', 'error'); return; }
  const useAll = document.getElementById('useAll').checked;
  const t0 = parseFloat(document.getElementById('t0').value);
  const t1 = parseFloat(document.getElementById('t1').value);
  const pos_min_mA = parseFloat(document.getElementById('posMin').value);
  const neg_min_abs_mA = parseFloat(document.getElementById('negMin').value);
  const min_delay_ms = parseFloat(document.getElementById('minDelay').value);
  const max_delay_ms = parseFloat(document.getElementById('maxDelay').value);
  const min_pos_distance_ms = parseFloat(document.getElementById('minPosDist').value);
  btnBusy('btnDetect', true, 'Detecting…');
  setStatus('status', 'Detecting pairs…', 'loading');
  api('/api/echem/detect', {
    path: _currentFile,
    t0,
    t1,
    pos_min_mA,
    neg_min_abs_mA,
    min_delay_ms,
    max_delay_ms,
    min_pos_distance_ms,
    use_all: useAll
  })
    .then(d => {
      if (d.error) throw new Error(d.error);
      setPlot('plotArea', d.img);
      _pairs = (d.pairs || []).map(p => Object.assign({}, p, {_removed: false}));
      _lastWindow = d.window || null;
      _lastParams = d.params || null;
      updateTable();
      setStatus('status', 'Detected ' + _pairs.length + ' pair(s)', 'ok');
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'))
    .finally(() => btnBusy('btnDetect', false, 'Detect Pairs'));
}

function activePairs() {
  return _pairs.filter(p => !p._removed);
}

function togglePairRemoved(idx) {
  if (idx < 0 || idx >= _pairs.length) return;
  _pairs[idx]._removed = !_pairs[idx]._removed;
  updateTable();
}

function restoreAllPairs() {
  if (!_pairs.length) {
    setStatus('status', 'No detection results to restore', 'error');
    return;
  }
  _pairs.forEach(p => { p._removed = false; });
  updateTable();
  setStatus('status', 'All pairs restored', 'ok');
}

function updateTable() {
  const tbody = document.querySelector('#pairsTable tbody');
  const card  = document.getElementById('pairsCard');
  if (!_pairs.length) { card.style.display = 'none'; tbody.innerHTML = ''; return; }
  card.style.display = '';
  const nActive = activePairs().length;
  document.getElementById('pairsCount').textContent = nActive + ' / ' + _pairs.length + ' active';
  tbody.innerHTML = _pairs.map((p, i) => {
    const t   = p.time  !== undefined ? p.time   : p.t_pos;
    const pos = p.pos_peak !== undefined ? p.pos_peak : p.i_pos;
    const neg = p.neg_peak !== undefined ? p.neg_peak : p.i_neg;
    const dur = p.duration !== undefined ? p.duration : p.delta_t;
    const removed = !!p._removed;
    const rowStyle = removed ? ' style="opacity:0.45;text-decoration:line-through"' : '';
    const btnLabel = removed ? 'Undo' : 'Remove';
    return `<tr${rowStyle}><td>${i+1}</td><td>${num(t,3)}</td><td>${num(pos,2)}</td><td>${num(neg,2)}</td><td>${num(dur,2)}</td><td><button class="btn-secondary" style="padding:2px 8px;min-height:24px" onclick="togglePairRemoved(${i})">${btnLabel}</button></td></tr>`;
  }).join('');
}

function exportCSV() {
  exportSegments();
}

function exportSegments() {
  const pairs = activePairs();
  if (!_currentFile || !pairs.length) { setStatus('status', 'No active pairs to export', 'error'); return; }
  setStatus('status', 'Saving CSV...', 'loading');

  const payload = {
    path: _currentFile,
    pairs,
    mode: 'save',
    window: _lastWindow,
    pair_window_ms: 50
  };
  if (_lastParams) {
    payload.pos_min_mA = _lastParams.pos_min_mA;
    payload.neg_min_abs_mA = _lastParams.neg_min_abs_mA;
  } else {
    payload.pos_min_mA = parseFloat(document.getElementById('posMin').value);
    payload.neg_min_abs_mA = parseFloat(document.getElementById('negMin').value);
  }

  dpRunJobEndpoint('/api/echem/export_job', payload, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Saving pair exports${pct}${msg}`, 'loading');
    },
  }).then(d => {
    const n = d.saved_count || 0;
    setStatus('status', 'Saved summary + ' + n + ' pair file(s): ' + (d.saved_path || 'ok'), 'ok');
    recordRunHistory({
      view: 'echem_pc',
      title: 'EChem Photocurrent Pair Export',
      status: 'ok',
      project_root: document.getElementById('folderPath').value.trim(),
      input_files: [{path: _currentFile, role: 'source_echem'}],
      outputs: dpAsPathRecords(d.saved_paths || (d.summary_path ? [d.summary_path] : []), 'photocurrent_pair_output'),
      parameters: payload,
      metadata: {
        saved_folder: d.saved_path || '',
        saved_count: n,
      },
    });
  }).catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

window.addEventListener('load', () => {
  setStatus('status', 'Ready', 'ok');
  if (new URLSearchParams(window.location.search).get('demo') === 'echem') {
    document.getElementById('folderPath').value = DEFAULT_EXAMPLES_DIR || 'examples';
    scanFolder();
  }
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'activePairs',
  'baseName',
  'detect',
  'exportCSV',
  'exportSegments',
  'num',
  'restoreAllPairs',
  'scanFolder',
  'selectFile',
  'togglePairRemoved',
  'toggleWindow',
  'updateTable',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
