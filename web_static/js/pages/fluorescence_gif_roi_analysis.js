async function scanFrameCounts(showStatus = true) {
  const paths = _tiffEntries.map(e => e.path.trim()).filter(Boolean);
  if (!paths.length) { toast('Add at least one TIFF first', true); return; }
  if (showStatus) setStatus('status', 'Scanning frames…', 'loading');
  try {
    const d = await api('/api/fluorescence/tiff_info_batch', { paths });
    if (d.error) { if (showStatus) setStatus('status', 'Scan error: ' + d.error, 'error'); return; }
    const infoMap = d.info || {};
    _tiffEntries.forEach(e => {
      const info = infoMap[e.path.trim()];
      e.frames = info ? info.n_frames : null;
      e.scale = info || null;
    });
    renderTiffList();
    const total = _tiffEntries.reduce((s, e) => s + (e.frames || 0), 0);
    if (showStatus) setStatus('status', `Scanned — total ${total} frames across ${Object.keys(infoMap).length} file(s)`, 'ok');
    scheduleGifPreview();
  } catch(ex) { if (showStatus) setStatus('status', 'Scan failed: ' + ex.message, 'error'); }
}

/* ---------- Generate GIF ---------- */
async function runGifBackgroundJob(endpoint, payload, label) {
  return dpRunJobEndpoint(endpoint, payload, {
    interval_ms: 900,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `${label}${pct}${msg}`, 'loading');
    },
  });
}

async function generateGif() {
  const paths = _tiffEntries.map(e => e.path.trim()).filter(Boolean);
  if (!paths.length) { toast('Add at least one TIFF first', true); return; }

  btnBusy('btnGenerate', true, 'Generating…');
  setStatus('status', 'Generating GIF…', 'loading');
  document.getElementById('resultArea').innerHTML = '';

  const payload = {
    tiff_paths:    paths,
    slice_specs:   _tiffEntries.filter(e => e.path.trim()).map(e => (e.slices || '').trim()),
    fps:           parseFloat(document.getElementById('gifFps').value) || 5,
    lut:           document.getElementById('gifLut').value,
    scale_bar_um:  parseFloat(document.getElementById('gifBarUm').value) || 0,
    px_per_um:     parseFloat(document.getElementById('gifPxPerUm').value) || 3.45,
    auto_scale:    document.getElementById('gifAutoScale').checked,
    add_timestamp: gifLabelMode() !== 'none',
    label_mode:    gifLabelMode(),
    show_roi_overlay: gifShowRoiOverlay(),
    roi_polygons:  getClosedRoiPolygons(),
    ...gifCropPayload(),
    output_path:   document.getElementById('gifOutput').value.trim(),
  };

  try {
    const d = await runGifBackgroundJob('/api/fluorescence/merge_gif_job', payload, 'Generating GIF');
    btnBusy('btnGenerate', false, 'Generate GIF');

    if (d.error) {
      setStatus('status', 'Error: ' + d.error, 'error');
      document.getElementById('resultArea').innerHTML =
        `<pre class="log-box">${escHtml(d.error)}</pre>`;
      return;
    }

    const sliceNote = d.selected_slices ? ` (${d.selected_slices} selected)` : '';
    setStatus('status', `Done — ${d.n_frames} frames${sliceNote} → ${d.output_path}`, 'ok');

    setGeneratedGifPreview(
      d.gif_preview || '',
      `Generated GIF · ${formatScaleInfo(d)} · ${d.roi_polygons || 0} polygon ROI · marked ${d.show_roi_overlay ? 'yes' : 'no'}`,
      d.output_path || ''
    );

    /* Result card */
    const outDir = d.output_path ? d.output_path.replace(/\/[^\/]+$/, '') : '';
    document.getElementById('resultArea').innerHTML = `
      <div class="result-card" style="padding:12px 16px;background:#f8fffe;border:1px solid #cce;border-radius:6px">
        <div style="font-weight:600;margin-bottom:6px">GIF Generated</div>
        <div style="font-size:12px;color:#555;margin-bottom:4px">Frames: <b>${d.n_frames}</b></div>
        ${d.selected_slices ? `<div style="font-size:12px;color:#555;margin-bottom:4px">Selected slices: <b>${d.selected_slices}</b></div>` : ''}
        <div style="font-size:12px;color:#555;margin-bottom:4px">Polygon ROI: <b>${d.roi_polygons || 0}</b> · marked: <b>${d.show_roi_overlay ? 'yes' : 'no'}</b></div>
        ${d.crop && d.crop.mode && d.crop.mode !== 'full' ? `<div style="font-size:12px;color:#555;margin-bottom:4px">Crop: <b>${escHtml(d.crop.width)}×${escHtml(d.crop.height)} px</b> from (${escHtml(d.crop.x)}, ${escHtml(d.crop.y)})</div>` : ''}
        <div style="font-size:12px;color:#555;margin-bottom:4px">Scale: <b>${escHtml(formatScaleInfo(d))}</b></div>
        <div style="font-size:12px;color:#555;margin-bottom:8px;word-break:break-all">Path: <code>${escHtml(d.output_path)}</code></div>
        ${outDir ? `<button class="btn-secondary" onclick="openFolder('${escHtml(outDir)}')">Open Folder</button>` : ''}
      </div>`;
    recordRunHistory({
      view: 'fluorescence_gif',
      title: 'Merged GIF',
      status: 'ok',
      project_root: gifProjectRoot(),
      input_files: gifInputFileRecords(_tiffEntries.filter(e => e.path && e.path.trim())),
      outputs: d.output_path ? [{path: d.output_path, type: 'gif'}] : [],
      parameters: payload,
      metadata: {
        n_frames: d.n_frames || 0,
        selected_slices: d.selected_slices || '',
        scale: formatScaleInfo(d),
      },
    });
  } catch(ex) {
    btnBusy('btnGenerate', false, 'Generate GIF');
    setStatus('status', 'Request failed: ' + ex.message, 'error');
  }
}

