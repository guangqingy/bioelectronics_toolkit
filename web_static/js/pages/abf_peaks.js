let _currentFile = null;
let _peaks = [];
let _lastWindow = null;
let _lastMeta = null;
let _folderFiles = [];
let _latestFile = null;
let _folderRefreshTimer = null;

function n(v, d = 4) {
  const x = Number(v);
  return Number.isFinite(x) ? x.toFixed(d) : '-';
}

function activePeaks() {
  return _peaks.filter(p => !p._removed);
}

function togglePeakRemoved(i) {
  if (i < 0 || i >= _peaks.length) return;
  _peaks[i]._removed = !_peaks[i]._removed;
  updatePeaksTable();
}

function restoreAllPeaks() {
  if (!_peaks.length) {
    setStatus('status', 'No peaks to restore', 'error');
    return;
  }
  _peaks.forEach(p => { p._removed = false; });
  updatePeaksTable();
  setStatus('status', 'All peaks restored', 'ok');
}

function updateFolderRefreshInfo(added) {
  const el = document.getElementById('folderRefreshInfo');
  if (!el) return;
  el.textContent = DP.liveFolder.infoText(_folderFiles, added || [], _latestFile);
}

function activateFilePath(path) {
  document.querySelectorAll('#fileList .file-item').forEach(e => {
    e.classList.toggle('active', e.dataset.path === path);
  });
}

function selectFileByPath(path) {
  const el = DP.liveFolder.findFileItem('fileList', path);
  if (el) selectFile(el);
}

function openLatestFile() {
  if (!_latestFile) {
    setStatus('status', 'No ABF file available', 'error');
    return;
  }
  selectFileByPath(_latestFile.path);
}

function configureFolderAutoRefresh() {
  if (_folderRefreshTimer) {
    clearInterval(_folderRefreshTimer);
    _folderRefreshTimer = null;
  }
  const enabled = document.getElementById('folderAutoRefresh')?.checked;
  if (!enabled) {
    setStatus('status', 'Auto refresh off', 'ok');
    return;
  }
  const seconds = Number(document.getElementById('folderRefreshSeconds')?.value || 5);
  const intervalMs = Math.max(2, seconds) * 1000;
  _folderRefreshTimer = setInterval(() => {
    scanFolder({
      preserveSelection: true,
      selectNewestAdded: document.getElementById('openNewOnRefresh')?.checked,
      selectFirstIfEmpty: false,
      reloadCurrent: false,
      silent: true,
    });
  }, intervalMs);
  setStatus('status', 'Auto refresh on', 'ok');
}

function updatePeaksTable() {
  const card = document.getElementById('peaksCard');
  const wrap = document.getElementById('peaksTable');

  if (!_peaks.length) {
    card.style.display = 'none';
    wrap.innerHTML = '';
    return;
  }

  card.style.display = 'block';
  const rows = _peaks.map((p, i) => {
    const removed = !!p._removed;
    const rowStyle = removed ? ' style="opacity:0.45;text-decoration:line-through"' : '';
    const btnLabel = removed ? 'Undo' : 'Remove';
    return `<tr${rowStyle}>`
      + `<td>${i + 1}</td>`
      + `<td>${n(p.time, 6)}</td>`
      + `<td>${n(p.amplitude, 6)}</td>`
      + `<td>${n(p.prominence, 6)}</td>`
      + `<td><button class="btn-secondary" style="padding:2px 8px;min-height:24px" data-dp-click="togglePeakRemoved(${i})">${btnLabel}</button></td>`
      + `</tr>`;
  }).join('');

  wrap.innerHTML = `
    <table class="dp-table">
      <thead>
        <tr>
          <th>#</th><th>Time (s)</th><th>Amplitude</th><th>Prominence</th><th>Action</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <div style="padding:8px 2px 0;color:var(--silver);font-size:12px;">`
      + `${activePeaks().length} / ${_peaks.length} selected`
    + `</div>`;
}

