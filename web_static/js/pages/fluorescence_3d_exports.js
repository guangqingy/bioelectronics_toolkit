function currentRotationGifPayload(forPreview) {
  const framesRaw = parseInt(document.getElementById('gifFrames')?.value || '48', 10);
  const fpsRaw = parseFloat(document.getElementById('gifFps')?.value || '12');
  const payload = currentVolumePayload(!forPreview);
  payload.rotation_axis = document.getElementById('rotationAxis')?.value || 'z';
  payload.rotation_direction = document.getElementById('rotationDirection')?.value || 'forward';
  payload.gif_frames = Number.isFinite(framesRaw) ? Math.max(8, Math.min(120, framesRaw)) : 48;
  payload.gif_fps = Number.isFinite(fpsRaw) ? Math.max(1, Math.min(30, fpsRaw)) : 12;
  payload.gif_size = forPreview ? 420 : 640;
  payload.gif_points = forPreview ? 18000 : 45000;
  if (forPreview) {
    payload.gif_frames = Math.min(payload.gif_frames, 24);
    payload.max_points = Math.min(payload.max_points || 70000, 26000);
    payload.max_xy = Math.min(payload.max_xy || 180, 150);
    payload.max_z = Math.min(payload.max_z || 80, 64);
  }
  return payload;
}

async function previewRotationGif() {
  if (!_currentInfo || !_currentInfo.can_3d) {
    setStatus('status', 'Load a TIFF Z-stack first', 'error');
    return;
  }
  if (!hasEnabledChannel()) {
    setStatus('status', 'Select at least one channel to render', 'error');
    return;
  }
  const preview = document.getElementById('rotationPreview');
  preview.innerHTML = '<div class="plot-placeholder">Building rotation preview...</div>';
  btnBusy('btnPreviewGif', true, 'Building...');
  setStatus('status', 'Rendering rotation GIF preview...', 'loading');
  try {
    const d = await api('/api/fluorescence/3d/rotation_gif_preview', currentRotationGifPayload(true));
    if (d.error) throw new Error(d.error);
    preview.innerHTML = `<img src="data:image/gif;base64,${d.gif_b64}" alt="3D rotation preview">`;
    setStatus('status', `Rotation preview ready: ${d.frames || 0} frames`, 'ok');
  } catch (e) {
    preview.innerHTML = `<div class="plot-placeholder">Rotation preview error: ${dpEscapeHtml(e.message)}</div>`;
    setStatus('status', 'Rotation preview error: ' + (e.message || String(e)), 'error');
    showLog('Rotation Preview Error', e.message || String(e));
  } finally {
    btnBusy('btnPreviewGif', false, 'Preview GIF');
  }
}

async function exportRotationGif() {
  if (!_currentInfo || !_currentInfo.can_3d) {
    setStatus('status', 'Load a TIFF Z-stack first', 'error');
    return;
  }
  if (!hasEnabledChannel()) {
    setStatus('status', 'Select at least one channel to export', 'error');
    return;
  }
  btnBusy('btnExportGif', true, 'Exporting...');
  setStatus('status', 'Exporting 3D rotation GIF...', 'loading');
  try {
    const payload = {
      ...currentRotationGifPayload(false),
      output_name: document.getElementById('outputName').value.trim(),
      overwrite: true,
    };
    const d = await dpRunJobEndpoint('/api/fluorescence/3d/export_rotation_gif_job', payload, {
      interval_ms: 1000,
      on_update: job => {
        const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
        const msg = job.message ? ` · ${job.message}` : '';
        setStatus('status', `Exporting 3D rotation GIF${pct}${msg}`, 'loading');
      },
    });
    if (d.error) throw new Error(d.error);
    setStatus('status', `Rotation GIF saved: ${d.output_path}`, 'ok');
    showLog('3D Rotation GIF Export', `Saved ${d.frames || 0} frame(s)\n${d.output_path}`);
    recordRunHistory({
      view: 'fluorescence_3d_stacking',
      title: '3D rotation GIF export',
      project_root: document.getElementById('folderPath').value.trim() || dpPathDir(document.getElementById('tiffPath').value.trim()),
      parameters: Object.assign(payload, {operation: 'export_rotation_gif'}),
      input_files: [{path: document.getElementById('tiffPath').value.trim(), type: 'tiff', role: 'source_stack'}],
      outputs: [{path: d.output_path, type: 'gif', role: '3d_rotation_gif'}],
      metadata: {frames: d.frames || 0, fps: d.fps || 0, axis: d.axis || 'z'},
    });
    toast('3D rotation GIF export complete');
  } catch (e) {
    setStatus('status', 'Rotation GIF export error: ' + (e.message || String(e)), 'error');
    showLog('Rotation GIF Export Error', e.message || String(e));
  } finally {
    btnBusy('btnExportGif', false, 'Export GIF');
  }
}