function gifAnalysisEntries() {
  const queued = _tiffEntries
    .filter(e => e.path && e.path.trim())
    .map(e => ({path: e.path.trim(), slices: (e.slices || '').trim()}));
  if (queued.length) return queued;

  const preview = getPreviewEntry();
  if (preview && preview.path) {
    return [{path: preview.path.trim(), slices: (preview.slices || '').trim()}];
  }
  return [];
}

function gifInputFileRecords(entries) {
  return (entries || [])
    .filter(e => e && e.path)
    .map(e => ({path: e.path, role: 'source_tiff', slices: e.slices || ''}));
}

function gifRoiNowStamp() {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, '_');
}

function sanitizeGifRoiPrefix(s) {
  const raw = String(s || '').trim() || 'gif_roi_time_analysis';
  return raw.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '') || 'gif_roi_time_analysis';
}

function buildGifRoiPrefix() {
  return sanitizeGifRoiPrefix(document.getElementById('gifRoiPrefix')?.value) + '_' + gifRoiNowStamp();
}

function buildGifRoiReferencePrefix(entry) {
  const baseName = fileBasename(entry && entry.path ? entry.path : '').replace(/\.[^.]+$/, '');
  const raw = (baseName || 'gif') + '_roi_reference';
  return raw.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '') + '_' + gifRoiNowStamp();
}

