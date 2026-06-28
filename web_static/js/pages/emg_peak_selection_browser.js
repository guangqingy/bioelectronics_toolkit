let _emgPeakSelectionPlotDrag = null;
let _emgPeakSelectionTraceDuration = null;
let _availableChannels = [];
let _linkedChannels = new Set();
let _processedPeakPlotSeq = 0;
let _processedPeakOverlayCleanup = null;

function formatEmgNumber(value, digits) {
  if (!Number.isFinite(value)) return '--';
  const abs = Math.abs(value);
  if (abs >= 1000 || (abs > 0 && abs < 0.001)) return value.toExponential(3);
  return value.toFixed(digits == null ? 4 : digits).replace(/\.?0+$/, '');
}

function updateEmgPeakSelectionPlotReadouts(x, y) {
  const coord = document.getElementById('plotCoordReadout');
  if (coord) coord.textContent = `Cursor: x ${formatEmgNumber(x, 5)}, y ${formatEmgNumber(y, 3)}`;
  updateEmgPeakSelectionWindowReadout();
}

function updateEmgPeakSelectionWindowReadout() {
  const readout = document.getElementById('plotWindowReadout');
  if (!readout) return;
  const xMin = document.getElementById('xMin')?.value || '';
  const xMax = document.getElementById('xMax')?.value || '';
  readout.textContent = `Window: ${xMin || '--'} to ${xMax || '--'} s`;
}

function clearProcessedPeakPlot() {
  _processedPeakPlotSeq += 1;
  if (_processedPeakOverlayCleanup) {
    try { _processedPeakOverlayCleanup(); } catch (_) {}
    _processedPeakOverlayCleanup = null;
  }
  if (window.dpDestroyTrace) window.dpDestroyTrace('processedPlotArea');
  const card = document.getElementById('processedPlotCard');
  if (card) card.hidden = true;
  setPlot('processedPlotArea', null);
}

function processedPeakPlotPayload() {
  const path = currentPath();
  if (!path) return null;
  return {
    path,
    x_min: document.getElementById('xMin')?.value ? parseFloat(document.getElementById('xMin').value) : null,
    x_max: document.getElementById('xMax')?.value ? parseFloat(document.getElementById('xMax').value) : null,
    invert_signal: typeof isEmgSignalInverted === 'function' ? isEmgSignalInverted() : false,
  };
}

function peakMarkerColor(peak, selected) {
  if (peak?.removed) return '#8b9098';
  if (selected) return '#3E6AE1';
  if (typeof isBaselinePeak === 'function' && isBaselinePeak(peak)) return '#8b5cf6';
  return '#e06c00';
}

function refreshProcessedPeakOverlay() {
  if (!window.dpGetTrace) return;
  const chart = window.dpGetTrace('processedPlotArea');
  const over = chart && chart.root ? chart.root.querySelector('.u-over') : null;
  if (!chart || !over) return;
  over.querySelectorAll('.emg-peak-overlay').forEach(node => node.remove());
  if (!_peaks.length) return;

  const overlay = document.createElement('div');
  overlay.className = 'emg-peak-overlay';
  const xScale = chart.scales?.x || {};
  const yScale = chart.scales?.y || {};
  const xMin = Number(xScale.min);
  const xMax = Number(xScale.max);
  const yMin = Number(yScale.min);
  const yMax = Number(yScale.max);
  const overWidth = over.clientWidth || 1;
  const overHeight = over.clientHeight || 1;

  _peaks.forEach((peak, idx) => {
    const x = typeof peakTime === 'function' ? peakTime(peak) : Number(peak?.time_s || peak?.time || NaN);
    const y = typeof peakHeight === 'function' ? peakHeight(peak) : Number(peak?.height_uV || peak?.height || NaN);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    if (Number.isFinite(xMin) && x < xMin) return;
    if (Number.isFinite(xMax) && x > xMax) return;
    if (Number.isFinite(yMin) && y < Math.min(yMin, yMax)) return;
    if (Number.isFinite(yMax) && y > Math.max(yMin, yMax)) return;

    const left = chart.valToPos(x, 'x');
    const top = chart.valToPos(y, 'y');
    if (!Number.isFinite(left) || !Number.isFinite(top)) return;

    const selected = _selected && _selected.has(idx);
    const color = peakMarkerColor(peak, selected);
    const removed = !!peak?.removed;

    const line = document.createElement('div');
    line.className = 'emg-peak-marker-line' + (removed ? ' is-removed' : '');
    line.style.left = `${left}px`;
    line.style.height = `${overHeight}px`;
    line.style.borderLeftColor = color;
    overlay.appendChild(line);

    const dot = document.createElement('div');
    dot.className = 'emg-peak-marker-dot' + (removed ? ' is-removed' : '');
    dot.style.left = `${left - 4}px`;
    dot.style.top = `${top - 4}px`;
    dot.style.backgroundColor = color;
    overlay.appendChild(dot);

    const labelText = String(idx);
    const labelWidth = Math.min(56, Math.max(20, labelText.length * 7 + 10));
    const label = document.createElement('div');
    const labelLeft = Math.min(Math.max(0, left + 5), Math.max(0, overWidth - labelWidth));
    const labelTop = Math.min(Math.max(0, top - 18), Math.max(0, overHeight - 18));
    label.textContent = labelText;
    label.title = `Peak ${idx}${removed ? ' removed' : ''}`;
    label.className = [
      'emg-peak-marker-label',
      removed ? 'is-removed' : '',
      selected ? 'is-selected' : '',
    ].filter(Boolean).join(' ');
    label.style.left = `${labelLeft}px`;
    label.style.top = `${labelTop}px`;
    label.style.width = `${labelWidth}px`;
    label.style.backgroundColor = color;
    overlay.appendChild(label);
  });

  over.appendChild(overlay);
}

