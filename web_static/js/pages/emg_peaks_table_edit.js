let _lastPeakSelectionIndex = null;

function formatPeakTableNumber(value, digits) {
  return Number.isFinite(value) ? value.toFixed(digits) : '--';
}

function isBaselinePeak(peak) {
  return !!(peak && (peak.baseline || String(peak.source_kind || '').indexOf('baseline') === 0));
}

function updatePeaksTable() {
  const tbody = document.querySelector('#peaksTable tbody');
  tbody.innerHTML = '';
  if (_lastPeakSelectionIndex !== null && (_lastPeakSelectionIndex < 0 || _lastPeakSelectionIndex >= _peaks.length)) {
    _lastPeakSelectionIndex = null;
  }

  _peaks.forEach((peak, idx) => {
    const tr = document.createElement('tr');
    const removed = !!peak.removed;
    if (removed) {
      tr.style.opacity = '0.45';
      tr.style.textDecoration = 'line-through';
    }
    if (_selected.has(idx)) {
      tr.style.backgroundColor = 'var(--cloud)';
    }

    const tVal = formatPeakTableNumber(peakTime(peak), 3);
    const hVal = formatPeakTableNumber(peakHeight(peak), 3);
    const dVal = formatPeakTableNumber(peakDuration(peak), 2);
    const group = escHtml(peak.group || '-');
    const kind = isBaselinePeak(peak) ? 'Baseline' : 'Peak';
    const btnLabel = removed ? 'Undo' : 'Remove';

    tr.innerHTML = '<td><input type="checkbox" ' + (_selected.has(idx) ? 'checked' : '') + '></td>' +
      '<td data-peak-select style="cursor:pointer;">' + idx + '</td>' +
      '<td data-peak-select style="cursor:pointer;">' + tVal + '</td>' +
      '<td data-peak-select style="cursor:pointer;">' + hVal + '</td>' +
      '<td data-peak-select style="cursor:pointer;">' + dVal + '</td>' +
      '<td>' + group + '</td>' +
      '<td>' + kind + '</td>' +
      '<td><button class="btn-icon">' + btnLabel + '</button></td>';

    const checkbox = tr.querySelector('input[type="checkbox"]');
    checkbox.addEventListener('click', ev => {
      ev.preventDefault();
      togglePeakSelection(idx, ev);
    });
    tr.querySelectorAll('[data-peak-select]').forEach(cell => {
      cell.addEventListener('click', ev => togglePeakSelection(idx, ev));
    });
    tr.querySelector('button').addEventListener('click', () => togglePeakRemoved(idx));
    tbody.appendChild(tr);
  });
}

function togglePeakSelection(idx, event) {
  const useRange = !!(
    event &&
    event.shiftKey &&
    _lastPeakSelectionIndex !== null &&
    _lastPeakSelectionIndex >= 0 &&
    _lastPeakSelectionIndex < _peaks.length
  );
  if (useRange) {
    const start = Math.max(0, Math.min(_lastPeakSelectionIndex, idx));
    const end = Math.min(_peaks.length - 1, Math.max(_lastPeakSelectionIndex, idx));
    for (let i = start; i <= end; i += 1) _selected.add(i);
  } else if (_selected.has(idx)) {
    _selected.delete(idx);
  } else {
    _selected.add(idx);
  }
  _lastPeakSelectionIndex = idx;
  updatePeaksTable();
}

function resetPeakSelectionAnchor() {
  _lastPeakSelectionIndex = null;
}

function togglePeakRemoved(idx) {
  if (!_peaks[idx]) return;
  _peaks[idx].removed = !_peaks[idx].removed;
  updatePeaksTable();
}

function setGroupForSelected() {
  const groupVal = document.getElementById('groupInput').value.trim();
  if (!groupVal) {
    setStatus('status', 'Enter a group value', 'error');
    return;
  }
  if (_selected.size === 0) {
    setStatus('status', 'Select at least one peak row', 'error');
    return;
  }

  const nSelected = _selected.size;
  _selected.forEach(idx => {
    if (_peaks[idx] && !_peaks[idx].removed) {
      _peaks[idx].group = groupVal;
    }
  });
  _selected.clear();
  updatePeaksTable();
  setStatus('status', 'Group set for ' + nSelected + ' peaks', 'ok');
}

