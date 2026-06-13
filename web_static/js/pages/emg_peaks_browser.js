let _emgPlotDrag = null;
let _emgTraceDuration = null;

function formatEmgNumber(value, digits) {
  if (!Number.isFinite(value)) return '--';
  const abs = Math.abs(value);
  if (abs >= 1000 || (abs > 0 && abs < 0.001)) return value.toExponential(3);
  return value.toFixed(digits == null ? 4 : digits).replace(/\.?0+$/, '');
}

function updateEmgPlotReadouts(x, y) {
  const coord = document.getElementById('plotCoordReadout');
  if (coord) coord.textContent = `Cursor: x ${formatEmgNumber(x, 5)}, y ${formatEmgNumber(y, 3)}`;
  updateEmgWindowReadout();
}

function updateEmgWindowReadout() {
  const readout = document.getElementById('plotWindowReadout');
  if (!readout) return;
  const xMin = document.getElementById('xMin')?.value || '';
  const xMax = document.getElementById('xMax')?.value || '';
  readout.textContent = `Window: ${xMin || '--'} to ${xMax || '--'} s`;
}

function clearProcessedPeakPlot() {
  const card = document.getElementById('processedPlotCard');
  if (card) card.hidden = true;
  setPlot('processedPlotArea', null);
}

function showProcessedPeakPlot(img, label) {
  const card = document.getElementById('processedPlotCard');
  if (card) card.hidden = false;
  setPlot('processedPlotArea', img, 'png', label || 'Processed peak detection preview');
}

function resetPreviewWindow() {
  document.getElementById('xMin').value = 0;
  document.getElementById('xMax').value = _emgTraceDuration || '';
  updateEmgWindowReadout();
  plot();
}

function emgValueFromPointer(plot, ev) {
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

function installEmgPlotInteractions() {
  if (!window.dpGetTrace) return;
  const chart = window.dpGetTrace('plotArea');
  const over = chart && chart.root ? chart.root.querySelector('.u-over') : null;
  if (!chart || !over || over._emgPeakInteractionsBound) return;

  over._emgPeakInteractionsBound = true;
  over.addEventListener('pointermove', ev => {
    const pos = emgValueFromPointer(chart, ev);
    if (pos) updateEmgPlotReadouts(pos.x, pos.y);
  });
  over.addEventListener('pointerdown', ev => {
    if (ev.button !== 0) return;
    const pos = emgValueFromPointer(chart, ev);
    if (!pos) return;
    _emgPlotDrag = { left: pos.left, x: pos.x };
  });
  over.addEventListener('pointerup', ev => {
    if (!_emgPlotDrag) return;
    const start = _emgPlotDrag;
    _emgPlotDrag = null;
    const pos = emgValueFromPointer(chart, ev);
    if (!pos || Math.abs(pos.left - start.left) < 6) return;

    const x0 = Math.min(start.x, pos.x);
    const x1 = Math.max(start.x, pos.x);
    if (!Number.isFinite(x0) || !Number.isFinite(x1) || x1 <= x0) return;
    document.getElementById('xMin').value = formatEmgNumber(x0, 6);
    document.getElementById('xMax').value = formatEmgNumber(x1, 6);
    updateEmgWindowReadout();
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
  api('/api/emg/browse', { folder })
    .then(data => {
      if (data.error) throw new Error(data.error);

      _currentFolder = folder;
      _currentSubfolder = null;
      _currentChannel = null;
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
  api('/api/emg/load_channels', {
    folder: _currentFolder,
    subfolder: _currentSubfolder
  })
    .then(data => {
      if (data.error) throw new Error(data.error);
      const list = document.getElementById('channelList');
      list.innerHTML = '';

      (data.channels || []).forEach(ch => {
        const item = document.createElement('div');
        item.className = 'file-item';
        item.textContent = ch;
        item.onclick = () => selectChannel(item, ch);
        list.appendChild(item);
      });

      if ((data.channels || []).length > 0) {
        selectChannel(list.children[0], data.channels[0]);
      } else {
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
  api('/api/emg/load', {
    folder: _currentFolder,
    subfolder: _currentSubfolder,
    channel: _currentChannel
  })
    .then(data => {
      if (data.error) throw new Error(data.error);
      _emgTraceDuration = data.duration || null;
      document.getElementById('xMax').value = data.duration || 10;
      updateEmgWindowReadout();
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
  const payload = { path, x_min, x_max };
  updateEmgWindowReadout();

  setStatus('status', 'Plotting...', 'loading');
  if (window.dpUplotAvailable && window.dpUplotAvailable()) {
    api('/api/emg/trace_data', payload)
      .then(data => {
        if (data.error) throw new Error(data.error);
        if (!window.dpRenderTrace('plotArea', data, {
          dragZoom: false,
          cursorReadout: false,
          onCursor: pos => updateEmgPlotReadouts(pos.x, pos.y),
        })) throw new Error('uplot-render-failed');
        installEmgPlotInteractions();
        setStatus('status', 'Ready', 'ok');
      })
      .catch(() => plotPng(payload));
    return;
  }

  plotPng(payload);
}

function plotPng(payload) {
  if (window.dpDestroyTrace) window.dpDestroyTrace('plotArea');
  api('/api/emg/plot', payload)
    .then(data => {
      if (data.error) throw new Error(data.error);
      setPlot('plotArea', data.img);
      updateEmgWindowReadout();
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
  'installEmgPlotInteractions',
  'resetPreviewWindow',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
