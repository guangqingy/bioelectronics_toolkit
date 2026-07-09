let _queue = [];
let _available = [];
let _selectedAvailIdx = -1;
let _selectedQueueIdx = -1;

window.dpApplyRunManifest = manifest => {
  dpApplyRunManifestFallback(manifest);
  const params = manifest.parameters || {};
  if (Array.isArray(params.queue)) {
    _queue = params.queue.map(item => ({...item}));
    _selectedQueueIdx = _queue.length ? 0 : -1;
    renderQueue();
    updateSummary();
  }
};

function browseMain() {
  const folder = document.getElementById('mainFolder').value.trim();
  if (!folder) {
    setStatus('status', 'Enter main folder path', 'error');
    toast('Enter folder path', true);
    return;
  }

  setStatus('status', 'Browsing folder...', 'loading');
  api('/api/figure/browse', { folder })
    .then(r => {
      if (r.error) throw new Error(r.error);
      _available = r.subfolders || [];
      _selectedAvailIdx = -1;

      if (_available.length === 0) {
        document.getElementById('subList').innerHTML = '<div class="file-list-empty">No subfolders found</div>';
        setStatus('status', 'No subfolders', 'error');
        return;
      }

      renderAvailable();
      setStatus('status', 'Ready', 'ok');
    })
    .catch(e => {
      setStatus('status', 'Browse failed', 'error');
      toast('Browse failed: ' + e.message, true);
    });
}

function renderAvailable() {
  const subList = document.getElementById('subList');
  if (_available.length === 0) {
    subList.innerHTML = '<div class="file-list-empty">No subfolders found</div>';
    return;
  }
  subList.innerHTML = _available.map((sf, i) =>
    `<div class="file-item${_selectedAvailIdx === i ? ' active' : ''}" data-dp-click="selectAvailable(${i})">${dpEscapeHtml(sf.name)}</div>`
  ).join('');
}

function selectAvailable(idx) {
  _selectedAvailIdx = idx;
  renderAvailable();
}

function addSelectedSubfolder() {
  if (_selectedAvailIdx < 0 || _selectedAvailIdx >= _available.length) {
    toast('Select an available subfolder first', true);
    return;
  }
  const sf = _available[_selectedAvailIdx];
  if (!_queue.find(q => q.path === sf.path)) {
    _queue.push({ path: sf.path, folder_name: sf.name, label: sf.name });
    _selectedQueueIdx = _queue.length - 1;
    renderQueue();
    toast('Added to queue: ' + sf.name);
    updateSummary();
  } else {
    toast('This subfolder is already in queue', true);
  }
}

function removeSelectedQueue() {
  if (_selectedQueueIdx < 0 || _selectedQueueIdx >= _queue.length) {
    toast('Select a queue item first', true);
    return;
  }
  _queue.splice(_selectedQueueIdx, 1);
  _selectedQueueIdx = -1;
  document.getElementById('queueLabel').value = '';
  renderQueue();
  updateSummary();
}

function renderQueue() {
  const qList = document.getElementById('queueList');

  if (_queue.length === 0) {
    qList.innerHTML = '<div class="file-list-empty">Queue is empty</div>';
    return;
  }

  qList.innerHTML = _queue.map((item, i) =>
    `<div class="file-item${_selectedQueueIdx === i ? ' active' : ''}" data-dp-click="selectQueueItem(${i})">${dpEscapeHtml(item.folder_name || item.label)} -> ${dpEscapeHtml(item.label)}</div>`
  ).join('');
}

function selectQueueItem(idx) {
  _selectedQueueIdx = idx;
  document.getElementById('queueLabel').value = _queue[idx].label;
  renderQueue();
}

function setLabel() {
  if (_selectedQueueIdx < 0 || _selectedQueueIdx >= _queue.length) {
    toast('Select a queue item first', true);
    return;
  }

  const newLabel = document.getElementById('queueLabel').value.trim();
  _queue[_selectedQueueIdx].label = newLabel || (_queue[_selectedQueueIdx].folder_name || _queue[_selectedQueueIdx].label);
  renderQueue();
  toast('Label updated');
  updateSummary();
}

function useFolderName() {
  if (_selectedQueueIdx < 0 || _selectedQueueIdx >= _queue.length) {
    toast('Select a queue item first', true);
    return;
  }
  const folderName = _queue[_selectedQueueIdx].folder_name || _queue[_selectedQueueIdx].label;
  document.getElementById('queueLabel').value = folderName;
}

function clearQueue() {
  _queue = [];
  _selectedQueueIdx = -1;
  renderQueue();
  document.getElementById('queueLabel').value = '';
  updateSummary();
}

function moveQueue(delta) {
  if (_selectedQueueIdx < 0 || _selectedQueueIdx >= _queue.length) {
    toast('Select a queue item first', true);
    return;
  }
  const j = _selectedQueueIdx + delta;
  if (j < 0 || j >= _queue.length) return;
  const tmp = _queue[_selectedQueueIdx];
  _queue[_selectedQueueIdx] = _queue[j];
  _queue[j] = tmp;
  _selectedQueueIdx = j;
  renderQueue();
  updateSummary();
}

function metricFlags() {
  return {
    peak: document.getElementById('metPeak').checked,
    integral: document.getElementById('metInt').checked,
  };
}

function buildPayload() {
  return {
    main_folder: document.getElementById('mainFolder').value.trim(),
    output_name: document.getElementById('outputName').value.trim(),
    queue: _queue,
    metrics: metricFlags(),
    x_lin_ranges: document.getElementById('xLin').value.trim(),
    x_log_ranges: document.getElementById('xLog').value.trim(),
  };
}

