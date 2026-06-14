function emgOutputBasename(path) {
  return String(path || '').split(/[\\/]/).pop() || String(path || '');
}

function renderEmgExportOutputs(data) {
  const card = document.getElementById('exportResultCard');
  const summary = document.getElementById('exportOutputSummary');
  const list = document.getElementById('exportOutputList');
  if (!card || !summary || !list) return;

  const segN = data.segment_count || 0;
  const linkedN = data.linked_channel_count || 0;
  const summaryPath = data.summary_path || '';
  const paths = data.saved_paths || (summaryPath ? [summaryPath] : []);
  summary.textContent = `Saved ${segN} segment file(s)`
    + (linkedN ? ` · linked channels: ${linkedN}` : '')
    + (summaryPath ? ` · summary: ${emgOutputBasename(summaryPath)}` : '');
  list.innerHTML = '';

  paths.slice(0, 80).forEach(path => {
    const item = document.createElement('div');
    item.className = 'output-path-item';
    item.title = path;
    item.textContent = path;
    list.appendChild(item);
  });
  if (paths.length > 80) {
    const more = document.createElement('div');
    more.className = 'output-path-more';
    more.textContent = `+ ${paths.length - 80} more output path(s)`;
    list.appendChild(more);
  }
  card.hidden = false;
}

function exportGrouped() {
  if (_peaks.length === 0) {
    setStatus('status', 'No peaks to export', 'error');
    return;
  }

  btnBusy('btnExport', true, 'Exporting...');
  setStatus('status', 'Saving grouped peaks...', 'loading');
  const linkedChannels = collectLinkedChannelNames();

  dpRunJobEndpoint('/api/emg/export_job', {
    folder: _currentFolder,
    subfolder: _currentSubfolder,
    channel: _currentChannel,
    path: currentPath(),
    peaks: _peaks,
    linked_channels: linkedChannels,
    half_ms: 100,
    mode: 'save'
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Saving grouped peaks${pct}${msg}`, 'loading');
    },
  })
    .then(d => {
      const segN = d.segment_count || 0;
      renderEmgExportOutputs(d);
      setStatus('status', 'Export complete: saved summary + ' + segN + ' segment file(s)', 'ok');
      recordRunHistory({
        view: 'emg_peaks',
        title: 'EMG Grouped Peaks Export',
        status: 'ok',
        project_root: document.getElementById('folderPath').value.trim(),
        input_files: [
          {path: currentPath(), role: 'source_channel_csv'},
          ...linkedChannels.map(channel => ({
            path: _currentFolder + '/' + _currentSubfolder + '/' + channel,
            role: 'linked_channel_csv',
          })),
        ],
        outputs: dpAsPathRecords(d.saved_paths || (d.summary_path ? [d.summary_path] : []), 'emg_peak_output'),
        parameters: {
          settings: collectEmgPeakSettings(),
          peaks: _peaks.map(p => ({...p})),
          linked_channels: linkedChannels,
        },
        metadata: {
          saved_folder: d.saved_path || '',
          segment_count: segN,
          linked_channel_count: d.linked_channel_count || 0,
        },
      });
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Export failed: ' + e.message, true);
    })
    .finally(() => btnBusy('btnExport', false, 'Export Grouped Peaks'));
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'exportGrouped',
  'renderEmgExportOutputs',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
