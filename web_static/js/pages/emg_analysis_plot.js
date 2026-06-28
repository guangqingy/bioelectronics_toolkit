function reloadCurrentEmgAnalysisFile() {
  if (!_currentFile) return;
  if (_emgAnalysisFiles.length) {
    renderEmgAnalysisFileList({preserveChannel: true, loadProfile: false});
    return;
  }
  const active = document.querySelector('#emgAnalysisFileList .file-item.active');
  selectFile(active, _currentFile, {preserveChannel: true, loadProfile: false});
}

function onPreviewMergeChanged() {
  reloadCurrentEmgAnalysisFile();
}

async function selectFile(el, path, options) {
  const opts = options || {};
  const requestSeq = ++_fileLoadSeq;
  const previousChannel = opts.preserveChannel ? _currentChannel : null;
  const mergeAtLoad = previewMergeEnabled();
  document.querySelectorAll('#emgAnalysisFileList .file-item').forEach(e => e.classList.remove('active'));
  if (el) el.classList.add('active');
  _currentFile = path;
  _currentChannel = null;
  setStatus('status', 'Loading EMG recording...', 'loading');

  try {
    const data = await api('/api/emg/analysis/load', {path, merge_pair: mergeAtLoad});
    if (requestSeq !== _fileLoadSeq || path !== _currentFile) return;
    if (data.error) throw new Error(data.error);
    _metadata = data;
    if (typeof autoFillEmgAnalysisRenameToken === 'function') {
      autoFillEmgAnalysisRenameToken(path);
    }
    document.getElementById('xMax').value = data.duration || 10;
    updateInfoCard();
    populateChannelList(data.channels || []);

    if (opts.loadProfile !== false) {
      _activeProfileLoadSeq = requestSeq;
      await loadGenericFileProfileForCurrent(true);
      if (_activeProfileLoadSeq === requestSeq) _activeProfileLoadSeq = 0;
      if (requestSeq !== _fileLoadSeq || path !== _currentFile) return;
      if (previewMergeEnabled() !== mergeAtLoad) {
        renderEmgAnalysisFileList({
          preserveChannel: true,
          loadProfile: false,
        });
        return;
      }
    }

    const channels = data.channels || [];
    const channelToSelect = (previousChannel && channels.includes(previousChannel))
      ? previousChannel
      : (_currentChannel && channels.includes(_currentChannel) ? _currentChannel : channels[0]);
    if (channelToSelect) {
      const item = [...document.querySelectorAll('#channelList .file-item')].find(node => node.textContent === channelToSelect);
      selectChannel(item || document.querySelector('#channelList .file-item'), channelToSelect);
    } else {
      setPlot('plotArea', null);
      setStatus('status', 'No amplifier channels found', 'error');
    }
  } catch (e) {
    if (requestSeq !== _fileLoadSeq) return;
    setStatus('status', 'Error: ' + e.message, 'error');
  }
}

function populateChannelList(channels) {
  const list = document.getElementById('channelList');
  list.innerHTML = '';
  channels.forEach(ch => {
    const item = document.createElement('div');
    item.className = 'file-item';
    item.textContent = ch;
    item.onclick = () => selectChannel(item, ch);
    list.appendChild(item);
  });
}

function selectChannel(el, channel) {
  document.querySelectorAll('#channelList .file-item').forEach(e => e.classList.remove('active'));
  if (el) {
    el.classList.add('active');
  }
  _currentChannel = channel;
  plot();
}

function updateInfoCard() {
  const card = document.getElementById('infoCard');
  const sr = _metadata.sampling_rate || _metadata.sample_rate || 0;
  const dur = _metadata.duration || _metadata.duration_s || 0;
  const nCh = _metadata.channels ? _metadata.channels.length : 0;
  const nAmp = _metadata.num_amplifiers || nCh;
  const merged = _metadata.merged_pair ? 'Yes' : 'No';
  const segments = _metadata.segment_count || (_metadata.source_paths ? _metadata.source_paths.length : 1);

  card.innerHTML = '<div style="padding:8px; display:grid; gap:6px;">' +
    '<div><strong>Sampling Rate:</strong> ' + (sr || 'N/A') + ' Hz</div>' +
    '<div><strong>Duration:</strong> ' + Number(dur || 0).toFixed(2) + ' s</div>' +
    '<div><strong>Channels:</strong> ' + nCh + '</div>' +
    '<div><strong>Amplifier Channels:</strong> ' + nAmp + '</div>' +
    '<div><strong>Merged Preview:</strong> ' + merged + '</div>' +
    '<div><strong>Segments:</strong> ' + segments + '</div>' +
    '</div>';
}

function plot() {
  if (!_currentFile || !_currentChannel) return;
  const plotSeq = ++_plotSeq;
  const fileAtStart = _currentFile;
  const channelAtStart = _currentChannel;

  const payload = {
    path: fileAtStart,
    channel: channelAtStart,
    merge_pair: previewMergeEnabled(),
    ...currentViewParams(),
    ...currentFigureParams(),
  };

  setStatus('status', 'Plotting...', 'loading');
  api('/api/emg/analysis/plot', payload)
    .then(data => {
      if (plotSeq !== _plotSeq || fileAtStart !== _currentFile || channelAtStart !== _currentChannel) return;
      if (data.error) throw new Error(data.error);
      setPlot('plotArea', data.img);
      setPlot('processArea', null);
      const ds = data.downsample && data.downsample > 1 ? ` · ${data.downsample}x downsample` : '';
      const pts = data.plotted_points ? ` · ${data.plotted_points} pts` : '';
      const merged = previewMergeEnabled() ? ' · merged preview' : '';
      const inverted = data.inverted_y ? ' · inverted Y' : '';
      setStatus('status', `Ready${merged}${inverted}${ds}${pts}`, 'ok');
    })
    .catch(e => {
      if (plotSeq !== _plotSeq) return;
      setStatus('status', 'Error: ' + e.message, 'error');
    });
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'onPreviewMergeChanged',
  'plot',
  'populateChannelList',
  'reloadCurrentEmgAnalysisFile',
  'selectChannel',
  'selectFile',
  'updateInfoCard',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