async function scanFolder(options) {
  const opts = Object.assign({
    preserveSelection: false,
    selectLatest: false,
    selectNewestAdded: false,
    selectFirstIfEmpty: true,
    reloadCurrent: true,
    silent: false,
  }, options || {});
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) {
    setStatus('status', 'Enter folder path', 'error');
    toast('Enter folder path', true);
    return;
  }

  if (!opts.silent) setStatus('status', 'Scanning folder...', 'loading');
  try {
    const r = await api('/api/abf/browse', { folder });
    if (r.error) throw new Error(r.error);
    const previousFiles = _folderFiles.slice();
    const files = DP.liveFolder.normalizeFiles(r.files || []);
    const diff = DP.liveFolder.diffFiles(previousFiles, files);
    const fileList = document.getElementById('fileList');
    _folderFiles = files;
    _latestFile = DP.liveFolder.newestFile(files);
    updateFolderRefreshInfo(diff.added);

    if (files.length === 0) {
      _currentFile = null;
      fileList.innerHTML = '<div class="file-list-empty">No ABF files found</div>';
      setStatus('status', 'No files found', 'error');
      return;
    }

    fileList.innerHTML = files.map((f, i) => {
      return `<div class="file-item" data-idx="${i}" data-path="${dpEscapeHtml(f.path)}" data-mtime="${dpEscapeHtml(f.mtime || '')}" title="${dpEscapeHtml(f.path)}" data-dp-click="selectFile(this)">${dpEscapeHtml(f.name)}</div>`;
    }).join('');
    DP.liveFolder.markNewItems('fileList', diff.addedPaths);

    const currentStillAvailable = _currentFile && files.some(f => f.path === _currentFile);
    const newestAdded = DP.liveFolder.newestFile(diff.added);
    let targetPath = null;
    if (opts.selectLatest && _latestFile) targetPath = _latestFile.path;
    else if (opts.selectNewestAdded && newestAdded) targetPath = newestAdded.path;
    else if (opts.preserveSelection && currentStillAvailable) targetPath = _currentFile;
    else if (!opts.preserveSelection && _latestFile) targetPath = _latestFile.path;
    else if ((!_currentFile || !currentStillAvailable) && opts.selectFirstIfEmpty !== false && _latestFile) targetPath = _latestFile.path;

    if (targetPath) {
      if (targetPath === _currentFile && opts.reloadCurrent === false) {
        activateFilePath(targetPath);
        if (!opts.silent) setStatus('status', 'Ready', 'ok');
      } else {
        selectFileByPath(targetPath);
      }
    } else if (!opts.silent) {
      setStatus('status', 'Ready', 'ok');
    }
  } catch (e) {
    setStatus('status', 'Scan failed', 'error');
    toast('Scan failed: ' + e.message, true);
  }
}

function selectFile(el) {
  document.querySelectorAll('#fileList .file-item').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  _currentFile = el.dataset.path;
  _peaks = [];
  _lastWindow = null;
  _lastMeta = null;
  updatePeaksTable();
  setStatus('status', 'File selected', 'ok');
}

function detect() {
  if (!_currentFile) {
    toast('Select a file first', true);
    return;
  }

  const channel = parseInt(document.getElementById('channel').value, 10);
  const i_ch = parseInt(document.getElementById('iCh').value, 10);
  const v_ch = parseInt(document.getElementById('vCh').value, 10);
  const r_norm = document.getElementById('rNorm').checked;
  const t0 = parseFloat(document.getElementById('t0').value) || 0;
  const t1 = document.getElementById('t1').value ? parseFloat(document.getElementById('t1').value) : null;
  const use_all = document.getElementById('useAll').checked;
  const polarity = document.getElementById('polarity').value;
  const heightTxt = document.getElementById('pkHeight').value.trim();
  const promTxt = document.getElementById('pkProm').value.trim();
  const height = heightTxt === '' ? null : parseFloat(heightTxt);
  const prominence = promTxt === '' ? null : parseFloat(promTxt);
    const distance = parseInt(document.getElementById('pkDist').value, 10) || 2;

  btnBusy('btnDetect', true, 'Detecting...');
  setStatus('status', 'Running peak detection...', 'loading');

  api('/api/abf/detect', {
    path: _currentFile,
    channel, i_ch, v_ch, r_norm,
    t0, t1, use_all,
    polarity, height, prominence, distance
  })
    .then(r => {
      if (r.error) throw new Error(r.error);
      if (r.img) {
        setPlot('plotArea', r.img);
      }

      _peaks = (r.peaks || []).map(p => Object.assign({}, p, { _removed: false }));
      _lastWindow = r.window || null;
      _lastMeta = r.meta || null;
      updatePeaksTable();

      setStatus('status', 'Detection complete: ' + _peaks.length + ' peak(s)', 'ok');
      toast('Peak detection complete');
    })
    .catch(e => {
      setStatus('status', 'Detection failed', 'error');
      toast('Detection failed: ' + e.message, true);
    })
    .finally(() => btnBusy('btnDetect', false, 'Detect Peaks'));
}

