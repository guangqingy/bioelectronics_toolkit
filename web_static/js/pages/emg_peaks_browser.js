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
      document.getElementById('xMax').value = data.duration || 10;
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

  setStatus('status', 'Plotting...', 'loading');
  if (window.dpUplotAvailable && window.dpUplotAvailable()) {
    api('/api/emg/trace_data', payload)
      .then(data => {
        if (data.error) throw new Error(data.error);
        if (!window.dpRenderTrace('plotArea', data)) throw new Error('uplot-render-failed');
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
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
