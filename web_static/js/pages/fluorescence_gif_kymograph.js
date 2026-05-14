function parseGifKymoPercentList(raw, maxItems = 8) {
  const seen = new Set();
  const vals = String(raw || '')
    .split(/[,;\s]+/)
    .map(x => parseFloat(x))
    .filter(x => Number.isFinite(x) && x > 0 && x <= 100)
    .filter(x => {
      const key = x.toFixed(6);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  return vals.slice(0, maxItems);
}

function buildGifKymoPayload() {
  const entries = gifAnalysisEntries();
  if (!entries.length) {
    throw new Error('Add at least one TIFF to the queue, or select a preview TIFF');
  }

  const polys = getClosedRoiPolygons();
  if (!polys.length) {
    throw new Error('Draw and close at least one polygon ROI first');
  }

  const roiLabel = document.getElementById('gifKymoRoiSelect')?.value || '';
  const roi = polys.find(p => p.label === roiLabel) || null;
  if (!roi) {
    throw new Error('Choose one signal ROI for kymography');
  }

  const bgMode = document.getElementById('gifBgMode').value;
  const bgLabel = gifBgLabel();
  const bgRoi = bgMode === 'roi' ? (polys.find(p => p.label === bgLabel) || null) : null;
  if (bgMode === 'roi' && !bgRoi) {
    throw new Error('Select a background ROI or change BG mode');
  }
  if (bgMode === 'roi' && bgRoi.label === roi.label) {
    throw new Error('Kymography ROI cannot also be the background ROI');
  }

  const valueMode = document.getElementById('gifKymoValueMode').value;
  if (valueMode === 'bg_subtracted' && bgMode === 'none') {
    throw new Error('Choose a BG mode before using BG Subtracted');
  }

  const bins = parseInt(document.getElementById('gifKymoBins').value, 10) || 80;
  const lowPct = parseFloat(document.getElementById('gifKymoLowPct').value);
  const highPct = parseFloat(document.getElementById('gifKymoHighPct').value);
  const smoothIntensity = parseFloat(document.getElementById('gifKymoSmoothIntensity').value);
  const smoothTime = parseFloat(document.getElementById('gifKymoSmoothTime').value);
  const thresholdsRaw = document.getElementById('gifKymoThresholds')?.value || '';
  const thresholdLines = thresholdsRaw
    .split(/[,;\s]+/)
    .map(x => parseFloat(x))
    .filter(x => Number.isFinite(x));
  const overlayPercentiles = parseGifKymoPercentList(document.getElementById('gifKymoPercentiles')?.value || '', 8);
  const overlayTopMeans = parseGifKymoPercentList(document.getElementById('gifKymoTopMeans')?.value || '', 6);
  if (!Number.isFinite(lowPct) || !Number.isFinite(highPct) || highPct <= lowPct) {
    throw new Error('Range percentiles must be valid and high > low');
  }
  if (!Number.isFinite(smoothIntensity) || !Number.isFinite(smoothTime) || smoothIntensity < 0 || smoothTime < 0) {
    throw new Error('Smooth values must be zero or positive');
  }

  return {
    tiff_paths: entries.map(e => e.path),
    slice_specs: entries.map(e => e.slices),
    roi,
    bg_mode: bgMode,
    bg_roi: bgRoi || undefined,
    value_mode: valueMode,
    fps: parseFloat(document.getElementById('gifFps').value) || 5,
    ref_frame: Math.max(1, parseInt(document.getElementById('gifKymoRefFrame').value, 10) || 1),
    ref_stat: document.getElementById('gifKymoRefStat').value,
    bins: Math.max(8, Math.min(240, bins)),
    range_low_pct: lowPct,
    range_high_pct: highPct,
    smooth_intensity_bins: Math.max(0, Math.min(8, smoothIntensity)),
    smooth_time_frames: Math.max(0, Math.min(8, smoothTime)),
    smooth_lines: !!document.getElementById('gifKymoSmoothLines')?.checked,
    overlay_peak: !!document.getElementById('gifKymoPeakLine')?.checked,
    overlay_mean: !!document.getElementById('gifKymoMeanLine')?.checked,
    threshold_lines: thresholdLines,
    overlay_percentiles: overlayPercentiles,
    overlay_top_means: overlayTopMeans,
  };
}

async function runGifKymograph() {
  let payload;
  try {
    updateGifKymoControls();
    payload = buildGifKymoPayload();
  } catch(ex) {
    setStatus('status', ex.message, 'error');
    return;
  }

  btnBusy('btnRunGifKymo', true, 'Running...');
  setStatus('status', 'Building ROI kymograph...', 'loading');

  try {
    const d = await runGifBackgroundJob('/api/fluorescence/gif_roi/kymograph_job', payload, 'Building ROI kymograph');
    btnBusy('btnRunGifKymo', false, 'Run Kymograph');
    if (d.error) {
      setStatus('status', 'Error: ' + d.error, 'error');
      return;
    }

    _gifKymoHeatmapCsv = d.heatmap_csv || null;
    _gifKymoSummaryCsv = d.summary_csv || null;
    _gifKymoPlotB64 = d.img || null;
    _gifKymoDefaultOutputDir = d.default_output_dir || _gifKymoDefaultOutputDir;

    const refText = d.value_mode === 'delta_f_over_f0'
      ? ` | ref frame ${d.ref_frame_applied} (${d.ref_stat}, F0=${Number(d.f0_value || 0).toPrecision(4)})`
      : '';
    const warnText = (d.warnings || []).length
      ? `<div style="font-size:11px;color:#9a6a00;margin-top:6px">${(d.warnings || []).map(escHtml).join('<br>')}</div>`
      : '';
    const header =
      `ROI Kymograph <span style="font-weight:400;color:var(--silver)">` +
      `${escHtml(d.roi_label)} | ${d.n_frames} frames | ${d.bins} bins | ${d.value_mode} | smooth I ${Number(d.smooth_intensity_bins || 0).toFixed(1)} / T ${Number(d.smooth_time_frames || 0).toFixed(1)}${refText}</span>`;
    const thresholdText = (d.threshold_lines || []).length
      ? ` · thresholds: ${(d.threshold_lines || []).map(x => Number(x).toPrecision(4)).join(', ')}`
      : '';
    const overlayBits = [];
    if (d.overlay_peak) overlayBits.push('peak bin');
    if (d.overlay_mean) overlayBits.push('mean');
    if ((d.overlay_percentiles || []).length) overlayBits.push(`p% ${(d.overlay_percentiles || []).map(x => Number(x).toPrecision(4)).join(', ')}`);
    if ((d.overlay_top_means || []).length) overlayBits.push(`top mean % ${(d.overlay_top_means || []).map(x => Number(x).toPrecision(4)).join(', ')}`);
    const overlayText = overlayBits.length ? ` · overlay: ${overlayBits.join(' / ')}` : ' · no overlay lines';
    const body = `
      <img src="data:image/png;base64,${d.img}" style="max-width:100%;border-radius:4px"/>
      <div style="font-size:11px;color:var(--silver);margin-top:6px">Intensity range: ${Number(d.range_min).toPrecision(4)} to ${Number(d.range_max).toPrecision(4)}${thresholdText}${overlayText} · heatmap CSV includes raw and smoothed bin percentages</div>
      ${warnText}`;
    upsertGifResultCard('gifKymoResultCard', header, body);
    document.getElementById('gifKymoExportSection').style.display = '';
    setStatus('status', `Kymograph complete: ${d.n_frames} frame(s), ${d.bins} bins`, 'ok');
    toast('ROI kymograph complete');
  } catch(ex) {
    btnBusy('btnRunGifKymo', false, 'Run Kymograph');
    setStatus('status', 'Request failed: ' + ex.message, 'error');
  }
}

async function saveGifKymoOutputs(opts) {
  const options = Object.assign({saveHeatmapCsv: true, saveSummaryCsv: true, savePlot: true}, opts || {});
  const hasRequestedOutput =
    (options.saveHeatmapCsv && !!_gifKymoHeatmapCsv) ||
    (options.saveSummaryCsv && !!_gifKymoSummaryCsv) ||
    (options.savePlot && !!_gifKymoPlotB64);
  if (!hasRequestedOutput) {
    setStatus('status', 'No kymograph output to export', 'error');
    return;
  }

  const entries = gifAnalysisEntries();
  const prefix = buildGifKymoPrefix();
  setStatus('status', 'Exporting kymograph output...', 'loading');
  try {
    const d = await runGifBackgroundJob('/api/fluorescence/gif_roi/kymograph_export_job', {
      tiff_paths: entries.map(e => e.path),
      output_dir: _gifKymoDefaultOutputDir || undefined,
      prefix,
      save_heatmap_csv: !!options.saveHeatmapCsv,
      save_summary_csv: !!options.saveSummaryCsv,
      save_plot: !!options.savePlot,
      heatmap_csv: _gifKymoHeatmapCsv || '',
      summary_csv: _gifKymoSummaryCsv || '',
      plot_png_b64: _gifKymoPlotB64 || '',
    }, 'Exporting kymograph output');
    if (d.error) throw new Error(d.error);
    _gifKymoDefaultOutputDir = d.output_dir || _gifKymoDefaultOutputDir;
    setStatus('status', 'Exported: ' + (d.saved_paths || []).join(' | '), 'ok');
    toast('Kymograph output exported');
    recordRunHistory({
      view: 'fluorescence_gif',
      title: 'GIF Kymograph Export',
      status: 'ok',
      project_root: gifProjectRoot(),
      input_files: gifInputFileRecords(entries),
      outputs: dpAsPathRecords(d.saved_paths || [], 'gif_kymograph_output'),
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

function exportGifKymoPlotPNG() {
  saveGifKymoOutputs({saveHeatmapCsv: false, saveSummaryCsv: false, savePlot: true});
}

function exportGifKymoHeatmapCSV() {
  saveGifKymoOutputs({saveHeatmapCsv: true, saveSummaryCsv: false, savePlot: false});
}

function exportGifKymoSummaryCSV() {
  saveGifKymoOutputs({saveHeatmapCsv: false, saveSummaryCsv: true, savePlot: false});
}

function exportGifKymoAll() {
  saveGifKymoOutputs({saveHeatmapCsv: true, saveSummaryCsv: true, savePlot: true});
}

async function openFolder(path) {
  try { await api('/api/scripts/open_folder', { path }); }
  catch(e) { toast('Could not open folder', true); }
}

function setupRoiCanvasEvents() {
  const canvas = document.getElementById('gifRoiCanvas');
  if (!canvas) return;
  canvas.addEventListener('mousedown', e => {
    if (!_draftCropRect || !_imgW || !_imgH) return;
    const rect = canvas.getBoundingClientRect();
    const pt = canvasToNative(e.clientX - rect.left, e.clientY - rect.top);
    _cropRectDragStart = pt;
    _draftCropRect.x = pt.x;
    _draftCropRect.y = pt.y;
    _draftCropRect.width = 0;
    _draftCropRect.height = 0;
    drawPolygons();
  });
  canvas.addEventListener('mousemove', e => {
    if (!_draftCropRect || !_cropRectDragStart || !_imgW || !_imgH) return;
    const rect = canvas.getBoundingClientRect();
    const pt = canvasToNative(e.clientX - rect.left, e.clientY - rect.top);
    _draftCropRect.x = _cropRectDragStart.x;
    _draftCropRect.y = _cropRectDragStart.y;
    _draftCropRect.width = pt.x - _cropRectDragStart.x;
    _draftCropRect.height = pt.y - _cropRectDragStart.y;
    renderCropRectList();
    drawPolygons();
  });
  canvas.addEventListener('mouseup', () => {
    if (_draftCropRect && _cropRectDragStart) finishCropRect();
  });
  canvas.addEventListener('mouseleave', () => {
    if (_draftCropRect && _cropRectDragStart) finishCropRect();
  });
  canvas.addEventListener('click', e => {
    if (!_draftPolygon || !_imgW || !_imgH) return;
    const rect = canvas.getBoundingClientRect();
    const pt = canvasToNative(e.clientX - rect.left, e.clientY - rect.top);
    _draftPolygon.points.push(pt);
    renderPolygonList();
    drawPolygons();
  });
  canvas.addEventListener('contextmenu', e => {
    e.preventDefault();
    if (_draftPolygon && _draftPolygon.points.length >= 3) closePolygon();
  });
}

/* ---------- Init ---------- */
document.addEventListener('DOMContentLoaded', async () => {
  try {
    _gifPrefs = await loadViewPreferences('fluorescence_gif');
    applyGifPrefs(_gifPrefs.defaults || {});
    if (_gifPrefs.defaults) setStatus('gifPrefsStatus', 'Defaults loaded.', 'ok');
  } catch (e) {
    setStatus('gifPrefsStatus', 'Defaults not loaded.', 'warning');
  }
  toggleScaleMode();
  renderAvailableTiffList();
  renderTiffList();
  renderPolygonList();
  renderCropRectList();
  updateGifBgControls();
  updateGifKymoControls();
  updateGifCropControls();
  clearGeneratedGifPreview();
  setupRoiCanvasEvents();
});

document.addEventListener('dp:prefs-saved', event => {
  if (!event.detail || event.detail.view !== 'fluorescence_gif') return;
  _gifPrefs = event.detail.data || {};
  applyGifPrefs(_gifPrefs.defaults || {});
  setStatus('gifPrefsStatus', 'Defaults updated from Settings.', 'ok');
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'buildGifKymoPayload',
  'exportGifKymoAll',
  'exportGifKymoHeatmapCSV',
  'exportGifKymoPlotPNG',
  'exportGifKymoSummaryCSV',
  'openFolder',
  'parseGifKymoPercentList',
  'runGifKymograph',
  'saveGifKymoOutputs',
  'setupRoiCanvasEvents',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