async function exportPNG() {
  if (!_currentFile) {
    toast('No file selected', true);
    return;
  }
  setStatus('status', 'Saving PNG...', 'loading');
  try {
    const use_all = document.getElementById('useAll').checked;
    const t0 = parseFloat(document.getElementById('t0').value);
    const t1 = document.getElementById('t1').value ? parseFloat(document.getElementById('t1').value) : null;
    const d = await dpRunJobEndpoint('/api/abf/export_job', {
      path: _currentFile,
      fmt: 'png',
      sweep: 0,
      channel: parseInt(document.getElementById('channel').value, 10) || 0,
      i_ch: parseInt(document.getElementById('iCh').value, 10) || 0,
      v_ch: parseInt(document.getElementById('vCh').value, 10) || 1,
      r_norm: document.getElementById('rNorm').checked,
      x_min: use_all ? null : t0,
      x_max: use_all ? null : t1,
      mode: 'save'
    }, {
      interval_ms: 1000,
      on_update: job => {
        const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
        const msg = job.message ? ` · ${job.message}` : '';
        setStatus('status', `Saving PNG${pct}${msg}`, 'loading');
      },
    });
    if (d.error) throw new Error(d.error);
    setStatus('status', 'Saved: ' + (d.saved_path || 'ok'), 'ok');
  } catch (e) {
    setStatus('status', 'Export failed', 'error');
    toast('Export failed: ' + e.message, true);
  }
}

async function exportSVG() {
  if (!_currentFile) {
    toast('No file selected', true);
    return;
  }
  setStatus('status', 'Saving SVG...', 'loading');
  try {
    const use_all = document.getElementById('useAll').checked;
    const t0 = parseFloat(document.getElementById('t0').value);
    const t1 = document.getElementById('t1').value ? parseFloat(document.getElementById('t1').value) : null;
    const d = await dpRunJobEndpoint('/api/abf/export_job', {
      path: _currentFile,
      fmt: 'svg',
      sweep: 0,
      channel: parseInt(document.getElementById('channel').value, 10) || 0,
      i_ch: parseInt(document.getElementById('iCh').value, 10) || 0,
      v_ch: parseInt(document.getElementById('vCh').value, 10) || 1,
      r_norm: document.getElementById('rNorm').checked,
      x_min: use_all ? null : t0,
      x_max: use_all ? null : t1,
      signal_only: true,
      mode: 'save'
    }, {
      interval_ms: 1000,
      on_update: job => {
        const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
        const msg = job.message ? ` · ${job.message}` : '';
        setStatus('status', `Saving SVG${pct}${msg}`, 'loading');
      },
    });
    if (d.error) throw new Error(d.error);
    setStatus('status', 'Saved: ' + (d.saved_path || 'ok'), 'ok');
  } catch (e) {
    setStatus('status', 'Export failed', 'error');
    toast('Export failed: ' + e.message, true);
  }
}

async function exportCSV() {
  if (!_currentFile) {
    toast('No file selected', true);
    return;
  }
  const peaks = activePeaks();
  if (!peaks.length) {
    toast('No selected peaks to export', true);
    return;
  }
  setStatus('status', 'Saving CSV...', 'loading');
  try {
    const payload = {
      path: _currentFile,
      mode: 'save',
      sweep: 0,
      channel: parseInt(document.getElementById('channel').value, 10) || 0,
      i_ch: parseInt(document.getElementById('iCh').value, 10) || 0,
      v_ch: parseInt(document.getElementById('vCh').value, 10) || 1,
      r_norm: document.getElementById('rNorm').checked,
      polarity: (document.getElementById('polarity').value || 'positive').toUpperCase(),
      export_window_ms: parseFloat(document.getElementById('exportWinMs').value) || 50,
      window: _lastWindow,
      peaks
    };

    const d = await dpRunJobEndpoint('/api/abf/export_peaks_job', payload, {
      interval_ms: 1000,
      on_update: job => {
        const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
        const msg = job.message ? ` · ${job.message}` : '';
        setStatus('status', `Saving CSV${pct}${msg}`, 'loading');
      },
    });
    setStatus('status', 'Saved summary + ' + (d.saved_count || 0) + ' segments: ' + (d.saved_path || 'ok'), 'ok');
  } catch (e) {
    setStatus('status', 'Export failed', 'error');
    toast('Export failed: ' + e.message, true);
  }
}

window.addEventListener('load', () => {
  setStatus('status', 'Ready', 'ok');
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'activePeaks',
  'configureFolderAutoRefresh',
  'detect',
  'exportCSV',
  'exportPNG',
  'exportSVG',
  'n',
  'openLatestFile',
  'restoreAllPeaks',
  'scanFolder',
  'selectFile',
  'togglePeakRemoved',
  'updatePeaksTable',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
