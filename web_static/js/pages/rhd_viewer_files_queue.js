function baseName(path) {
  return (path || '').split(/[\\/]/).pop() || path;
}

function previewMergeEnabled() {
  return document.getElementById('previewMergePair')?.checked || false;
}

function currentDownsampleValue() {
  return document.getElementById('previewDownsample')?.value || 'auto';
}

function previewInvertYEnabled() {
  return document.getElementById('invertY')?.checked || false;
}

function updateRhdParameterGroups() {
  dpApplyParamGroups('processType', 'data-process-mode');
  dpApplyParamGroups('filterType', 'data-filter-mode');
}

function viewNumber(id) {
  const value = document.getElementById(id)?.value;
  return value ? parseFloat(value) : null;
}

function currentViewParams() {
  return {
    x_min: viewNumber('xMin'),
    x_max: viewNumber('xMax'),
    y_min: viewNumber('yMin'),
    y_max: viewNumber('yMax'),
    invert_y: previewInvertYEnabled(),
    downsample: currentDownsampleValue(),
    preview_merge_pair: previewMergeEnabled(),
    filter_type: document.getElementById('filterType')?.value || 'none',
    filter_low_hz: viewNumber('filterLowHz'),
    filter_high_hz: viewNumber('filterHighHz'),
    filter_notch_hz: viewNumber('filterNotchHz'),
    filter_order: viewNumber('filterOrder'),
    filter_notch_q: viewNumber('filterNotchQ'),
  };
}

function currentFigureParams() {
  return {
    fig_width_in: viewNumber('figWidthIn'),
    fig_height_in: viewNumber('figHeightIn'),
    fig_dpi: viewNumber('figDpi'),
    trace_line_width: viewNumber('traceLineWidth'),
    trace_color: document.getElementById('traceColor')?.value || '#3E6AE1',
    show_grid: document.getElementById('showGrid')?.checked || false,
    show_title: document.getElementById('showTitle')?.checked || false,
  };
}

function currentProcessingParams() {
  return {
    process_type: document.getElementById('processType')?.value || 'envelope',
    smooth_ms: viewNumber('smoothMs'),
    envelope_smooth_ms: viewNumber('envelopeSmoothMs'),
    smooth_method: document.getElementById('smoothMethod')?.value || 'moving',
    sg_poly: viewNumber('sgPoly'),
    fit_degree: viewNumber('fitDegree'),
    fit_show_raw: document.getElementById('fitShowRaw')?.checked || false,
    fft_window: document.getElementById('fftWindow')?.value || 'hann',
    fft_max_hz: viewNumber('fftMaxHz'),
    fft_log: document.getElementById('fftLog')?.checked || false,
    stft_ms: viewNumber('stftMs'),
    stft_overlap_pct: viewNumber('stftOverlapPct'),
    stft_max_hz: viewNumber('stftMaxHz'),
    stft_cmap: document.getElementById('stftCmap')?.value || 'viridis',
    stft_log: document.getElementById('stftLog')?.checked || false,
  };
}

function currentProcessingPayload(extra) {
  return Object.assign({
    path: _currentFile,
    channel: _currentChannel,
    merge_pair: previewMergeEnabled(),
  }, currentViewParams(), currentProcessingParams(), currentFigureParams(), extra || {});
}