function bindProcessedPeakOverlayRefresh() {
  if (_processedPeakOverlayCleanup) {
    try { _processedPeakOverlayCleanup(); } catch (_) {}
  }
  const area = document.getElementById('processedPlotArea');
  let raf = null;
  const schedule = () => {
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      raf = null;
      refreshProcessedPeakOverlay();
    });
  };
  let observer = null;
  if (area && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(schedule);
    observer.observe(area);
  }
  window.addEventListener('resize', schedule);
  _processedPeakOverlayCleanup = () => {
    if (raf) cancelAnimationFrame(raf);
    if (observer) observer.disconnect();
    window.removeEventListener('resize', schedule);
  };
}

function showProcessedPeakPlot(img, label) {
  const card = document.getElementById('processedPlotCard');
  if (card) card.hidden = false;
  const payload = processedPeakPlotPayload();
  const area = document.getElementById('processedPlotArea');
  if (payload && window.dpUplotAvailable && window.dpUplotAvailable()) {
    const seq = ++_processedPeakPlotSeq;
    if (area) area.innerHTML = '<div class="plot-placeholder">Rendering processed preview...</div>';
    api('/api/emg/peak-selection/trace_data', payload)
      .then(data => {
        if (seq !== _processedPeakPlotSeq) return;
        if (data.error) throw new Error(data.error);
        const tracePayload = Object.assign({}, data, { title: label || data.title || 'Processed peak detection preview' });
        if (!window.dpRenderTrace('processedPlotArea', tracePayload, {
          dragZoom: false,
          cursorReadout: true,
          color: '#3E6AE1',
        })) throw new Error('uplot-render-failed');
        bindProcessedPeakOverlayRefresh();
        refreshProcessedPeakOverlay();
      })
      .catch(() => {
        if (seq !== _processedPeakPlotSeq) return;
        if (window.dpDestroyTrace) window.dpDestroyTrace('processedPlotArea');
        setPlot('processedPlotArea', img, 'png', label || 'Processed peak detection preview');
      });
    return;
  }
  if (window.dpDestroyTrace) window.dpDestroyTrace('processedPlotArea');
  setPlot('processedPlotArea', img, 'png', label || 'Processed peak detection preview');
}

function linkedSelectableChannels() {
  return (_availableChannels || []).filter(ch => ch && ch !== _currentChannel);
}

function renderLinkedChannelList() {
  const list = document.getElementById('linkedChannelList');
  if (!list) return;
  const channels = linkedSelectableChannels();
  if (!channels.length) {
    list.innerHTML = '<div class="file-list-empty">No linked channels</div>';
    return;
  }
  list.innerHTML = '';
  channels.forEach(channel => {
    const item = document.createElement('label');
    item.className = 'file-item checkbox-row';
    item.title = channel;
    const checked = _linkedChannels.has(channel) ? ' checked' : '';
    item.innerHTML = '<input class="dp-check" type="checkbox"' + checked + '><span>' + escHtml(channel) + '</span>';
    item.querySelector('input').addEventListener('change', ev => {
      if (ev.target.checked) _linkedChannels.add(channel);
      else _linkedChannels.delete(channel);
    });
    list.appendChild(item);
  });
}

function resetLinkedChannelsToAll() {
  _linkedChannels = new Set(linkedSelectableChannels());
  renderLinkedChannelList();
}

