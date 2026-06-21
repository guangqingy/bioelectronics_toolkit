function detectPeaks() {
  const path = currentPath();
  if (!path) {
    setStatus('status', 'Select folder, subfolder, and channel first', 'error');
    toast('Select folder, subfolder, and channel first', true);
    return;
  }

  const pk_height = parseFloat(document.getElementById('pkHeight').value) || 200;
  const pk_prom = parseFloat(document.getElementById('pkProm').value) || 100;
  const pk_dist = parseInt(document.getElementById('pkDist').value, 10) || 400;
  const pk_dur = parseInt(document.getElementById('pkDur').value, 10) || 200;
  const pk_minw = parseFloat(document.getElementById('pkMinW').value);
  const pk_wlen = document.getElementById('pkWlen').value.trim();
  const polarity = document.getElementById('pkPolarity').value;
  const adaptive_sigma = document.getElementById('pkAdaptive').checked;
  const sigma_prom = parseFloat(document.getElementById('pkSigmaProm').value);
  const sigma_height = parseFloat(document.getElementById('pkSigmaHeight').value);
  const detectMode = document.getElementById('detectMode').value;
  const x_min = document.getElementById('xMin').value ? parseFloat(document.getElementById('xMin').value) : null;
  const x_max = document.getElementById('xMax').value ? parseFloat(document.getElementById('xMax').value) : null;

  btnBusy('btnDetect', true, 'Detecting...');
  setStatus('status', 'Detecting peaks...', 'loading');
  api('/api/emg/detect', {
    path,
    pk_height,
    pk_prom,
    pk_dist,
    pk_dur,
    pk_minw: Number.isFinite(pk_minw) ? pk_minw : null,
    pk_wlen: pk_wlen === '' ? null : Number(pk_wlen),
    polarity,
    invert_signal: typeof isEmgSignalInverted === 'function' ? isEmgSignalInverted() : false,
    adaptive_sigma,
    sigma_prom: Number.isFinite(sigma_prom) ? sigma_prom : 1,
    sigma_height: Number.isFinite(sigma_height) ? sigma_height : 1,
    x_min,
    x_max
  })
    .then(data => {
      if (data.error) throw new Error(data.error);
      const detected = (data.peaks || []).map(normalizePeak);
      if (detectMode === 'append') {
        mergeDetectedPeaks(detected);
      } else {
        _peaks = detected.sort((a, b) => peakTime(a) - peakTime(b));
      }
      _selected.clear();
      if (typeof resetPeakSelectionAnchor === 'function') resetPeakSelectionAnchor();
      updatePeaksTable();
      showProcessedPeakPlot(data.img, `Processed peak detection preview: ${_peaks.length} peak(s)`);
      setStatus('status', 'Detected ' + detected.length + ' peaks (' + detectMode + ')', 'ok');
      toast('Peak detection complete');
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Detect failed: ' + e.message, true);
    })
    .finally(() => btnBusy('btnDetect', false, 'Detect Peaks'));
}

function mergeDetectedPeaks(newPeaks) {
  const merged = new Map();
  _peaks.forEach(p => merged.set(peakKey(p), normalizePeak(p)));
  newPeaks.forEach(p => {
    const np = normalizePeak(p);
    const key = peakKey(np);
    const prev = merged.get(key);
    if (prev && prev.group) np.group = prev.group;
    merged.set(key, np);
  });
  _peaks = Array.from(merged.values()).sort((a, b) => peakTime(a) - peakTime(b));
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'detectPeaks',
  'mergeDetectedPeaks',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
