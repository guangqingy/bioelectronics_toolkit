let _currentFile = null;
let _mergeQueue = [];
let _columns = [];

window.dpCurrentFilePath = () => _currentFile || '';
window.dpCurrentProjectRoot = () => document.getElementById('folderPath').value.trim();
window.dpCollectFileProfilePayload = () => ({ mergeQueue: _mergeQueue.slice() });
window.dpApplyFileProfilePayload = payload => {
  if (payload && Array.isArray(payload.mergeQueue)) {
    _mergeQueue = payload.mergeQueue.slice();
    updateMergeListUI();
    updateSummaryCard();
  }
};
window.dpAfterFileProfileApplied = () => {
  updateMergeListUI();
  updateSummaryCard();
  plot();
};
window.dpApplyRunManifest = async manifest => {
  dpApplyRunManifestFallback(manifest);
  const inputPath = dpFirstManifestInput(manifest);
  if (inputPath) {
    _currentFile = inputPath;
    document.getElementById('folderPath').value = manifest.project_root || dpPathDir(inputPath);
  }
  const params = manifest.parameters || {};
  if (Array.isArray(params.paths)) _mergeQueue = params.paths.slice();
  else if ((manifest.input_files || []).length > 1) _mergeQueue = manifest.input_files.map(rec => rec.path).filter(Boolean);
  updateMergeListUI();
  updateSummaryCard();
  if (_currentFile) {
    try {
      const d = await api('/api/csv/columns', {path: _currentFile});
      if (!d.error) {
        _columns = d.columns || [];
        ['xCol', 'yCol'].forEach(id => {
          const el = document.getElementById(id);
          const current = el.value;
          el.innerHTML = '<option value="">Select</option>' + _columns.map(c => `<option value="${dpEscapeHtml(c)}">${dpEscapeHtml(c)}</option>`).join('');
          if (current) el.value = current;
        });
      }
    } catch (_) {}
    updateSummaryCard();
    plot();
  }
};

function baseName(path) {
  return (path || '').split('/').pop() || path;
}

function valOrNull(id) {
  const raw = document.getElementById(id).value;
  return raw === '' ? null : parseFloat(raw);
}

function csvPlotParams(extra) {
  return Object.assign({
    x_col: document.getElementById('xCol').value,
    y_col: document.getElementById('yCol').value,
    x_min: valOrNull('xMin'),
    x_max: valOrNull('xMax'),
    y_min: valOrNull('yMin'),
    y_max: valOrNull('yMax'),
    dsf: parseInt(document.getElementById('dsf').value, 10) || 1,
  }, extra || {});
}

function recordCsvOutput(title, inputPaths, outputPaths, parameters, metadata) {
  recordRunHistory({
    view: 'csv_viewer',
    title,
    status: 'ok',
    project_root: document.getElementById('folderPath').value.trim(),
    input_files: (inputPaths || []).map(p => ({path: p, role: 'source_csv'})),
    outputs: dpAsPathRecords(outputPaths || [], metadata?.output_type || 'csv_output'),
    parameters: parameters || {},
    metadata: metadata || {},
  });
}

function updateSummaryCard() {
  const rows = [
    { 'Key': 'File', 'Value': _currentFile ? baseName(_currentFile) : '-' },
    { 'Key': 'Columns', 'Value': _columns.length ? _columns.join(', ') : '-' },
    { 'Key': 'Queue Size', 'Value': String(_mergeQueue.length) }
  ];
  document.getElementById('dataTable').innerHTML = buildTable(rows, ['Key', 'Value']);
}

function scanFolder() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) {
    setStatus('status', 'Please enter a folder path', 'error');
    toast('Please enter a folder path', true);
    return;
  }

  setStatus('status', 'Scanning folder...', 'loading');
  api('/api/csv/browse', { folder })
    .then(data => {
      if (data.error) throw new Error(data.error);
      const list = document.getElementById('fileList');
      const filePaths = (data.files || []).map(f => typeof f === 'string' ? f : (f.path || ''));
      list.innerHTML = '';

      filePaths.forEach(path => {
        const item = document.createElement('div');
        item.className = 'file-item';
        item.textContent = baseName(path);
        item.onclick = () => selectFile(item, path);
        list.appendChild(item);
      });

      if (filePaths.length > 0) {
        selectFile(list.children[0], filePaths[0]);
      } else {
        _currentFile = null;
        _columns = [];
        setPlot('plotArea', null);
        updateSummaryCard();
        setStatus('status', 'No files found', 'error');
      }
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Scan failed: ' + e.message, true);
    });
}