function autoGroupByTime() {
  if (_peaks.length === 0) {
    setStatus('status', 'No peaks to group', 'error');
    return;
  }

  const periodHz = parseFloat(document.getElementById('grpPeriod').value);
  const gapFac = parseFloat(document.getElementById('grpGapFac').value);
  const startId = parseInt(document.getElementById('grpStart').value, 10) || 0;

  const active = _peaks
    .map((p, idx) => ({ p, idx }))
    .filter(x => !x.p.removed)
    .sort((a, b) => peakTime(a.p) - peakTime(b.p));

  if (active.length === 0) {
    setStatus('status', 'All peaks are removed', 'error');
    return;
  }

  let gid = startId;
  if (!(periodHz > 0)) {
    active.forEach(x => {
      x.p.group = String(startId);
    });
  } else {
    const thr = gapFac * (1.0 / periodHz);
    active[0].p.group = String(gid);
    for (let i = 1; i < active.length; i++) {
      const dt = peakTime(active[i].p) - peakTime(active[i - 1].p);
      if (dt > thr) gid += 1;
      active[i].p.group = String(gid);
    }
  }

  updatePeaksTable();
  setStatus('status', 'Auto grouped ' + active.length + ' peaks', 'ok');
}

function usePreviewWindowForBaseline() {
  const x0 = parseFloat(document.getElementById('xMin').value);
  const x1Raw = document.getElementById('xMax').value;
  const x1 = x1Raw ? parseFloat(x1Raw) : NaN;
  if (!Number.isFinite(x0) || !Number.isFinite(x1) || x1 <= x0) {
    setStatus('status', 'Set a valid preview window first', 'error');
    return;
  }
  document.getElementById('baselineStart').value = formatEmgNumber(x0, 6);
  document.getElementById('baselineEnd').value = formatEmgNumber(x1, 6);
  setStatus('status', 'Baseline window copied', 'ok');
}

function baselineBoundsFromControls() {
  let t0 = parseFloat(document.getElementById('baselineStart').value);
  let t1 = parseFloat(document.getElementById('baselineEnd').value);
  if (!Number.isFinite(t0) || !Number.isFinite(t1) || t0 === t1) return null;
  if (t1 < t0) [t0, t1] = [t1, t0];
  return { t0, t1 };
}

function targetGroupLabelsFromControls() {
  const total = parseInt(document.getElementById('grpTargetCount').value, 10);
  const startId = parseInt(document.getElementById('grpStart').value, 10) || 0;
  if (!(total > 0)) return [];
  return Array.from({ length: total }, (_, offset) => String(startId + offset));
}

function baselineRepsPerGroupFromControls() {
  const reps = parseInt(document.getElementById('baselinePerGroup').value, 10);
  return reps > 0 ? reps : 1;
}

function baselineSeedFromControls() {
  const input = document.getElementById('baselineSeed');
  const raw = parseInt(input.value, 10);
  if (Number.isFinite(raw)) return raw >>> 0;
  const generated = (Date.now() ^ Math.floor(Math.random() * 0xffffffff)) >>> 0;
  input.value = String(generated);
  return generated;
}

function baselineDurationFromExistingPeaks() {
  const durations = _peaks
    .filter(peak => !isBaselinePeak(peak) && !peak.removed)
    .map(peakDuration)
    .filter(value => Number.isFinite(value) && value > 0)
    .sort((a, b) => a - b);
  if (!durations.length) return 2.0;
  const mid = Math.floor(durations.length / 2);
  return durations.length % 2
    ? durations[mid]
    : (durations[mid - 1] + durations[mid]) / 2;
}

function seededBaselineRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(1664525, state) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

function shuffledBaselineSlots(count, seed) {
  const rand = seededBaselineRandom(seed);
  const slots = Array.from({ length: count }, (_, idx) => idx);
  for (let i = slots.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rand() * (i + 1));
    [slots[i], slots[j]] = [slots[j], slots[i]];
  }
  return slots;
}

function baselineRandomCenters(bounds, count, seed, halfS) {
  const widthS = halfS * 2;
  const durationS = bounds.t1 - bounds.t0;
  const maxSlots = Math.floor((durationS + 1e-12) / widthS);
  if (count > maxSlots) {
    return { error: `Baseline window can fit ${maxSlots} non-overlapping ${Math.round(widthS * 1000)} ms period(s); requested ${count}` };
  }
  const rand = seededBaselineRandom(seed ^ 0xa5a5a5a5);
  const slots = shuffledBaselineSlots(maxSlots, seed)
    .slice(0, count)
    .sort((a, b) => a - b);
  const slotWidth = durationS / maxSlots;
  const centers = slots.map(slot => {
    const lo = bounds.t0 + slot * slotWidth + halfS;
    const hi = bounds.t0 + (slot + 1) * slotWidth - halfS;
    const center = hi > lo ? lo + rand() * (hi - lo) : (lo + hi) / 2;
    return Math.min(bounds.t1 - halfS, Math.max(bounds.t0 + halfS, center));
  });
  return { centers };
}

function existingActiveGroupLabels() {
  const labels = new Set();
  _peaks.forEach(peak => {
    if (peak.removed) return;
    if (isBaselinePeak(peak)) return;
    const group = String(peak.group || '').trim();
    if (group) labels.add(group);
  });
  return labels;
}