function scanFolder() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) {
    setStatus('status', 'Please enter a folder path', 'error');
    return;
  }
  setStatus('status', 'Scanning folder...', 'loading');

  api('/api/rhd/browse', { folder })
    .then(data => {
      if (data.error) throw new Error(data.error);
      const filePaths = (data.files || []).map((f) => typeof f === 'string' ? f : (f.path || ''));
      _rhdFiles = filePaths.slice();
      renderRhdFileList({preserveChannel: false, loadProfile: true});
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function renderRhdFileList(options) {
  const opts = options || {};
  const list = document.getElementById('rhdList');
  list.innerHTML = '';

  if (!_rhdFiles.length) {
    _currentFile = null;
    _currentChannel = null;
    document.getElementById('channelList').innerHTML = '';
    setPlot('plotArea', null);
    setPlot('processArea', null);
    _metadata = {};
    updateInfoCard();
    setStatus('status', 'Ready', 'ok');
    return;
  }

  if (previewMergeEnabled()) {
    const item = document.createElement('div');
    const folder = document.getElementById('folderPath').value.trim();
    item.className = 'file-item';
    item.textContent = (baseName(folder) || 'Folder') + ' recording (' + _rhdFiles.length + ' files)';
    item.title = _rhdFiles.join('\n');
    item.onclick = () => selectFile(item, _rhdFiles[0], {
      preserveChannel: true,
      loadProfile: false,
    });
    list.appendChild(item);
    selectFile(item, _rhdFiles[0], opts);
    return;
  }

  _rhdFiles.forEach(path => {
    const item = document.createElement('div');
    item.className = 'file-item';
    item.textContent = baseName(path);
    item.title = path;
    item.onclick = () => selectFile(item, path);
    list.appendChild(item);
  });

  const selectedPath = (_currentFile && _rhdFiles.includes(_currentFile)) ? _currentFile : _rhdFiles[0];
  const selectedItem = [...list.children].find(item => item.title === selectedPath) || list.children[0];
  selectFile(selectedItem, selectedPath, opts);
}

function queueRender() {
  const list = document.getElementById('queueList');
  list.innerHTML = '';
  _queueFiles.forEach((path, idx) => {
    const item = document.createElement('div');
    item.className = 'file-item' + (idx === _queueSel ? ' active' : '');
    item.textContent = baseName(path);
    item.title = path;
    item.onclick = () => {
      _queueSel = idx;
      queueRender();
    };
    list.appendChild(item);
  });
}

function queueAddPaths(paths) {
  let added = 0;
  (paths || []).forEach(p => {
    if (p && !_queueFiles.includes(p)) {
      _queueFiles.push(p);
      added += 1;
    }
  });
  if (added > 0) {
    if (_queueSel < 0) _queueSel = _queueFiles.length - 1;
    queueRender();
  }
  return added;
}

function currentRhdBatchPaths() {
  if (previewMergeEnabled()) {
    if (Array.isArray(_metadata.source_paths) && _metadata.source_paths.length) {
      return _metadata.source_paths.slice();
    }
    if (_rhdFiles.length) {
      return _rhdFiles.slice();
    }
  }
  return _currentFile ? [_currentFile] : [];
}

function queueAddCurrent() {
  if (!_currentFile) {
    setStatus('status', 'No file selected', 'error');
    return;
  }
  const paths = currentRhdBatchPaths();
  const added = queueAddPaths(paths);
  const label = paths.length > 1 ? `${paths.length} continuous recording file(s)` : 'current file';
  setStatus('status', added ? `Added ${label} to queue` : `${label} already in queue`, added ? 'ok' : 'loading');
}

function queueAddAll() {
  const added = queueAddPaths(_rhdFiles);
  setStatus('status', added ? ('Added ' + added + ' file(s) to queue') : 'No new files to add', added ? 'ok' : 'loading');
}

function queueAddAllRecursive() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) {
    setStatus('status', 'Please enter a folder path', 'error');
    return;
  }
  setStatus('status', 'Scanning recursively...', 'loading');
  api('/api/rhd/browse_recursive', { folder })
    .then(data => {
      if (data.error) throw new Error(data.error);
      const paths = (data.files || []).map((f) => typeof f === 'string' ? f : (f.path || ''));
      const added = queueAddPaths(paths);
      const suffix = data.truncated ? ' · showing first 300 matches' : '';
      setStatus('status', added ? ('Added ' + added + ' file(s) recursively' + suffix) : ('No new recursive files found' + suffix), data.truncated ? 'warning' : 'ok');
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function queueRemoveSelected() {
  if (_queueSel < 0 || _queueSel >= _queueFiles.length) {
    setStatus('status', 'No queue item selected', 'error');
    return;
  }
  _queueFiles.splice(_queueSel, 1);
  if (_queueSel >= _queueFiles.length) _queueSel = _queueFiles.length - 1;
  queueRender();
  setStatus('status', 'Removed selected queue item', 'ok');
}

function queueClear() {
  _queueFiles = [];
  _queueSel = -1;
  queueRender();
  setStatus('status', 'Queue cleared', 'ok');
}

function queueMove(delta) {
  if (_queueSel < 0 || _queueSel >= _queueFiles.length) {
    setStatus('status', 'No queue item selected', 'error');
    return;
  }
  const j = _queueSel + delta;
  if (j < 0 || j >= _queueFiles.length) return;
  const tmp = _queueFiles[_queueSel];
  _queueFiles[_queueSel] = _queueFiles[j];
  _queueFiles[j] = tmp;
  _queueSel = j;
  queueRender();
}

function rhdExportParams(extra) {
  return Object.assign({
    merge_pair: document.getElementById('mergePair').checked,
    preview_merge_pair: previewMergeEnabled(),
    downsample: currentDownsampleValue(),
    wide_csv: document.getElementById('wideCsv').checked,
    x_min: viewNumber('xMin'),
    x_max: viewNumber('xMax'),
    y_min: viewNumber('yMin'),
    y_max: viewNumber('yMax'),
    invert_y: previewInvertYEnabled(),
    ...currentFigureParams(),
  }, extra || {});
}

function exportQueue() {
  if (_queueFiles.length === 0) {
    setStatus('status', 'Queue is empty', 'error');
    return;
  }

  const payload = {
    paths: _queueFiles,
    merge_pair: document.getElementById('mergePair').checked,
    wide_csv: document.getElementById('wideCsv').checked
  };

  btnBusy('btnQueueExport', true, 'Exporting...');
  setStatus('status', 'Exporting queue...', 'loading');
  dpRunJobEndpoint('/api/rhd/export_queue_job', payload, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Exporting queue${pct}${msg}`, 'loading');
    },
  })
    .then(data => {
      if (data.error) throw new Error(data.error);
      const warns = (data.warnings || []).length;
      const first = (data.saved_paths || [])[0] || 'ok';
      setStatus('status', 'Exported ' + (data.saved_count || 0) + '/' + (data.total || _queueFiles.length)
        + (warns ? (' (warnings: ' + warns + ')') : '')
        + '. First output: ' + first, warns ? 'loading' : 'ok');
      recordRunHistory({
        view: 'rhd_viewer',
        title: 'RHD Queue Export',
        status: warns ? 'warning' : 'ok',
        project_root: document.getElementById('folderPath').value.trim(),
        input_files: _queueFiles.map(p => ({path: p, role: 'source_rhd'})),
        outputs: dpAsPathRecords(data.saved_paths || [], document.getElementById('wideCsv').checked ? 'wide_tsv' : 'channel_csv_folder'),
        parameters: rhdExportParams({mode: 'queue'}),
        warnings: data.warnings || [],
        metadata: {
          total: data.total || _queueFiles.length,
          saved_count: data.saved_count || 0,
        },
      });
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'))
    .finally(() => btnBusy('btnQueueExport', false, 'Export Queue'));
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'baseName',
  'currentDownsampleValue',
  'currentFigureParams',
  'currentProcessingParams',
  'currentProcessingPayload',
  'currentRhdBatchPaths',
  'currentViewParams',
  'exportQueue',
  'previewInvertYEnabled',
  'previewMergeEnabled',
  'queueAddAll',
  'queueAddAllRecursive',
  'queueAddCurrent',
  'queueAddPaths',
  'queueClear',
  'queueMove',
  'queueRemoveSelected',
  'queueRender',
  'renderRhdFileList',
  'rhdExportParams',
  'scanFolder',
  'updateRhdParameterGroups',
  'viewNumber',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
