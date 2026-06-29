function updateAnalysisCount() {
  const n = _analysisPairs.length;
  document.getElementById('analysisCount').textContent = n + ' selected for analysis';
}

function renderPairList() {
  const el = document.getElementById('pairList');
  if (!_allPairs.length) {
    el.innerHTML = '<div class="file-list-empty">No TIFF stacks found</div>';
    return;
  }

  el.innerHTML = _allPairs.map((p, i) => {
    const checked = _checkedPairIndices.has(i) ? 'checked' : '';
    const active = i === _currentPairIndex ? 'active' : '';
    return `
      <div class="file-item ${active}" style="display:flex;align-items:center;gap:6px" data-dp-click="selectPair(${i})">
        <input class="dp-check" type="checkbox" ${checked} data-dp-click="event.stopPropagation()" data-dp-change="togglePairCheck(${i}, this.checked)">
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis">${dpEscapeHtml(p.base || ('Pair ' + (i + 1)))}</span>
        <button class="btn-secondary" style="font-size:11px;padding:1px 7px;min-height:22px" data-dp-click="event.stopPropagation();addPairByIndex(${i})">Add</button>
      </div>`;
  }).join('');
}

function renderAnalysisList() {
  const el = document.getElementById('analysisList');
  if (!_analysisPairs.length) {
    el.innerHTML = '<div class="file-list-empty">No files selected</div>';
    updateAnalysisCount();
    return;
  }

  el.innerHTML = _analysisPairs.map((p, i) => {
    const active = i === _analysisActiveIndex ? 'active' : '';
    return `
      <div class="file-item ${active}" style="display:flex;align-items:center;gap:6px" data-dp-click="selectAnalysisPair(${i})">
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis">${dpEscapeHtml(p.base || ('Pair ' + (i + 1)))}</span>
        <button class="btn-secondary" style="font-size:11px;padding:1px 7px;min-height:22px" data-dp-click="event.stopPropagation();removeAnalysisPair(${i})">X</button>
      </div>`;
  }).join('');

  updateAnalysisCount();
}

function togglePairCheck(i, checked) {
  if (checked) _checkedPairIndices.add(i);
  else _checkedPairIndices.delete(i);
}

function clearPairChecks() {
  _checkedPairIndices.clear();
  renderPairList();
}

function ensurePreviewStackAvailable() {
  const sel = document.getElementById('stackSelect');
  if (!sel) return;
  const which = sel.value;
  if (which === 'stack2' && !_stack2Path && _stack1Path) {
    sel.value = 'stack1';
  } else if (which === 'stack1' && !_stack1Path && _stack2Path) {
    sel.value = 'stack2';
  }
}

function selectPair(i) {
  if (i < 0 || i >= _allPairs.length) return;
  _currentPairIndex = i;
  _currentPair = _allPairs[i];
  _stack1Path = _currentPair.stack1 || '';
  _stack2Path = _currentPair.stack2 || '';
  ensurePreviewStackAvailable();
  document.getElementById('stackSection').style.display = '';
  renderPairList();
  loadStack();
  loadRoiProfileForCurrent(true, false);
}

function selectAnalysisPair(i) {
  if (i < 0 || i >= _analysisPairs.length) return;
  _analysisActiveIndex = i;
  _currentPair = _analysisPairs[i];
  _stack1Path = _currentPair.stack1 || '';
  _stack2Path = _currentPair.stack2 || '';
  const k = pairKey(_currentPair);
  _currentPairIndex = _allPairs.findIndex(p => pairKey(p) === k);
  ensurePreviewStackAvailable();
  document.getElementById('stackSection').style.display = '';
  renderPairList();
  renderAnalysisList();
  loadStack();
  loadRoiProfileForCurrent(true, false);
}

function addPairByIndex(i) {
  if (i < 0 || i >= _allPairs.length) return;
  const p = _allPairs[i];
  const k = pairKey(p);
  if (_analysisPairs.some(x => pairKey(x) === k)) return;
  _analysisPairs.push({ base: p.base || '', stack1: p.stack1 || '', stack2: p.stack2 || '' });
  renderAnalysisList();
}

function addCheckedPairs() {
  if (!_checkedPairIndices.size) {
    setStatus('status', 'No checked files to add', 'error');
    return;
  }
  const before = _analysisPairs.length;
  Array.from(_checkedPairIndices).sort((a, b) => a - b).forEach(i => addPairByIndex(i));
  const added = _analysisPairs.length - before;
  setStatus('status', 'Added ' + added + ' file(s) to analysis list', 'ok');
}

