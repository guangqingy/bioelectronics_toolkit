let _currentFolder = null;
let _currentSubfolder = null;
let _currentChannel = null;
let _peaks = [];
let _selected = new Set();

window.dpCurrentFilePath = () => currentPath() || '';
window.dpCurrentProjectRoot = () => document.getElementById('folderPath').value.trim();
window.dpCollectFileProfilePayload = () => ({
  peaks: _peaks.map(p => ({...p})),
  selected: Array.from(_selected),
});
window.dpApplyFileProfilePayload = payload => {
  if (!payload || typeof payload !== 'object') return;
  if (Array.isArray(payload.peaks)) _peaks = payload.peaks.map(normalizePeak);
  _selected = new Set(Array.isArray(payload.selected) ? payload.selected : []);
  if (typeof resetPeakSelectionAnchor === 'function') resetPeakSelectionAnchor();
  updatePeaksTable();
};
window.dpAfterFileProfileApplied = () => {
  updatePeaksTable();
  plot();
};
window.dpApplyRunManifest = manifest => {
  dpApplyRunManifestFallback(manifest);
  const inputPath = dpFirstManifestInput(manifest);
  if (inputPath) {
    const dir = dpPathDir(inputPath);
    _currentFolder = dpPathDir(dir);
    _currentSubfolder = dir.split('/').pop() || '';
    _currentChannel = inputPath.split('/').pop() || '';
    document.getElementById('folderPath').value = manifest.project_root || _currentFolder;
  }
  const params = manifest.parameters || {};
  if (params.settings) dpApplyObjectToControls(params.settings);
  if (Array.isArray(params.peaks)) _peaks = params.peaks.map(normalizePeak);
  _selected.clear();
  if (typeof resetPeakSelectionAnchor === 'function') resetPeakSelectionAnchor();
  updatePeaksTable();
  plot();
};

function currentPath() {
  if (!_currentFolder || !_currentSubfolder || !_currentChannel) return null;
  return _currentFolder + '/' + _currentSubfolder + '/' + _currentChannel;
}

function normalizePeak(p) {
  const out = Object.assign({}, p || {});
  if (out.peak_idx === undefined || out.peak_idx === null) {
    out.peak_idx = (out.idx !== undefined && out.idx !== null) ? Number(out.idx) : -1;
  }
  out.source_kind = String(out.source_kind || (out.baseline ? 'baseline' : 'peak'));
  out.baseline = !!out.baseline || out.source_kind.indexOf('baseline') === 0;
  out.group = String(out.group || '');
  out.removed = !!out.removed;
  return out;
}

function peakKey(peak) {
  const idx = peak && peak.peak_idx !== undefined && peak.peak_idx !== null ? Number(peak.peak_idx) : NaN;
  if (Number.isFinite(idx) && idx >= 0) {
    return 'idx:' + idx;
  }
  return [
    't:' + peakTime(peak).toFixed(6),
    'g:' + String(peak?.group || ''),
    'k:' + String(peak?.source_kind || ''),
  ].join('|');
}

function peakTime(peak) {
  if (peak.time !== undefined) return Number(peak.time);
  if (peak.time_s !== undefined) return Number(peak.time_s);
  return 0;
}

function peakHeight(peak) {
  if (peak.height !== undefined) return Number(peak.height);
  return 0;
}

function peakDuration(peak) {
  if (peak.duration !== undefined) return Number(peak.duration);
  if (peak.duration_ms !== undefined) return Number(peak.duration_ms);
  return 0;
}

function collectEmgPeakSettings() {
  const val = id => document.getElementById(id)?.value ?? '';
  const checked = id => !!document.getElementById(id)?.checked;
  return {
    polarity: val('pkPolarity'),
    height: val('pkHeight'),
    prominence: val('pkProm'),
    distance_ms: val('pkDist'),
    max_duration_ms: val('pkDur'),
    min_width_ms: val('pkMinW'),
    wlen_ms: val('pkWlen'),
    adaptive_sigma: checked('pkAdaptive'),
    sigma_prom: val('pkSigmaProm'),
    sigma_height: val('pkSigmaHeight'),
    detect_mode: val('detectMode'),
    x_min: val('xMin'),
    x_max: val('xMax'),
    grouping_period_hz: val('grpPeriod'),
    grouping_gap_factor: val('grpGapFac'),
    grouping_start: val('grpStart'),
    grouping_target_count: val('grpTargetCount'),
    baseline_start_s: val('baselineStart'),
    baseline_end_s: val('baselineEnd'),
    linked_channels: typeof collectLinkedChannelNames === 'function' ? collectLinkedChannelNames() : [],
  };
}

window.addEventListener('load', () => {
  setStatus('status', 'Ready', 'ok');
  updatePeaksTable();
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'collectEmgPeakSettings',
  'currentPath',
  'normalizePeak',
  'peakDuration',
  'peakHeight',
  'peakKey',
  'peakTime',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
