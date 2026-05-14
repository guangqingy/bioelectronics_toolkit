function exportLifManifest() {
  if (!_lifPath || !_lifDisplayRecords.length) {
    setStatus('status', 'No LIF order to export', 'error');
    return;
  }
  syncRenameFromInput();
  btnBusy('btnManifest', true, 'Exporting...');
  const order_indices = _lifDisplayRecords.map(r => r.index);
  api('/api/fluorescence/lif/export_manifest', {
    path: _lifPath,
    order_indices,
    rename_map: _lifRenameMap,
  }).then(d => {
    btnBusy('btnManifest', false, 'Export CSV');
    if (d.error) throw new Error(d.error);
    setStatus('status', `Saved order CSV: ${d.output_path}`, 'ok');
    showLog('LIF Order CSV', `${d.rows || 0} rows saved\n${d.output_path}`);
    recordRunHistory({
      view: 'fluorescence_lif',
      title: 'LIF order CSV',
      project_root: document.getElementById('folderPath').value.trim() || dpPathDir(_lifPath),
      parameters: lifRunParameters({operation: 'export_manifest_csv'}),
      input_files: [{path: _lifPath, type: 'lif'}],
      outputs: [{path: d.output_path, type: 'csv', role: 'lif_order'}],
      metadata: {rows: d.rows || 0},
    });
    toast('LIF order CSV saved');
  }).catch(e => {
    btnBusy('btnManifest', false, 'Export CSV');
    setStatus('status', 'Export error: ' + e.message, 'error');
    showLog('LIF Export Error', e.message || String(e));
  });
}

async function runLifBackgroundJob(endpoint, payload, label) {
  return dpRunJobEndpoint(endpoint, payload, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `${label}${pct}${msg}`, 'loading');
    },
  });
}

function exportSelectedTiff() {
  const rec = findRecord(_lifActiveIndex);
  if (!rec || !_lifPath) {
    setStatus('status', 'No subfile selected', 'error');
    return;
  }
  if (!HAS_TIFF) {
    setStatus('status', 'tifffile is not installed', 'error');
    return;
  }
  const outputName = syncRenameFromInput();
  renderLifBrowser();
  btnBusy('btnExportTiff', true, 'Exporting...');
  setStatus('status', 'Exporting selected subfile to TIFF...', 'loading');

  runLifBackgroundJob('/api/fluorescence/lif/export_tiff_job', {
    path: _lifPath,
    image_index: rec.index,
    output_name: outputName,
    overwrite: true,
  }, 'Exporting selected subfile to TIFF').then(d => {
    btnBusy('btnExportTiff', false, 'Export Selected TIFF');
    if (d.error) throw new Error(d.error);
    setStatus('status', `TIFF saved: ${d.output_path}`, 'ok');
    showLog('TIFF Export', `Saved ${d.planes || 1} plane(s)\n${d.output_path}\n\nMetadata JSON:\n${d.metadata_path || ''}`);
    recordRunHistory({
      view: 'fluorescence_lif',
      title: 'LIF selected TIFF export',
      project_root: document.getElementById('folderPath').value.trim() || dpPathDir(_lifPath),
      parameters: lifRunParameters({operation: 'export_selected_tiff', image_index: rec.index, output_name: outputName}),
      input_files: [{path: _lifPath, type: 'lif', image_index: rec.index, name: rec.name || ''}],
      outputs: [
        {path: d.output_path, type: 'tiff', role: 'exported_tiff'},
        {path: d.metadata_path, type: 'json', role: 'metadata'},
      ].filter(x => x.path),
      metadata: {planes: d.planes || 1, shape: d.shape || [], calibration: d.calibration || {}},
    });
    toast('TIFF export complete');
  }).catch(e => {
    btnBusy('btnExportTiff', false, 'Export Selected TIFF');
    setStatus('status', 'TIFF export error: ' + e.message, 'error');
    showLog('TIFF Export Error', e.message || String(e));
  });
}

