let _lastPeakSelectionIndex = null;

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

    const tVal = peakTime(peak).toFixed(3);
    const hVal = peakHeight(peak).toFixed(3);
    const dVal = peakDuration(peak).toFixed(2);
    const group = escHtml(peak.group || '-');
    const btnLabel = removed ? 'Undo' : 'Remove';

    tr.innerHTML = '<td><input type="checkbox" ' + (_selected.has(idx) ? 'checked' : '') + '></td>' +
      '<td data-peak-select style="cursor:pointer;">' + idx + '</td>' +
      '<td data-peak-select style="cursor:pointer;">' + tVal + '</td>' +
      '<td data-peak-select style="cursor:pointer;">' + hVal + '</td>' +
      '<td data-peak-select style="cursor:pointer;">' + dVal + '</td>' +
      '<td>' + group + '</td>' +
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
  'removeSelectedPeaks',
  'resetAllRemovals',
  'restoreSelectedPeaks',
  'selectAllPeaks',
  'setGroupForSelected',
  'resetPeakSelectionAnchor',
  'togglePeakRemoved',
  'togglePeakSelection',
  'updatePeaksTable',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