function removeAnalysisPair(i) {
  if (i < 0 || i >= _analysisPairs.length) return;
  _analysisPairs.splice(i, 1);
  if (_analysisActiveIndex >= _analysisPairs.length) _analysisActiveIndex = _analysisPairs.length - 1;
  renderAnalysisList();
}

function removeActiveAnalysisPair() {
  if (_analysisActiveIndex < 0 || _analysisActiveIndex >= _analysisPairs.length) {
    setStatus('status', 'Select one file in the analysis list first', 'error');
    return;
  }
  removeAnalysisPair(_analysisActiveIndex);
}

function clearAnalysisList() {
  _analysisPairs = [];
  _analysisActiveIndex = -1;
  renderAnalysisList();
}

function scanFolder() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) {
    setStatus('status', 'Please enter a folder path', 'error');
    return;
  }

  setStatus('status', 'Scanning folder...', 'loading');
  api('/api/fluorescence/roi/browse', { folder })
    .then(d => {
      if (d.error) throw new Error(d.error);
      _allPairs = d.pairs || [];
      _checkedPairIndices.clear();
      _analysisPairs = [];
      _analysisActiveIndex = -1;
      _currentPairIndex = -1;
      _currentPair = null;
      _stack1Path = '';
      _stack2Path = '';
      renderPairList();
      renderAnalysisList();

      if (_allPairs.length) {
        selectPair(0);
      } else {
        document.getElementById('stackSection').style.display = 'none';
      }

      setStatus('status', _allPairs.length + ' stack pair(s) found', 'ok');
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function loadStack() {
  if (!_currentPair) return;
  ensurePreviewStackAvailable();
  const which = document.getElementById('stackSelect').value;
  let path = which === 'stack2' ? _stack2Path : _stack1Path;
  if (!path) {
    const fallback = which === 'stack2' ? _stack1Path : _stack2Path;
    if (fallback) {
      document.getElementById('stackSelect').value = which === 'stack2' ? 'stack1' : 'stack2';
      path = fallback;
    }
  }
  if (!path) {
    setStatus('status', 'Selected stack path is empty', 'error');
    return;
  }

  const lut = document.getElementById('lutSelect').value;
  const frame = parseInt(document.getElementById('frameSlider').value, 10) || 0;
  setStatus('status', 'Loading frame...', 'loading');

  api('/api/fluorescence/roi/load_stack', { stack_path: path, frame, lut })
    .then(d => {
      if (d.error) throw new Error(d.error);
      _nFrames = d.n_frames;
      _imgW = d.width;
      _imgH = d.height;

      const slider = document.getElementById('frameSlider');
      slider.max = Math.max(0, _nFrames - 1);
      slider.value = Math.min(frame, _nFrames - 1);
      document.getElementById('frameLabel').textContent = (parseInt(slider.value, 10) + 1) + ' / ' + _nFrames;

      const img = document.getElementById('tiffImg');
      img.onload = () => {
        applyPreviewScale(false);
        initCanvas();
      };
      img.src = 'data:image/png;base64,' + d.img;

      const stackTag = which === 'stack2' ? 'Stack 2' : 'Stack 1';
      const pairName = _currentPair.base || 'Selected pair';
      document.getElementById('stackInfo').textContent =
        pairName + ' | ' + stackTag + ' | ' + _nFrames + ' frames | ' + _imgW + 'x' + _imgH;
      setStatus('status', 'Ready', 'ok');
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function onFrameSlide() {
  const idx = parseInt(document.getElementById('frameSlider').value, 10) || 0;
  document.getElementById('frameLabel').textContent = (idx + 1) + ' / ' + _nFrames;
  loadStack();
}

function applyPreviewScale(redraw = true) {
  if (!_imgW) return;
  const scale = parseFloat(document.getElementById('previewScale').value) || 0.85;
  const wrap = document.getElementById('previewWrap');
  const surface = wrap?.closest('.roi-preview-surface');
  const available = surface ? Math.max(360, surface.clientWidth - 24) : 1120;
  const targetW = Math.max(360, Math.round(_imgW * scale));
  wrap.style.width = Math.min(1120, available, targetW) + 'px';
  if (redraw) requestAnimationFrame(initCanvas);
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'addCheckedPairs',
  'addPairByIndex',
  'applyPreviewScale',
  'clearAnalysisList',
  'clearPairChecks',
  'ensurePreviewStackAvailable',
  'loadStack',
  'onFrameSlide',
  'removeActiveAnalysisPair',
  'removeAnalysisPair',
  'renderAnalysisList',
  'renderPairList',
  'scanFolder',
  'selectAnalysisPair',
  'selectPair',
  'togglePairCheck',
  'updateAnalysisCount',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