function selectFile(el, path) {
  document.querySelectorAll('#fileList .file-item').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  _currentFile = path;
  setStatus('status', 'Loading columns...', 'loading');

  api('/api/csv/columns', { path })
    .then(data => {
      if (data.error) throw new Error(data.error);
      _columns = data.columns || [];

      const xSel = document.getElementById('xCol');
      const ySel = document.getElementById('yCol');
      xSel.innerHTML = '<option value="">Select</option>';
      ySel.innerHTML = '<option value="">Select</option>';

      _columns.forEach(col => {
        const o1 = document.createElement('option');
        o1.value = col;
        o1.textContent = col;
        xSel.appendChild(o1);

        const o2 = document.createElement('option');
        o2.value = col;
        o2.textContent = col;
        ySel.appendChild(o2);
      });

      if (_columns.length >= 2) {
        xSel.value = _columns[0];
        ySel.value = _columns[1];
      }

      loadGenericFileProfileForCurrent(true).finally(() => {
        updateSummaryCard();
        setStatus('status', 'Ready', 'ok');
        plot();
      });
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Load columns failed: ' + e.message, true);
    });
}

function plot() {
  if (!_currentFile) return;

  const x_col = document.getElementById('xCol').value;
  const y_col = document.getElementById('yCol').value;
  if (!x_col || !y_col) return;

  const payload = {
    path: _currentFile,
    x_col,
    y_col,
    x_min: valOrNull('xMin'),
    x_max: valOrNull('xMax'),
    y_min: valOrNull('yMin'),
    y_max: valOrNull('yMax'),
    dsf: parseInt(document.getElementById('dsf').value, 10) || 1
  };

  setStatus('status', 'Plotting...', 'loading');
  api('/api/csv/plot', payload)
    .then(data => {
      if (data.error) throw new Error(data.error);
      setPlot('plotArea', data.img);
      setStatus('status', 'Ready', 'ok');
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Plot failed: ' + e.message, true);
    });
}

function addToMergeQueue() {
  if (_currentFile && !_mergeQueue.includes(_currentFile)) {
    _mergeQueue.push(_currentFile);
    updateMergeListUI();
    updateSummaryCard();
  }
}

function clearMergeQueue() {
  _mergeQueue = [];
  updateMergeListUI();
  updateSummaryCard();
}

function removeMergeItem(idx) {
  _mergeQueue.splice(idx, 1);
  updateMergeListUI();
  updateSummaryCard();
}

function updateMergeListUI() {
  const list = document.getElementById('mergeList');
  if (_mergeQueue.length === 0) {
    list.innerHTML = '<div class="file-list-empty">Queue is empty</div>';
    return;
  }

  list.innerHTML = '';
  _mergeQueue.forEach((file, idx) => {
    const item = document.createElement('div');
    item.className = 'file-item';
    item.innerHTML = '<span>' + baseName(file) + '</span>' +
      '<button class="btn-icon" style="margin-left:auto;" onclick="removeMergeItem(' + idx + '); event.stopPropagation();">x</button>';
    list.appendChild(item);
  });
}