async function analyzeIntensityDistribution() {
  if (!_currentInfo || !_currentInfo.can_3d) {
    setStatus('status', 'Load a TIFF Z-stack first', 'error');
    return;
  }
  btnBusy('btnAnalyzeDistribution', true, 'Analyzing...');
  setStatus('status', 'Analyzing channel intensity distribution...', 'loading');
  try {
    const payload = {
      ...currentPreviewPayload(),
      distribution_channel: parseInt(document.getElementById('distributionChannel')?.value || '0', 10),
      distribution_axis: document.getElementById('distributionAxis')?.value || 'z',
      distribution_metric: document.getElementById('distributionMetric')?.value || 'mean',
      denoise: document.getElementById('volumeDenoise')?.value || 'Off',
      output_name: document.getElementById('outputName').value.trim(),
      overwrite: true,
    };
    const d = await api('/api/fluorescence/3d/intensity_distribution', payload);
    if (d.error) throw new Error(d.error);
    const rows = (d.rows || []).slice(0, 8).map(row => `
      <tr>
        <td>${dpEscapeHtml(String(row.index))}</td>
        <td>${formatNumber(row.coordinate_um, 3)}</td>
        <td>${formatNumber(row.intensity, 3)}</td>
      </tr>`).join('');
    upsertResultCard('stackDistributionCard', `C${(d.channel || 0) + 1} ${String(d.axis || '').toUpperCase()} Distribution`, `
      <div class="stack-distribution-plot"><img src="data:image/png;base64,${d.plot}" alt="Intensity distribution"></div>
      <div class="lif-export-note">CSV: ${dpEscapeHtml(d.csv_path || '')}</div>
      <table class="stack-distribution-table">
        <thead><tr><th>Index</th><th>µm</th><th>Intensity</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`);
    setStatus('status', `Distribution saved: ${d.csv_path}`, 'ok');
    recordRunHistory({
      view: 'fluorescence_3d_stacking',
      title: '3D intensity distribution',
      project_root: document.getElementById('folderPath').value.trim() || dpPathDir(document.getElementById('tiffPath').value.trim()),
      parameters: Object.assign(payload, {operation: 'intensity_distribution'}),
      input_files: [{path: document.getElementById('tiffPath').value.trim(), type: 'tiff', role: 'source_stack'}],
      outputs: [{path: d.csv_path, type: 'csv', role: 'intensity_distribution'}],
      metadata: {axis: d.axis || 'z', metric: d.metric || 'mean', channel: d.channel || 0},
    });
  } catch (e) {
    setStatus('status', 'Distribution error: ' + (e.message || String(e)), 'error');
    showLog('Distribution Error', e.message || String(e));
  } finally {
    btnBusy('btnAnalyzeDistribution', false, 'Analyze Distribution');
  }
}

function showLog(title, text) {
  const body = `<pre style="white-space:pre-wrap;font-size:11.5px;color:var(--graphite)">${dpEscapeHtml(text)}</pre>`;
  upsertResultCard('stackLogCard', title, body);
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
    <div class="result-card-header">${dpEscapeHtml(title)}</div>
    <div class="result-card-body">${bodyHtml}</div>`;
}

window.addEventListener('load', () => {
  setStatus('status', 'Ready', 'ok');
  renderAvailableTiffList();
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'analyzeIntensityDistribution',
  'currentRotationGifPayload',
  'exportRotationGif',
  'previewRotationGif',
  'showLog',
  'upsertResultCard',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
