function runProcessing() {
  if (!_currentFile || !_currentChannel) {
    setStatus('status', 'No channel selected', 'error');
    return;
  }
  const payload = currentProcessingPayload();
  btnBusy('btnProcess', true, 'Generating...');
  setStatus('status', 'Generating processing plot...', 'loading');
  api('/api/emg/analysis/process', payload)
    .then(data => {
      if (data.error) throw new Error(data.error);
      setPlot('processArea', data.img);
      setStatus('status', 'Processing plot ready', 'ok');
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'))
    .finally(() => btnBusy('btnProcess', false, 'Generate Processing Plot'));
}

function exportProcessing(fmt) {
  if (!_currentFile || !_currentChannel) {
    setStatus('status', 'No channel selected', 'error');
    return;
  }
  const payload = currentProcessingPayload({fmt, mode: 'save'});
  setStatus('status', 'Exporting processing ' + fmt.toUpperCase() + '...', 'loading');
  dpRunJobEndpoint('/api/emg/analysis/export_processing_job', payload, {
    interval_ms: 800,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Exporting processing ${fmt.toUpperCase()}${pct}${msg}`, 'loading');
    },
  })
    .then(data => {
      if (data.error) throw new Error(data.error);
      const savedPath = data.saved_path || ((data.outputs || [])[0] || {}).path || 'ok';
      setStatus('status', 'Exported processing ' + fmt.toUpperCase() + ': ' + savedPath, 'ok');
      recordRunHistory({
        view: 'emg_analysis',
        title: 'EMG Analysis Processing ' + fmt.toUpperCase() + ' Export',
        status: 'ok',
        project_root: document.getElementById('folderPath').value.trim(),
        input_files: [{path: _currentFile, role: 'source_emg_recording'}],
        outputs: data.outputs || (data.saved_path ? [{path: data.saved_path, type: 'processing_' + fmt}] : []),
        parameters: emgAnalysisExportParams({
          mode: 'processing',
          channel: _currentChannel,
          fmt,
          ...currentProcessingParams(),
        }),
        metadata: {
          process_type: data.process_type || payload.process_type,
        },
      });
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function saveAllChannels() {
  if (!_currentFile) {
    setStatus('status', 'No file selected', 'error');
    return;
  }

  btnBusy('btnSaveAll', true, 'Exporting...');
  setStatus('status', 'Exporting all channels...', 'loading');

  dpRunJobEndpoint('/api/emg/analysis/export_all_job', {
    path: _currentFile,
    mode: 'save',
    merge_pair: document.getElementById('mergePair').checked,
    wide_csv: document.getElementById('wideCsv').checked
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Exporting all channels${pct}${msg}`, 'loading');
    },
  })
    .then(d => {
      setStatus('status', 'Exported: ' + (d.saved_path || 'ok'), 'ok');
      recordRunHistory({
        view: 'emg_analysis',
        title: 'EMG Analysis All Channels Export',
        status: 'ok',
        project_root: document.getElementById('folderPath').value.trim(),
        input_files: [{path: _currentFile, role: 'source_emg_recording'}],
        outputs: dpAsPathRecords(d.saved_paths || (d.saved_path ? [d.saved_path] : []), document.getElementById('wideCsv').checked ? 'wide_tsv' : 'channel_csv'),
        parameters: emgAnalysisExportParams({mode: 'all_channels'}),
        metadata: {
          saved_count: d.saved_count || 0,
        },
      });
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'))
    .finally(() => btnBusy('btnSaveAll', false, 'Export All Channels'));
}

function exportFig(fmt) {
  if (!_currentFile || !_currentChannel) {
    setStatus('status', 'No channel selected', 'error');
    return;
  }
  const payload = {
    path: _currentFile,
    channel: _currentChannel,
    fmt,
    mode: 'save',
    merge_pair: previewMergeEnabled() ? '1' : '0',
    downsample: currentDownsampleValue(),
  };
  const viewParams = currentViewParams();
  ['x_min', 'x_max', 'y_min', 'y_max', 'filter_low_hz', 'filter_high_hz', 'filter_notch_hz', 'filter_order', 'filter_notch_q'].forEach(key => {
    if (viewParams[key] !== null && Number.isFinite(viewParams[key])) payload[key] = viewParams[key];
  });
  payload.invert_y = viewParams.invert_y;
  payload.filter_type = viewParams.filter_type || 'none';
  const figureParams = currentFigureParams();
  ['fig_width_in', 'fig_height_in', 'fig_dpi', 'trace_line_width'].forEach(key => {
    if (figureParams[key] !== null && Number.isFinite(figureParams[key])) payload[key] = figureParams[key];
  });
  payload.trace_color = figureParams.trace_color || '#3E6AE1';
  payload.show_grid = figureParams.show_grid;
  payload.show_title = figureParams.show_title;
  setStatus('status', 'Exporting current channel...', 'loading');
  dpRunJobEndpoint('/api/emg/analysis/export_channel_job', payload, {
    interval_ms: 800,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Exporting current channel${pct}${msg}`, 'loading');
    },
  })
    .then(d => {
      setStatus('status', 'Exported: ' + (d.saved_path || 'ok'), 'ok');
      recordRunHistory({
        view: 'emg_analysis',
        title: 'EMG Analysis Current Channel ' + fmt.toUpperCase() + ' Export',
        status: 'ok',
        project_root: document.getElementById('folderPath').value.trim(),
        input_files: [{path: _currentFile, role: 'source_emg_recording'}],
        outputs: d.saved_path ? [{path: d.saved_path, type: fmt === 'csv' ? 'channel_csv' : 'channel_figure'}] : [],
        parameters: emgAnalysisExportParams({mode: 'single_channel', channel: _currentChannel, fmt}),
      });
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function exportCSV() {
  exportFig('csv');
}

window.addEventListener('load', () => {
  dpBindParamGroups('processType', 'data-process-mode');
  dpBindParamGroups('filterType', 'data-filter-mode');
  queueRender();
  setStatus('status', 'Ready', 'ok');
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'exportCSV',
  'exportFig',
  'exportProcessing',
  'runProcessing',
  'saveAllChannels',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
