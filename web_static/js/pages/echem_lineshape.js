let _samples = [];
let _selected = new Set();
let _avgData  = null;
let _avgB64   = null;

function browseBase() {
  const mat = document.getElementById('baseDir').value;
  if (!mat) return;
  api('/api/echem/lineshape/browse', {base_dir: mat})
    .then(d => {
      if (d.error) { toast(d.error, true); return; }
      if (d.materials && d.materials.length) {
        const sel = document.getElementById('material');
        sel.value = d.materials[0];
        setStatus('status', 'Found materials: ' + d.materials.join(', '), 'ok');
      }
    });
}

function loadSamples() {
  const base_dir = document.getElementById('baseDir').value.trim();
  const material = document.getElementById('material').value.trim();
  const index_k  = parseInt(document.getElementById('indexK').value, 10) || 1;
  const kind     = document.getElementById('kind').value;
  const crop_t0  = parseFloat(document.getElementById('cropT0').value) || -0.005;
  const crop_t1  = parseFloat(document.getElementById('cropT1').value) || 0.020;

  if (!base_dir || !material) { setStatus('status', 'Set base dir and material', 'error'); return; }
  btnBusy('btnLoad', true, 'Loading…');
  setStatus('status', 'Scanning segments…', 'loading');

  api('/api/echem/lineshape/load', {base_dir, material, index_k, kind, crop_t0, crop_t1})
    .then(d => {
      btnBusy('btnLoad', false, 'Load Segments');
      if (d.error) { setStatus('status', 'Error: ' + d.error, 'error'); return; }
      _samples = d.samples || [];
      _selected = new Set(_samples.map((_, i) => i));
      renderSampleList();
      document.getElementById('sampleListSection').style.display = '';
      document.getElementById('btnPlot').style.display = '';
      document.getElementById('fileInfo').textContent =
        material + ' · index ' + index_k + ' · ' + kind + ' · ' + _samples.length + ' segments';
      setStatus('status', _samples.length + ' segments loaded', 'ok');
      updatePlot();
    })
    .catch(e => { btnBusy('btnLoad', false, 'Load Segments'); setStatus('status', 'Error: ' + e.message, 'error'); });
}

function renderSampleList() {
  const el = document.getElementById('sampleList');
  document.getElementById('sampleCount').textContent = _selected.size + ' / ' + _samples.length;
  el.innerHTML = _samples.map((s, i) => {
    const active = _selected.has(i);
    return `<div class="file-item${active ? ' active' : ''}" onclick="toggleSample(${i})" data-idx="${i}">${s.label}</div>`;
  }).join('');
}

function toggleSample(i) {
  if (_selected.has(i)) _selected.delete(i);
  else _selected.add(i);
  renderSampleList();
}

function selectAll(val) {
  if (val) _samples.forEach((_, i) => _selected.add(i));
  else _selected.clear();
  renderSampleList();
}

function updatePlot() {
  if (!_samples.length) { setStatus('status', 'No samples loaded', 'error'); return; }
  const selected = Array.from(_selected).sort((a,b)=>a-b);
  const crop_t0  = parseFloat(document.getElementById('cropT0').value) || -0.005;
  const crop_t1  = parseFloat(document.getElementById('cropT1').value) || 0.020;
  const x_offset = parseFloat(document.getElementById('xOffset').value) || 0;
  const y_min    = parseFloat(document.getElementById('yMin').value) || null;
  const y_max    = parseFloat(document.getElementById('yMax').value) || null;
  const kind     = document.getElementById('kind').value;

  btnBusy('btnPlot', true, 'Plotting…');
  setStatus('status', 'Rendering…', 'loading');

  api('/api/echem/lineshape/plot', {
    samples: _samples.map(s => ({label: s.label, t: s.t, y: s.y})),
    selected, crop_t0, crop_t1, x_offset,
    y_min: y_min || undefined, y_max: y_max || undefined, kind
  }).then(d => {
    btnBusy('btnPlot', false, 'Update Plot');
    if (d.error) { setStatus('status', 'Error: ' + d.error, 'error'); return; }
    setPlot('avgPlotArea', d.avg_img);
    _avgB64 = d.avg_img;
    _avgData = d.avg_data;

    const gc = document.getElementById('gridCard');
    gc.style.display = '';
    document.getElementById('gridImg').src = 'data:image/png;base64,' + d.grid_img;
    document.getElementById('gridInfo').textContent = d.n_selected + ' selected / ' + d.n_total + ' total';

    document.getElementById('btnExportCSV').style.display = '';
    document.getElementById('btnExportPNG').style.display = '';
    setStatus('status', 'n=' + d.n_selected + ' averaged', 'ok');
  }).catch(e => { btnBusy('btnPlot', false, 'Update Plot'); setStatus('status', 'Error: ' + e.message, 'error'); });
}

function exportCSV() {
  if (!_avgData) return;
  const sourcePath = (_samples && _samples.length && _samples[0].file) ? _samples[0].file : '';
  setStatus('status', 'Saving CSV...', 'loading');
  dpRunJobEndpoint('/api/echem/lineshape/export_avg_job', {avg_data: _avgData, source_path: sourcePath, mode: 'save'}, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Saving CSV${pct}${msg}`, 'loading');
    },
  }).then(d => {
    setStatus('status', 'Saved: ' + (d.saved_path || 'ok'), 'ok');
    recordRunHistory({
      view: 'echem_lineshape',
      title: 'EChem Lineshape Average CSV',
      status: 'ok',
      project_root: document.getElementById('baseDir').value.trim() || dpPathDir(sourcePath),
      input_files: (_samples || []).map(s => ({path: s.file || sourcePath, role: 'segment'})).filter(s => s.path),
      outputs: d.saved_path ? [{path: d.saved_path, type: 'lineshape_avg_csv'}] : [],
      parameters: {
        base_dir: document.getElementById('baseDir').value.trim(),
        material: document.getElementById('material').value.trim(),
        index_k: document.getElementById('indexK').value,
        kind: document.getElementById('kind').value,
        selected_count: _selected.size,
      },
    });
  }).catch(e => setStatus('status', 'Error: ' + e.message, 'error'));
}

function exportPNG() {
  if (!_avgB64) return;
  const a = Object.assign(document.createElement('a'), {
    href: 'data:image/png;base64,' + _avgB64,
    download: 'lineshape_avg.png'
  });
  a.click();
}

window.addEventListener('load', () => setStatus('status', 'Ready', 'ok'));

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'browseBase',
  'exportCSV',
  'exportPNG',
  'loadSamples',
  'renderSampleList',
  'selectAll',
  'toggleSample',
  'updatePlot',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