function setLinkedChannels(value) {
  _linkedChannels = value ? new Set(linkedSelectableChannels()) : new Set();
  renderLinkedChannelList();
}

function collectLinkedChannelNames() {
  const enabled = document.getElementById('linkedExportEnabled')?.checked;
  if (!enabled) return [];
  const selectable = new Set(linkedSelectableChannels());
  return Array.from(_linkedChannels).filter(channel => selectable.has(channel));
}

function resetPreviewWindow() {
  document.getElementById('xMin').value = 0;
  document.getElementById('xMax').value = _emgPeakSelectionTraceDuration || '';
  updateEmgPeakSelectionWindowReadout();
  plot();
}

function emgPeakSelectionValueFromPointer(plot, ev) {
  const over = plot && plot.root ? plot.root.querySelector('.u-over') : null;
  if (!over) return null;
  const rect = over.getBoundingClientRect();
  const left = ev.clientX - rect.left;
  const top = ev.clientY - rect.top;
  if (left < 0 || top < 0 || left > rect.width || top > rect.height) return null;
  return {
    left,
    x: plot.posToVal(left, 'x'),
    y: plot.posToVal(top, 'y')
  };
}

function installEmgPeakSelectionPlotInteractions() {
  if (!window.dpGetTrace) return;
  const chart = window.dpGetTrace('plotArea');
  const over = chart && chart.root ? chart.root.querySelector('.u-over') : null;
  if (!chart || !over || over._emgPeakInteractionsBound) return;

  over._emgPeakInteractionsBound = true;
  over.addEventListener('pointermove', ev => {
    const pos = emgPeakSelectionValueFromPointer(chart, ev);
    if (pos) updateEmgPeakSelectionPlotReadouts(pos.x, pos.y);
  });
  over.addEventListener('pointerdown', ev => {
    if (ev.button !== 0) return;
    const pos = emgPeakSelectionValueFromPointer(chart, ev);
    if (!pos) return;
    _emgPeakSelectionPlotDrag = { left: pos.left, x: pos.x };
  });
  over.addEventListener('pointerup', ev => {
    if (!_emgPeakSelectionPlotDrag) return;
    const start = _emgPeakSelectionPlotDrag;
    _emgPeakSelectionPlotDrag = null;
    const pos = emgPeakSelectionValueFromPointer(chart, ev);
    if (!pos || Math.abs(pos.left - start.left) < 6) return;

    const x0 = Math.min(start.x, pos.x);
    const x1 = Math.max(start.x, pos.x);
    if (!Number.isFinite(x0) || !Number.isFinite(x1) || x1 <= x0) return;
    document.getElementById('xMin').value = formatEmgNumber(x0, 6);
    document.getElementById('xMax').value = formatEmgNumber(x1, 6);
    updateEmgPeakSelectionWindowReadout();
    setStatus('status', `Window set: ${formatEmgNumber(x0, 5)} to ${formatEmgNumber(x1, 5)} s`, 'ok');
    plot();
  });
}

function browseMain() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) {
    setStatus('status', 'Please enter a folder path', 'error');
    toast('Please enter a folder path', true);
    return;
  }

  setStatus('status', 'Scanning folder...', 'loading');
  api('/api/emg/peak-selection/browse', { folder })
    .then(data => {
      if (data.error) throw new Error(data.error);

      _currentFolder = folder;
      _currentSubfolder = null;
      _currentChannel = null;
      _availableChannels = [];
      _linkedChannels.clear();
      _peaks = [];
      _selected.clear();
      if (typeof resetPeakSelectionAnchor === 'function') resetPeakSelectionAnchor();
      clearProcessedPeakPlot();
      updatePeaksTable();

      const list = document.getElementById('subfolderList');
      list.innerHTML = '';
      (data.subfolders || []).forEach(sf => {
        const item = document.createElement('div');
        item.className = 'file-item';
        item.textContent = sf;
        item.onclick = () => selectSubfolder(item, sf);
        list.appendChild(item);
      });

      if ((data.subfolders || []).length > 0) {
        selectSubfolder(list.children[0], data.subfolders[0]);
      } else {
        document.getElementById('channelList').innerHTML = '<div class="file-list-empty">No channels</div>';
        renderLinkedChannelList();
        setPlot('plotArea', null);
        clearProcessedPeakPlot();
      }

      setStatus('status', 'Ready', 'ok');
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Browse failed: ' + e.message, true);
    });
}