function upsertGifResultCard(cardId, headerHtml, bodyHtml) {
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
    <div class="result-card-header">${headerHtml}</div>
    <div class="result-card-body" style="padding:8px">${bodyHtml}</div>`;
}

async function exportGifRoiPreview() {
  const entry = getPreviewEntry();
  const rois = getClosedRoiPolygons();
  if (!entry || !entry.path) {
    setStatus('status', 'Choose a preview TIFF before exporting ROI preview', 'error');
    return;
  }
  if (!rois.length) {
    setStatus('status', 'Draw and close at least one polygon ROI first', 'error');
    return;
  }

  btnBusy('btnExportGifRoiPreview', true, 'Exporting...');
  setStatus('status', 'Exporting ROI preview PNG...', 'loading');
  try {
    const d = await runGifBackgroundJob('/api/fluorescence/gif_roi/export_preview_job', {
      input_path: entry.path.trim(),
      slice_spec: (entry.slices || '').trim(),
      roi_polygons: rois,
      lut: document.getElementById('gifLut').value,
      scale_bar_um: parseFloat(document.getElementById('gifBarUm').value) || 0,
      px_per_um: parseFloat(document.getElementById('gifPxPerUm').value) || 3.45,
      auto_scale: document.getElementById('gifAutoScale').checked,
      show_name: true,
      show_scale_bar: true,
      ...gifCropPayload(),
      prefix: buildGifRoiReferencePrefix(entry),
    }, 'Exporting ROI preview PNG');
    btnBusy('btnExportGifRoiPreview', false, 'Export ROI Preview');
    if (d.error) throw new Error(d.error);
    _gifRoiDefaultOutputDir = d.output_dir || _gifRoiDefaultOutputDir;

    const openButton = d.output_dir
      ? `<button class="btn-secondary" data-folder="${escHtml(d.output_dir)}" onclick="openFolder(this.dataset.folder)">Open Folder</button>`
      : '';
    const header =
      `ROI Preview <span style="font-weight:400;color:var(--silver)">` +
      `${d.roi_polygons || rois.length} ROI | slice ${d.frame} | ${escHtml(formatScaleInfo(d))}</span>`;
    const body = `
      <img src="data:image/png;base64,${d.img}" style="max-width:100%;border-radius:4px"/>
      <div style="font-size:11px;color:var(--silver);margin-top:6px;word-break:break-all">Exported: <code>${escHtml(d.output_path || '')}</code></div>
      <div style="font-size:11px;color:var(--silver);margin-top:2px">Scale bar: ${Number(d.scale_bar_um || 0).toPrecision(4)} um · ${escHtml(formatScaleInfo(d))}</div>
      <div style="margin-top:8px">${openButton}</div>`;
    upsertGifResultCard('gifRoiPreviewResultCard', header, body);
    setStatus('status', 'ROI preview exported: ' + (d.output_path || ''), 'ok');
    toast('ROI preview exported');
    recordRunHistory({
      view: 'fluorescence_gif',
      title: 'GIF ROI Preview',
      status: 'ok',
      project_root: gifProjectRoot(),
      input_files: gifInputFileRecords([entry]),
      outputs: d.output_path ? [{path: d.output_path, type: 'roi_preview_png'}] : [],
      parameters: {
        settings: collectGifPrefs(),
        roi_polygons: rois,
        crop: gifCropPayload(),
      },
      metadata: {
        output_dir: d.output_dir || '',
        frame: d.frame,
      },
    });
  } catch(ex) {
    btnBusy('btnExportGifRoiPreview', false, 'Export ROI Preview');
    setStatus('status', 'Export failed: ' + ex.message, 'error');
    toast('Export failed: ' + ex.message, true);
  }
}

function buildGifRoiAnalysisPayload() {
  const entries = gifAnalysisEntries();
  if (!entries.length) {
    throw new Error('Add at least one TIFF to the queue, or select a preview TIFF');
  }

  const allPolys = getClosedRoiPolygons();
  if (!allPolys.length) {
    throw new Error('Draw and close at least one polygon ROI first');
  }

  const bgMode = document.getElementById('gifBgMode').value;
  const bgLabel = gifBgLabel();
  const plotMetric = document.getElementById('gifRoiPlotMetric').value;
  let bgRoi = null;
  let signalRois = allPolys.slice();

  if (bgMode === 'roi') {
    bgRoi = allPolys.find(p => p.label === bgLabel) || null;
    if (!bgRoi) throw new Error('Select a background ROI or change BG mode');
    signalRois = allPolys.filter(p => p.label !== bgLabel);
  }

  if (!signalRois.length) {
    throw new Error('All polygons are assigned to background; draw another signal ROI');
  }
  if ((plotMetric === 'bg_subtracted' || plotMetric === 'bg_normalized') && bgMode === 'none') {
    throw new Error('Choose a BG mode before using BG Subtracted or F / F_BG');
  }

  const fps = parseFloat(document.getElementById('gifFps').value) || 5;
  const refFrame = parseInt(document.getElementById('gifRoiRefFrame').value, 10) || 1;
  return {
    tiff_paths: entries.map(e => e.path),
    slice_specs: entries.map(e => e.slices),
    rois: signalRois,
    bg_mode: bgMode,
    bg_roi: bgRoi || undefined,
    metric: document.getElementById('gifRoiMetric').value,
    plot_metric: plotMetric,
    fps,
    ref_frame: Math.max(1, refFrame),
  };
}

async function runGifRoiAnalysis() {
  let payload;
  try {
    payload = buildGifRoiAnalysisPayload();
  } catch(ex) {
    setStatus('status', ex.message, 'error');
    return;
  }

  btnBusy('btnAnalyzeGifRoi', true, 'Analyzing...');
  setStatus('status', 'Running ROI time analysis...', 'loading');

  try {
    const d = await runGifBackgroundJob('/api/fluorescence/gif_roi/analyze_job', payload, 'Running ROI time analysis');
    btnBusy('btnAnalyzeGifRoi', false, 'Analyze ROI Time');
    if (d.error) {
      setStatus('status', 'Error: ' + d.error, 'error');
      return;
    }

    _gifRoiCsvContent = d.csv || null;
    _gifRoiPlotB64 = d.img || null;
    _gifRoiDefaultOutputDir = d.default_output_dir || _gifRoiDefaultOutputDir;

    const refText = d.plot_metric === 'delta_f_over_f0' ? ` | ref frame ${d.ref_frame_applied}` : '';
    const warnText = (d.warnings || []).length
      ? `<div style="font-size:11px;color:#9a6a00;margin-top:6px">${(d.warnings || []).map(escHtml).join('<br>')}</div>`
      : '';
    const header =
      `ROI Time Analysis <span style="font-weight:400;color:var(--silver)">` +
      `${d.n_frames} frames | ${d.n_rois} ROI | ${d.metric} | ${d.plot_metric}${refText}</span>`;
    const body = `
      <img src="data:image/png;base64,${d.img}" style="max-width:100%;border-radius:4px"/>
      ${warnText}`;
    upsertGifResultCard('gifRoiTimeResultCard', header, body);
    document.getElementById('gifRoiExportSection').style.display = '';
    setStatus('status', `ROI time analysis complete: ${d.n_frames} frame(s)`, 'ok');
    toast('ROI time analysis complete');
  } catch(ex) {
    btnBusy('btnAnalyzeGifRoi', false, 'Analyze ROI Time');
    setStatus('status', 'Request failed: ' + ex.message, 'error');
  }
}

async function saveGifRoiOutputs(opts) {
  const options = Object.assign({saveCsv: true, savePlot: true}, opts || {});
  const hasRequestedOutput =
    (options.saveCsv && !!_gifRoiCsvContent) ||
    (options.savePlot && !!_gifRoiPlotB64);
  if (!hasRequestedOutput) {
    setStatus('status', 'No ROI time-analysis output to export', 'error');
    return;
  }

  const entries = gifAnalysisEntries();
  const prefix = buildGifRoiPrefix();
  setStatus('status', 'Exporting ROI time-analysis output...', 'loading');

  try {
    const d = await runGifBackgroundJob('/api/fluorescence/gif_roi/export_job', {
      tiff_paths: entries.map(e => e.path),
      output_dir: _gifRoiDefaultOutputDir || undefined,
      prefix,
      save_csv: !!options.saveCsv,
      save_plot: !!options.savePlot,
      csv: _gifRoiCsvContent || '',
      plot_png_b64: _gifRoiPlotB64 || '',
    }, 'Exporting ROI time-analysis output');
    if (d.error) throw new Error(d.error);
    _gifRoiDefaultOutputDir = d.output_dir || _gifRoiDefaultOutputDir;
    setStatus('status', 'Exported: ' + (d.saved_paths || []).join(' | '), 'ok');
    toast('ROI time-analysis output exported');
    recordRunHistory({
      view: 'fluorescence_gif',
      title: 'GIF ROI Time Export',
      status: 'ok',
      project_root: gifProjectRoot(),
      input_files: gifInputFileRecords(entries),
      outputs: dpAsPathRecords(d.saved_paths || [], 'gif_roi_time_output'),
      parameters: {
        settings: collectGifPrefs(),
        export_options: options,
        prefix,
      },
      metadata: {
        output_dir: d.output_dir || '',
      },
    });
  } catch(ex) {
    setStatus('status', 'Export failed: ' + ex.message, 'error');
    toast('Export failed: ' + ex.message, true);
  }
}

function exportGifRoiCSV() {
  saveGifRoiOutputs({saveCsv: true, savePlot: false});
}

function exportGifRoiPlotPNG() {
  saveGifRoiOutputs({saveCsv: false, savePlot: true});
}

function exportGifRoiAll() {
  saveGifRoiOutputs({saveCsv: true, savePlot: true});
}

function sanitizeGifKymoPrefix(s) {
  const raw = String(s || '').trim() || 'gif_roi_kymograph';
  return raw.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '') || 'gif_roi_kymograph';
}

function buildGifKymoPrefix() {
  return sanitizeGifKymoPrefix(document.getElementById('gifKymoPrefix')?.value) + '_' + gifRoiNowStamp();
}