function fillMissingGroupsWithBaseline() {
  const bounds = baselineBoundsFromControls();
  if (!bounds) {
    setStatus('status', 'Enter a valid baseline window', 'error');
    return;
  }

  const targetLabels = targetGroupLabelsFromControls();
  if (!targetLabels.length) {
    setStatus('status', 'Enter a valid total group count', 'error');
    return;
  }
  const targetSet = new Set(targetLabels);
  _peaks = _peaks.filter(peak => !(isBaselinePeak(peak) && targetSet.has(String(peak.group || '').trim())));

  const existing = existingActiveGroupLabels();
  const missing = targetLabels.filter(label => !existing.has(label));
  if (!missing.length) {
    setStatus('status', 'No missing groups to fill', 'ok');
    return;
  }

  const halfMs = typeof emgGroupedSegmentHalfMs === 'function' ? emgGroupedSegmentHalfMs() : 100;
  const halfS = halfMs / 1000;
  const durationMs = baselineDurationFromExistingPeaks();
  const repsPerGroup = baselineRepsPerGroupFromControls();
  const totalPeriods = missing.length * repsPerGroup;
  const seed = baselineSeedFromControls();
  const sampled = baselineRandomCenters(bounds, totalPeriods, seed, halfS);
  if (sampled.error) {
    setStatus('status', sampled.error, 'error');
    return;
  }

  const stamp = Date.now();
  sampled.centers.forEach((center, sampledIndex) => {
    const groupIndex = Math.floor(sampledIndex / repsPerGroup);
    const repIndex = sampledIndex % repsPerGroup;
    const group = missing[groupIndex];
    _peaks.push(normalizePeak({
      peak_idx: -1,
      time_s: center,
      height: null,
      duration_ms: durationMs,
      group,
      baseline: true,
      source_kind: 'baseline',
      segment_start_s: center - halfS,
      segment_end_s: center + halfS,
      baseline_source_start_s: bounds.t0,
      baseline_source_end_s: bounds.t1,
      baseline_fill_seed: seed,
      baseline_rep: repIndex + 1,
      baseline_fill_id: `${stamp}_${sampledIndex}`,
    }));
  });
  _peaks.sort((a, b) => {
    const ga = Number(a.group);
    const gb = Number(b.group);
    if (Number.isFinite(ga) && Number.isFinite(gb) && ga !== gb) return ga - gb;
    return peakTime(a) - peakTime(b);
  });
  _selected.clear();
  if (typeof resetPeakSelectionAnchor === 'function') resetPeakSelectionAnchor();
  updatePeaksTable();
  setStatus('status', 'Filled ' + missing.length + ' group(s) with ' + totalPeriods + ' baseline period(s)', 'ok');
}

function removeBaselineFillRows() {
  const before = _peaks.length;
  _peaks = _peaks.filter(peak => !isBaselinePeak(peak));
  _selected.clear();
  if (typeof resetPeakSelectionAnchor === 'function') resetPeakSelectionAnchor();
  updatePeaksTable();
  setStatus('status', 'Removed ' + (before - _peaks.length) + ' baseline fill row(s)', 'ok');
}

function selectAllPeaks() {
  _selected = new Set(_peaks.map((_, idx) => idx));
  _lastPeakSelectionIndex = _peaks.length ? 0 : null;
  updatePeaksTable();
}

function clearPeakSelection() {
  _selected.clear();
  _lastPeakSelectionIndex = null;
  updatePeaksTable();
}

function removeSelectedPeaks() {
  if (_selected.size === 0) {
    setStatus('status', 'Select peaks first', 'error');
    return;
  }
  _selected.forEach(idx => {
    if (_peaks[idx]) _peaks[idx].removed = true;
  });
  updatePeaksTable();
  setStatus('status', 'Removed selected peaks', 'ok');
}

function restoreSelectedPeaks() {
  if (_selected.size === 0) {
    setStatus('status', 'Select peaks first', 'error');
    return;
  }
  _selected.forEach(idx => {
    if (_peaks[idx]) _peaks[idx].removed = false;
  });
  updatePeaksTable();
  setStatus('status', 'Restored selected peaks', 'ok');
}

function resetAllRemovals() {
  _peaks.forEach(p => {
    p.removed = false;
  });
  updatePeaksTable();
  setStatus('status', 'All removals reset', 'ok');
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'autoGroupByTime',
  'clearPeakSelection',
  'baselineRandomCenters',
  'fillMissingGroupsWithBaseline',
  'isBaselinePeak',
  'removeSelectedPeaks',
  'removeBaselineFillRows',
  'resetAllRemovals',
  'restoreSelectedPeaks',
  'selectAllPeaks',
  'setGroupForSelected',
  'resetPeakSelectionAnchor',
  'togglePeakRemoved',
  'togglePeakSelection',
  'updatePeaksTable',
  'usePreviewWindowForBaseline',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
