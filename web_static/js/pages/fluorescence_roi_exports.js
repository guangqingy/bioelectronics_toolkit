function nowStamp() {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, '_');
}

function sanitizePrefix(s) {
  const raw = String(s || '').trim();
  const use = raw || 'roi_analysis';
  return use.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '') || 'roi_analysis';
}

function buildExportPrefix() {
  const base = sanitizePrefix(document.getElementById('outPrefix').value);
  return base + '_' + nowStamp();
}

function activeRecordsForExport() {
  if (_analysisPairs && _analysisPairs.length) return _analysisPairs.slice();
  if (_currentPair) return [_currentPair];
  return [];
}

function roiInputFileRecords(records) {
  const out = [];
  (records || []).forEach(rec => {
    if (rec.stack1) out.push({path: rec.stack1, role: 'stack1', pair: rec.base || ''});
    if (rec.stack2) out.push({path: rec.stack2, role: 'stack2', pair: rec.base || ''});
  });
  return out;
}

function saveSequenceOutputs(opts) {
  const options = Object.assign({
    saveCsv: true,
    savePlot: true,
    savePreview: true,
    saveRadialCsv: true,
    saveRadialPlot: true,
  }, opts || {});
  const records = activeRecordsForExport();
  if (!records.length) {
    setStatus('status', 'No records available for export path resolution', 'error');
    return;
  }

	  const hasContent =
	    (options.saveCsv && !!_csvContent) ||
	    (options.savePlot && !!_lastPlotB64) ||
	    (options.savePreview && !!_lastPreviewB64) ||
	    (options.saveRadialCsv && !!_radialCsvContent) ||
	    (options.saveRadialPlot && !!_lastRadialPlotB64);
  if (!hasContent) {
    setStatus('status', 'No export content available', 'error');
    return;
  }

  const prefix = buildExportPrefix();
  _lastExportPrefix = prefix;
  setStatus('status', 'Exporting outputs to disk...', 'loading');

  dpRunJobEndpoint('/api/fluorescence/roi/export_sequence_job', {
    records,
    output_dir: _defaultOutputDir || undefined,
    prefix,
    save_csv: !!options.saveCsv,
    save_plot: !!options.savePlot,
	    save_preview: !!options.savePreview,
	    save_radial_csv: !!options.saveRadialCsv,
	    save_radial_plot: !!options.saveRadialPlot,
	    csv: _csvContent || '',
	    plot_png_b64: _lastPlotB64 || '',
	    roi_preview_png_b64: _lastPreviewB64 || '',
	    radial_csv: _radialCsvContent || '',
	    radial_plot_png_b64: _lastRadialPlotB64 || ''
	  }, {
	    interval_ms: 1000,
	    on_update: job => {
	      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
	      const msg = job.message ? ` · ${job.message}` : '';
	      setStatus('status', `Exporting outputs to disk${pct}${msg}`, 'loading');
	    },
	  }).then(d => {
    if (d.error) throw new Error(d.error);
    _defaultOutputDir = d.output_dir || _defaultOutputDir;
    const saved = (d.saved_paths || []).join(' | ');
    setStatus('status', 'Exported: ' + (saved || 'ok'), 'ok');
    toast('Exported to disk');
    recordRunHistory({
      view: 'fluorescence_roi',
      title: 'ROI Sequence Export',
      status: 'ok',
      project_root: roiProjectRoot(),
      input_files: roiInputFileRecords(records),
      outputs: dpAsPathRecords(d.saved_paths || [], 'roi_sequence_output'),
      parameters: {
        settings: collectRoiSettings(),
        export_options: options,
        rois: _rois.filter(r => r.drawn).map(roiToPayload).filter(Boolean),
        prefix,
      },
      metadata: {
        output_dir: d.output_dir || '',
        records: records.length,
      },
    });
  }).catch(e => {
    setStatus('status', 'Error: ' + e.message, 'error');
    toast('Export failed: ' + e.message, true);
  });
}