function exportAllTiff() {
  if (!_lifPath || !_lifDisplayRecords.length) {
    setStatus('status', 'No LIF project loaded', 'error');
    return;
  }
  if (!HAS_TIFF) {
    setStatus('status', 'tifffile is not installed', 'error');
    return;
  }
  syncRenameFromInput();
  btnBusy('btnExportAllTiff', true, 'Exporting...');
  setStatus('status', `Exporting ${_lifDisplayRecords.length} subfiles to TIFF...`, 'loading');
  const order_indices = _lifDisplayRecords.map(r => r.index);

  runLifBackgroundJob('/api/fluorescence/lif/export_tiff_batch_job', {
    path: _lifPath,
    order_indices,
    rename_map: _lifRenameMap,
    overwrite: true,
  }, `Exporting ${_lifDisplayRecords.length} subfiles to TIFF`).then(d => {
    btnBusy('btnExportAllTiff', false, 'Export All TIFF');
    if (d.error) throw new Error(d.error);
    const failed = d.failed || 0;
    setStatus('status', `TIFF batch export: ${d.success || 0} saved, ${failed} failed`, failed ? 'error' : 'ok');
    const lines = [
      `Output folder: ${d.output_dir}`,
      `Success: ${d.success || 0}`,
      `Failed: ${failed}`,
      '',
      'Each TIFF has calibration tags plus a *_metadata.json sidecar with Leica dimensions, scale, and settings.',
    ];
    if (failed && d.failed_files) {
      lines.push('', ...d.failed_files.slice(0, 8).map(x => `${x.name || x.image_index}: ${x.error}`));
    }
    showLog('TIFF Batch Export', lines.join('\n'));
    const outputs = [];
    (d.outputs || []).forEach(item => {
      if (item.output_path) outputs.push({path: item.output_path, type: 'tiff', role: 'exported_tiff', image_index: item.image_index});
      if (item.metadata_path) outputs.push({path: item.metadata_path, type: 'json', role: 'metadata', image_index: item.image_index});
    });
    recordRunHistory({
      view: 'fluorescence_lif',
      title: 'LIF batch TIFF export',
      status: failed ? 'warning' : 'ok',
      project_root: document.getElementById('folderPath').value.trim() || dpPathDir(_lifPath),
      parameters: lifRunParameters({operation: 'export_all_tiff', output_dir: d.output_dir}),
      input_files: [{path: _lifPath, type: 'lif'}],
      outputs,
      warnings: failed && d.failed_files ? d.failed_files.map(x => `${x.name || x.image_index}: ${x.error}`) : [],
      metadata: {output_dir: d.output_dir, success: d.success || 0, failed},
    });
    toast('TIFF batch export finished');
  }).catch(e => {
    btnBusy('btnExportAllTiff', false, 'Export All TIFF');
    setStatus('status', 'Batch export error: ' + e.message, 'error');
    showLog('TIFF Batch Export Error', e.message || String(e));
  });
}

function showLog(title, text) {
  const body = `<pre style="white-space:pre-wrap;font-size:11.5px;color:var(--graphite)">${escapeHtml(text)}</pre>`;
  upsertResultCard('lifLogCard', title, body);
}

function upsertResultCard(cardId, title, bodyHtml) {
  const area = document.getElementById('resultArea');
  area.style.display = 'flex';
  let card = document.getElementById(cardId);
  if (!card) {
    card = document.createElement('div');
    card.id = cardId;
    card.className = 'result-card';
    area.prepend(card);
  }
  card.innerHTML = `
    <div class="result-card-header">${escapeHtml(title)}</div>
    <div class="result-card-body">${bodyHtml}</div>`;
}

window.addEventListener('load', () => {
  if (!HAS_READLIF) {
    setStatus('status', 'readlif is not installed: python -m pip install readlif', 'error');
  } else if (!HAS_PIL) {
    setStatus('status', 'Pillow is not installed', 'error');
  } else if (!HAS_TIFF) {
    setStatus('status', 'tifffile is not installed: TIFF export is disabled', 'error');
  } else {
    setStatus('status', 'Ready', 'ok');
  }
});
