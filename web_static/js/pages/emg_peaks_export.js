function exportGrouped() {
  if (_peaks.length === 0) {
    setStatus('status', 'No peaks to export', 'error');
    return;
  }

  btnBusy('btnExport', true, 'Exporting...');
  setStatus('status', 'Saving grouped peaks...', 'loading');

  dpRunJobEndpoint('/api/emg/export_job', {
    folder: _currentFolder,
    subfolder: _currentSubfolder,
    channel: _currentChannel,
    path: currentPath(),
    peaks: _peaks,
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
      const summary = d.summary_path ? (' | summary: ' + d.summary_path) : '';
      setStatus('status', 'Saved summary + ' + segN + ' segment file(s): ' + (d.saved_path || 'ok') + summary, 'ok');
      recordRunHistory({
        view: 'emg_peaks',
        title: 'EMG Grouped Peaks Export',
        status: 'ok',
        project_root: document.getElementById('folderPath').value.trim(),
        input_files: [{path: currentPath(), role: 'source_channel_csv'}],
        outputs: dpAsPathRecords(d.saved_paths || (d.summary_path ? [d.summary_path] : []), 'emg_peak_output'),
        parameters: {
          settings: collectEmgPeakSettings(),
          peaks: _peaks.map(p => ({...p})),
        },
        metadata: {
          saved_folder: d.saved_path || '',
          segment_count: segN,
        },
      });
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Export failed: ' + e.message, true);
    })
    .finally(() => btnBusy('btnExport', false, 'Export Grouped Peaks'));
}