function upsertResultCard(cardId, headerHtml, bodyHtml) {
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

function runAnalysis() {
  const bgMode = document.getElementById('bgMode').value;
  const bgLabel = document.getElementById('bgRoiSelect').value;
  const metric = document.getElementById('metric').value;
  const plotMetric = document.getElementById('plotMetric').value;
  const refSequence = document.getElementById('refSequence').value.trim();
  const scaleBarUm = parseFloat(document.getElementById('scaleBarUm').value) || 0;
  const pixelSizeRaw = document.getElementById('pixelSizeUm').value.trim();
  const pixelSizeUm = pixelSizeRaw ? parseFloat(pixelSizeRaw) : undefined;
  const scaleBarLabel = document.getElementById('scaleLabel').value.trim();
  const labelScale = parseFloat(document.getElementById('labelScale').value) || 2.0;
  const showPreviewName = document.getElementById('showPreviewName').checked;
  const showScaleBar = document.getElementById('showScaleBar').checked;

  if (pixelSizeRaw && (!Number.isFinite(pixelSizeUm) || pixelSizeUm <= 0)) {
    setStatus('status', 'Pixel size must be > 0 or left blank for auto', 'error');
    return;
  }

  let signalRois = _rois.filter(r => r.drawn);
	  let bgRoiPayload = null;
	  if (bgMode === 'roi' && bgLabel) {
	    const bgRoi = signalRois.find(r => r.label === bgLabel);
	    if (bgRoi) {
	      bgRoiPayload = roiToPayload(bgRoi);
	      signalRois = signalRois.filter(r => r.label !== bgLabel);
	    }
	  }

  if (!signalRois.length) {
    setStatus('status', 'Draw at least one signal ROI before analysis', 'error');
    return;
  }

  let records = _analysisPairs.slice();
  if (!records.length && _currentPair) records = [_currentPair];
  if (!records.length) {
    setStatus('status', 'No file selected: add files to analysis list or select one pair', 'error');
    return;
  }

	  const rois = signalRois.map(roiToPayload).filter(Boolean);

  btnBusy('btnAnalyze', true, 'Analyzing...');
  setStatus('status', 'Running sequence analysis on ' + records.length + ' file(s)...', 'loading');

  const previewStack = document.getElementById('stackSelect').value;
  const previewPath = previewStack === 'stack2' ? _stack2Path : _stack1Path;

  api('/api/fluorescence/roi/analyze_sequence', {
    records,
    rois,
    metric,
    plot_metric: plotMetric,
    bg_mode: bgMode,
    bg_roi: bgRoiPayload || undefined,
    ref_sequence: refSequence || undefined,
    preview_stack: previewStack,
    preview_path: previewPath || undefined,
    scale_bar_um: scaleBarUm,
    pixel_size_um: pixelSizeUm,
    scale_bar_label: scaleBarLabel || undefined,
    label_scale: labelScale,
    show_preview_name: showPreviewName,
    show_scale_bar: showScaleBar,
    img_width: _imgW,
    img_height: _imgH,
  }).then(d => {
    btnBusy('btnAnalyze', false, 'Run Analysis');
    if (d.error) {
      setStatus('status', 'Error: ' + d.error, 'error');
      return;
    }

	    _csvContent = d.csv || null;
	    _lastPlotB64 = d.img || null;
	    _lastPreviewB64 = d.roi_preview_img || null;
	    _radialCsvContent = d.radial_csv || null;
	    _lastRadialPlotB64 = d.radial_img || null;
	    _lastRefSequence = d.ref_sequence_applied || '';
	    _defaultOutputDir = d.default_output_dir || _defaultOutputDir;

    const refText = d.ref_sequence_applied ? ` | ref=${d.ref_sequence_applied}` : '';
    const header =
      `ROI Sequence Result <span style="font-weight:400;color:var(--silver)">` +
      `${d.n_records} files | ${d.n_rois} ROI | ${d.metric} | ${d.plot_metric}${refText}</span>`;
    const body = `<img class="roi-result-img" src="data:image/png;base64,${d.img}" alt="ROI sequence plot"/>`;
    upsertResultCard('roiSequenceResultCard', header, body);

	    if (_lastPreviewB64) {
	      const pxInfo = d.roi_preview_pixel_size_um ? ` | pixel=${Number(d.roi_preview_pixel_size_um).toFixed(5)} um/px` : '';
	      const prevHeader =
	        `ROI Reference Preview <span style="font-weight:400;color:var(--silver)">` +
	        `${d.roi_preview_path ? escapeHtml(d.roi_preview_path.split('/').pop()) : 'selected preview'}${pxInfo}</span>`;
	      const prevBody = `<img class="roi-reference-img" src="data:image/png;base64,${_lastPreviewB64}" alt="ROI reference preview"/>`;
	      upsertResultCard('roiPreviewResultCard', prevHeader, prevBody);
	    }

	    if (_lastRadialPlotB64) {
	      const radialHeader =
	        `Concentric ROI Ring Sequence <span style="font-weight:400;color:var(--silver)">` +
	        `${d.n_radial_rows || 0} ring measurements</span>`;
	      const radialBody = `<img class="roi-result-img" src="data:image/png;base64,${_lastRadialPlotB64}" alt="Concentric ROI ring plot"/>`;
	      upsertResultCard('roiRadialResultCard', radialHeader, radialBody);
	    } else {
	      const oldRadial = document.getElementById('roiRadialResultCard');
	      if (oldRadial) oldRadial.remove();
	    }

    document.getElementById('exportSection').style.display = '';
    setStatus('status', 'Analysis complete: ' + d.n_records + ' files processed', 'ok');
    toast('ROI sequence analysis complete');
  }).catch(e => {
    btnBusy('btnAnalyze', false, 'Run Analysis');
    const msg = String((e && e.message) || e || 'Unknown error');
    if (/failed to fetch|networkerror|network error/i.test(msg)) {
      setStatus('status', 'Error: Backend service is not running (start web_app.py first)', 'error');
      return;
    }
    setStatus('status', 'Error: ' + msg, 'error');
  });
}

function exportCSV() {
  if (!_csvContent) {
    setStatus('status', 'No analysis CSV to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: true, savePlot: false, savePreview: false, saveRadialCsv: false, saveRadialPlot: false });
}

function exportPlotPNG() {
  if (!_lastPlotB64) {
    setStatus('status', 'No analysis plot to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: false, savePlot: true, savePreview: false, saveRadialCsv: false, saveRadialPlot: false });
}

function exportPreviewPNG() {
  if (!_lastPreviewB64) {
    setStatus('status', 'No ROI preview to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: false, savePlot: false, savePreview: true, saveRadialCsv: false, saveRadialPlot: false });
}

function exportRadialCSV() {
  if (!_radialCsvContent) {
    setStatus('status', 'No radial CSV to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: false, savePlot: false, savePreview: false, saveRadialCsv: true, saveRadialPlot: false });
}

function exportRadialPlotPNG() {
  if (!_lastRadialPlotB64) {
    setStatus('status', 'No radial plot to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: false, savePlot: false, savePreview: false, saveRadialCsv: false, saveRadialPlot: true });
}

function exportGif() {
  const records = activeRecordsForExport();
  if (!records.length) {
    setStatus('status', 'No records selected for GIF export', 'error');
    return;
  }

  const frameMs = parseInt(document.getElementById('gifFrameMs').value, 10) || 2000;
  const scaleBarUm = parseFloat(document.getElementById('scaleBarUm').value) || 0;
  const pixelSizeRaw = document.getElementById('pixelSizeUm').value.trim();
  const pixelSizeUm = pixelSizeRaw ? parseFloat(pixelSizeRaw) : undefined;
  const scaleBarLabel = document.getElementById('scaleLabel').value.trim();
  const labelScale = parseFloat(document.getElementById('labelScale').value) || 2.0;
  const showPreviewName = document.getElementById('showPreviewName').checked;
  const showScaleBar = document.getElementById('showScaleBar').checked;

  if (frameMs < 20) {
    setStatus('status', 'GIF frame ms must be >= 20', 'error');
    return;
  }
  if (pixelSizeRaw && (!Number.isFinite(pixelSizeUm) || pixelSizeUm <= 0)) {
    setStatus('status', 'Pixel size must be > 0 or left blank for auto', 'error');
    return;
  }

	  const rois = _rois.filter(r => r.drawn).map(roiToPayload).filter(Boolean);

  const prefix = buildExportPrefix();
  _lastExportPrefix = prefix;
  setStatus('status', 'Exporting GIF to disk...', 'loading');
  dpRunJobEndpoint('/api/fluorescence/roi/export_sequence_gif_job', {
    records,
    rois,
    preview_stack: document.getElementById('stackSelect').value,
    output_dir: _defaultOutputDir || undefined,
    prefix,
    frame_ms: frameMs,
    scale_bar_um: scaleBarUm,
    pixel_size_um: pixelSizeUm,
    scale_bar_label: scaleBarLabel || undefined,
    label_scale: labelScale,
    show_preview_name: showPreviewName,
    show_scale_bar: showScaleBar,
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Exporting GIF to disk${pct}${msg}`, 'loading');
    },
  }).then(d => {
    if (d.error) throw new Error(d.error);
    if (d.gif_path) {
      const pos = d.gif_path.lastIndexOf('/');
      if (pos > 0) _defaultOutputDir = d.gif_path.slice(0, pos);
    }
    setStatus('status', 'Exported GIF: ' + (d.gif_path || 'ok'), 'ok');
    toast('GIF exported to disk');
    recordRunHistory({
      view: 'fluorescence_roi',
      title: 'ROI Sequence GIF',
      status: 'ok',
      project_root: roiProjectRoot(),
      input_files: roiInputFileRecords(records),
      outputs: d.gif_path ? [{path: d.gif_path, type: 'roi_sequence_gif'}] : [],
      parameters: {
        settings: collectRoiSettings(),
        frame_ms: frameMs,
        preview_stack: document.getElementById('stackSelect').value,
        rois,
        prefix,
      },
      metadata: {
        n_frames: d.n_frames || records.length,
        pixel_size_um: d.pixel_size_um || null,
      },
    });
  }).catch(e => {
    setStatus('status', 'Error: ' + e.message, 'error');
    toast('GIF export failed: ' + e.message, true);
  });
}

function exportAllOutputs() {
  if (!_csvContent && !_lastPlotB64 && !_lastPreviewB64 && !_radialCsvContent && !_lastRadialPlotB64) {
    setStatus('status', 'No analysis outputs to export', 'error');
    return;
  }
  saveSequenceOutputs({ saveCsv: true, savePlot: true, savePreview: true, saveRadialCsv: true, saveRadialPlot: true });
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'activeRecordsForExport',
  'buildExportPrefix',
  'exportAllOutputs',
  'exportCSV',
  'exportGif',
  'exportPlotPNG',
  'exportPreviewPNG',
  'exportRadialCSV',
  'exportRadialPlotPNG',
  'nowStamp',
  'roiInputFileRecords',
  'runAnalysis',
  'sanitizePrefix',
  'saveSequenceOutputs',
  'upsertResultCard',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