function selectSubfolder(el, subfolder) {
  document.querySelectorAll('#subfolderList .file-item').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  _currentSubfolder = subfolder;
  _currentChannel = null;
  _peaks = [];
  _selected.clear();
  if (typeof resetPeakSelectionAnchor === 'function') resetPeakSelectionAnchor();
  clearProcessedPeakPlot();
  updatePeaksTable();
  loadChannels();
}

function loadChannels() {
  if (!_currentFolder || !_currentSubfolder) return;

  setStatus('status', 'Loading channels...', 'loading');
  api('/api/emg/peak-selection/load_channels', {
    folder: _currentFolder,
    subfolder: _currentSubfolder
  })
    .then(data => {
      if (data.error) throw new Error(data.error);
      _availableChannels = data.channels || [];
      const list = document.getElementById('channelList');
      list.innerHTML = '';

      _availableChannels.forEach(ch => {
        const item = document.createElement('div');
        item.className = 'file-item';
        item.textContent = ch;
        item.onclick = () => selectChannel(item, ch);
        list.appendChild(item);
      });

      if (_availableChannels.length > 0) {
        selectChannel(list.children[0], data.channels[0]);
      } else {
        _linkedChannels.clear();
        renderLinkedChannelList();
        setPlot('plotArea', null);
        clearProcessedPeakPlot();
      }

      setStatus('status', 'Ready', 'ok');
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Load channels failed: ' + e.message, true);
    });
}

function selectChannel(el, channel) {
  document.querySelectorAll('#channelList .file-item').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  _currentChannel = channel;
  resetLinkedChannelsToAll();
  _peaks = [];
  _selected.clear();
  if (typeof resetPeakSelectionAnchor === 'function') resetPeakSelectionAnchor();
  clearProcessedPeakPlot();
  updatePeaksTable();
  loadChannelData();
}

function loadChannelData() {
  if (!_currentFolder || !_currentSubfolder || !_currentChannel) return;

  setStatus('status', 'Loading trace...', 'loading');
  api('/api/emg/peak-selection/load', {
    folder: _currentFolder,
    subfolder: _currentSubfolder,
    channel: _currentChannel
  })
    .then(data => {
      if (data.error) throw new Error(data.error);
      _emgPeakSelectionTraceDuration = data.duration || null;
      document.getElementById('xMax').value = data.duration || 10;
      updateEmgPeakSelectionWindowReadout();
      loadGenericFileProfileForCurrent(true).finally(() => plot());
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Load trace failed: ' + e.message, true);
    });
}

function plot() {
  const path = currentPath();
  if (!path) return;

  const x_min = parseFloat(document.getElementById('xMin').value) || 0;
  const x_max = document.getElementById('xMax').value ? parseFloat(document.getElementById('xMax').value) : null;
  const payload = {
    path,
    x_min,
    x_max,
    invert_signal: typeof isEmgSignalInverted === 'function' ? isEmgSignalInverted() : false,
  };
  updateEmgPeakSelectionWindowReadout();

  setStatus('status', 'Plotting...', 'loading');
  if (window.dpUplotAvailable && window.dpUplotAvailable()) {
    api('/api/emg/peak-selection/trace_data', payload)
      .then(data => {
        if (data.error) throw new Error(data.error);
        if (!window.dpRenderTrace('plotArea', data, {
          dragZoom: false,
          cursorReadout: false,
          onCursor: pos => updateEmgPeakSelectionPlotReadouts(pos.x, pos.y),
        })) throw new Error('uplot-render-failed');
        installEmgPeakSelectionPlotInteractions();
        setStatus('status', 'Ready', 'ok');
      })
      .catch(() => plotPng(payload));
    return;
  }

  plotPng(payload);
}

function plotPng(payload) {
  if (window.dpDestroyTrace) window.dpDestroyTrace('plotArea');
  api('/api/emg/peak-selection/plot', payload)
    .then(data => {
      if (data.error) throw new Error(data.error);
      setPlot('plotArea', data.img);
      updateEmgPeakSelectionWindowReadout();
      setStatus('status', 'Ready', 'ok');
    })
    .catch(e => {
      setStatus('status', 'Error: ' + e.message, 'error');
      toast('Plot failed: ' + e.message, true);
    });
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'browseMain',
  'loadChannelData',
  'loadChannels',
  'plot',
  'plotPng',
  'selectChannel',
  'selectSubfolder',
  'clearProcessedPeakPlot',
  'showProcessedPeakPlot',
  'refreshProcessedPeakOverlay',
  'installEmgPeakSelectionPlotInteractions',
  'collectLinkedChannelNames',
  'renderLinkedChannelList',
  'resetPreviewWindow',
  'setLinkedChannels',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