function mergePlot() {
  if (_mergeQueue.length < 2) {
    setStatus('status', 'Add at least 2 files to merge', 'error');
    toast('Add at least 2 files to merge', true);
    return;
  }

  const x_col = document.getElementById('xCol').value;
  const y_col = document.getElementById('yCol').value;
  if (!x_col || !y_col) {
    setStatus('status', 'Select X and Y columns', 'error');
    toast('Select X and Y columns', true);
    return;
  }

  btnBusy('btnMerge', true, 'Merging...');
  setStatus('status', 'Merging traces...', 'loading');
  dpRunJobEndpoint('/api/csv/merge_job', {
    paths: _mergeQueue,
    x_col,
    y_col,
    x_min: valOrNull('xMin'),
    x_max: valOrNull('xMax')
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Merging traces${pct}${msg}`, 'loading');
    },
  })
    .then(data => {
      if (data.error) throw new Error(data.error);
      setPlot('plotArea', data.img);
      setStatus('status', 'Merged plot ready', 'ok');
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Merge failed: ' + e.message, true);
    })
    .finally(() => btnBusy('btnMerge', false, 'Merge and Plot'));
}

function exportMergePreview() {
  if (_mergeQueue.length < 2) {
    setStatus('status', 'Add at least 2 files to merge', 'error');
    toast('Add at least 2 files to merge', true);
    return;
  }

  const x_col = document.getElementById('xCol').value;
  const y_col = document.getElementById('yCol').value;
  if (!x_col || !y_col) {
    setStatus('status', 'Select X and Y columns', 'error');
    toast('Select X and Y columns', true);
    return;
  }

  btnBusy('btnMergeExport', true, 'Exporting...');
  setStatus('status', 'Saving merged preview...', 'loading');

  dpRunJobEndpoint('/api/csv/export_merge_job', {
    paths: _mergeQueue,
    x_col,
    y_col,
    x_min: valOrNull('xMin'),
    x_max: valOrNull('xMax'),
    drop_first_subsequent: document.getElementById('mergeDropFirst').checked,
    mode: 'save'
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Saving merged preview${pct}${msg}`, 'loading');
    },
  }).then(d => {
    if (d.error) throw new Error(d.error);
    setStatus('status', 'Saved: ' + (d.saved_path || 'ok') + ' | rows=' + (d.rows || 0), 'ok');
    recordCsvOutput(
      'CSV Merge Export',
      _mergeQueue,
      d.saved_path ? [d.saved_path] : [],
      csvPlotParams({drop_first_subsequent: document.getElementById('mergeDropFirst').checked}),
      {rows: d.rows || 0, output_type: 'merged_csv'}
    );
  }).catch(e => {
    setStatus('status', 'Error: ' + e.message, 'error');
    toast('Merge export failed: ' + e.message, true);
  }).finally(() => btnBusy('btnMergeExport', false, 'Export Merge'));
}

function exportFig(fmt) {
  if (!_currentFile) {
    setStatus('status', 'No file selected', 'error');
    return;
  }

  const x_col = document.getElementById('xCol').value;
  const y_col = document.getElementById('yCol').value;
  if (!x_col || !y_col) {
    setStatus('status', 'Select X and Y columns', 'error');
    return;
  }

  const payload = {
    path: _currentFile,
    fmt,
    x_col,
    y_col
  };

  const xMin = document.getElementById('xMin').value;
  const xMax = document.getElementById('xMax').value;
  const yMin = document.getElementById('yMin').value;
  const yMax = document.getElementById('yMax').value;
  if (xMin) payload.x_min = xMin;
  if (xMax) payload.x_max = xMax;
  if (yMin) payload.y_min = yMin;
  if (yMax) payload.y_max = yMax;
  payload.mode = 'save';

  setStatus('status', 'Saving export...', 'loading');
  dpRunJobEndpoint('/api/csv/export_job', payload, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Saving export${pct}${msg}`, 'loading');
    },
  })
    .then(d => {
      setStatus('status', 'Saved: ' + (d.saved_path || 'ok'), 'ok');
      recordCsvOutput(
        `CSV Plot ${fmt.toUpperCase()} Export`,
        [_currentFile],
        d.saved_path ? [d.saved_path] : [],
        csvPlotParams({fmt}),
        {output_type: fmt === 'csv' ? 'plot_csv' : `plot_${fmt}`}
      );
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Export failed: ' + e.message, true);
    });
}

function exportCSV() {
  if (!_currentFile) {
    setStatus('status', 'No file selected', 'error');
    return;
  }
  setStatus('status', 'Saving CSV...', 'loading');
  dpRunJobEndpoint('/api/csv/export_csv_job', {path: _currentFile, mode: 'save'}, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Saving CSV${pct}${msg}`, 'loading');
    },
  })
    .then(d => {
      setStatus('status', 'Saved: ' + (d.saved_path || 'ok'), 'ok');
      recordCsvOutput(
        'CSV Full Export',
        [_currentFile],
        d.saved_path ? [d.saved_path] : [],
        {full_export: true},
        {output_type: 'full_csv'}
      );
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Export failed: ' + e.message, true);
    });
}

window.addEventListener('load', () => {
  updateMergeListUI();
  updateSummaryCard();
  setStatus('status', 'Ready', 'ok');
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'addToMergeQueue',
  'baseName',
  'clearMergeQueue',
  'csvPlotParams',
  'exportCSV',
  'exportFig',
  'exportMergePreview',
  'mergePlot',
  'plot',
  'recordCsvOutput',
  'removeMergeItem',
  'scanFolder',
  'selectFile',
  'updateMergeListUI',
  'updateSummaryCard',
  'valOrNull',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