function previewPlots() {
  if (_queue.length === 0) {
    toast('Add items to queue first', true);
    return;
  }

  const metrics = metricFlags();
  if (!metrics.peak && !metrics.integral) {
    toast('Select at least one metric', true);
    return;
  }

  btnBusy('btnPreview', true, 'Previewing...');
  setStatus('status', 'Generating preview...', 'loading');

  api('/api/figure/plot', buildPayload())
    .then(r => {
      if (r.error) throw new Error(r.error);
      renderPreviewImages(r.images || []);
      const peakN = (r.series_count && r.series_count.peak) || 0;
      const intN = (r.series_count && r.series_count.integral) || 0;
      document.getElementById('queueSummary').innerHTML =
        'Preview generated.<br>' +
        'Queue items: ' + _queue.length + '<br>' +
        'Peak series: ' + peakN + '<br>' +
        'Integral series: ' + intN;
      setStatus('status', 'Preview complete', 'ok');
      toast('Preview complete');
    })
    .catch(e => {
      setStatus('status', 'Preview failed', 'error');
      toast('Preview failed: ' + e.message, true);
    })
    .finally(() => btnBusy('btnPreview', false, 'Preview Plot'));
}

async function runFigureBackgroundJob(endpoint, payload, label) {
  return dpRunJobEndpoint(endpoint, payload, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `${label}${pct}${msg}`, 'loading');
    },
  });
}

function runExportAction(action) {
  if (_queue.length === 0) {
    toast('Add items to queue first', true);
    return;
  }

  const metrics = metricFlags();
  if (!metrics.peak && !metrics.integral) {
    toast('Select at least one metric', true);
    return;
  }

  const btnMap = {
    analyze: ['btnAnalyze', 'Analyzing...'],
    normalize: ['btnNormalize', 'Normalizing...'],
    svg: ['btnSvg', 'Exporting SVG...'],
  };
  const cfg = btnMap[action] || ['btnAnalyze', 'Running...'];
  btnBusy(cfg[0], true, cfg[1]);
  setStatus('status', 'Running ' + action + '...', 'loading');

  const payload = buildPayload();
  payload.action = action;

  runFigureBackgroundJob('/api/figure/run_job', payload, 'Running ' + action)
    .then(r => {
      if (r.error) throw new Error(r.error);
      const files = (r.generated_files || []).slice(0, 8).map(f => dpEscapeHtml(f)).join('<br>');
      document.getElementById('queueSummary').innerHTML =
        'Action: ' + dpEscapeHtml(action) + '<br>' +
        'Saved folder: ' + dpEscapeHtml(r.saved_dir || '-') + '<br>' +
        'Generated files: ' + (r.generated_count || 0) + '<br>' +
        (files ? ('Sample outputs:<br>' + files) : '');
      setStatus('status', 'Complete', 'ok');
      toast('Saved ' + (r.generated_count || 0) + ' file(s) to disk');
      recordRunHistory({
        view: 'abf_figure',
        title: 'ABF Figure ' + action,
        status: 'ok',
        project_root: document.getElementById('mainFolder').value.trim() || r.saved_dir || '',
        input_files: (_queue || []).map(item => ({path: item.path, role: 'summary_folder', label: item.label || item.folder_name || ''})),
        outputs: dpAsPathRecords(r.generated_files || [], action === 'svg' ? 'svg_figure' : 'figure_output'),
        parameters: payload,
        metadata: {
          action,
          saved_dir: r.saved_dir || '',
          generated_count: r.generated_count || 0,
        },
      });
    })
    .catch(e => {
      setStatus('status', 'Run failed', 'error');
      toast('Run failed: ' + e.message, true);
    })
    .finally(() => {
      btnBusy('btnAnalyze', false, 'Analyze Queue');
      btnBusy('btnNormalize', false, 'Normalize');
      btnBusy('btnSvg', false, 'Export SVG');
    });
}

function renderPreviewImages(images) {
  const area = document.getElementById('plotArea');
  if (!images || images.length === 0) {
    area.innerHTML = '<div class="plot-placeholder">No preview images</div>';
    return;
  }
  area.innerHTML = '<div class="figure-preview-grid">' + images.map(item => {
    const name = dpEscapeHtml(item.name || 'figure');
    const src = dpEscapeHtml(item.img || '');
    return (
      '<figure class="figure-preview-item">' +
      '<figcaption class="figure-preview-title">' + name + '</figcaption>' +
      '<img class="figure-preview-img" src="data:image/png;base64,' + src + '" alt="' + name + '" />' +
      '</figure>'
    );
  }).join('') + '</div>';
}

function updateSummary() {
  document.getElementById('queueSummary').textContent =
    _queue.length ? ('Queue items: ' + _queue.length + '. Configure ranges and run Preview or export.') : 'Queue is empty.';
}

window.addEventListener('load', () => {
  renderQueue();
  renderAvailable();
  updateSummary();
  setStatus('status', 'Ready', 'ok');
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'addSelectedSubfolder',
  'browseMain',
  'buildPayload',
  'clearQueue',
  'metricFlags',
  'moveQueue',
  'previewPlots',
  'removeSelectedQueue',
  'renderAvailable',
  'renderPreviewImages',
  'renderQueue',
  'runExportAction',
  'runFigureBackgroundJob',
  'selectAvailable',
  'selectQueueItem',
  'setLabel',
  'updateSummary',
  'useFolderName',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
