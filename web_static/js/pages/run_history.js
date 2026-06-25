let _runs = [];
let _selectedRunIndex = -1;
let _selectedManifest = null;
let _selectedManifestPath = '';
let _compareManifest = null;
let _lastHistoryPath = '';
let _lastProjectRoot = '';

function initRunViewFilter() {
  const select = document.getElementById('runViewFilter');
  const options = [['', 'All interfaces']]
    .concat(SETTINGS_VIEW_ORDER.filter(v => v !== 'global' && v !== 'index').map(v => [v, settingsViewLabel(v)]));
  select.innerHTML = options.map(([value, label]) =>
    `<option value="${dpEscapeHtml(value)}">${dpEscapeHtml(label)}</option>`
  ).join('');
}

function runProjectRoot() {
  return document.getElementById('runProjectRoot').value.trim();
}

function defaultRunProjectRoot() {
  return (window.RUN_HISTORY_BOOTSTRAP || {}).defaultDataDir || DEFAULT_DATA_DIR || '';
}

function useDefaultRunProjectRoot() {
  const input = document.getElementById('runProjectRoot');
  if (input) input.value = defaultRunProjectRoot();
  return loadRuns({selectFirst: true});
}

function renderRunList() {
  const list = document.getElementById('runList');
  if (!_runs.length) {
    const view = document.getElementById('runViewFilter')?.value || '';
    const filter = view ? ` for ${settingsViewLabel(view)}` : '';
    list.innerHTML = `
      <div class="file-list-empty">
        No run manifests found${filter}. Run an analysis or export first, then refresh history.
        ${_lastHistoryPath ? `<div class="run-page-meta" style="margin-top:6px">${dpEscapeHtml(_lastHistoryPath)}</div>` : ''}
      </div>`;
    return;
  }
  list.innerHTML = _runs.map((run, i) => {
    const active = i === _selectedRunIndex ? ' active' : '';
    return `
      <div class="run-page-row${active}" data-dp-click="selectRun(${i})">
        <div class="run-page-title">${dpEscapeHtml(run.title || run.view || 'Run')}</div>
        <div class="run-page-meta">${dpEscapeHtml(settingsViewLabel(run.view || ''))} · ${dpEscapeHtml(run.status || '')}</div>
        <div class="run-page-meta">${dpEscapeHtml(run.completed_at || '')} · ${Number(run.output_count || 0)} output(s)</div>
      </div>`;
  }).join('');
}

function updateRunKpis(historyPath) {
  document.getElementById('runCount').textContent = String(_runs.length);
  document.getElementById('outputCount').textContent = String(_runs.reduce((sum, r) => sum + Number(r.output_count || 0), 0));
  document.getElementById('historyPath').textContent = historyPath || 'No history loaded';
}

function runFormatBytes(bytes) {
  const n = Number(bytes || 0);
  if (!n) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function loadRuns(options) {
  const opts = options || {};
  const input = document.getElementById('runProjectRoot');
  const root = runProjectRoot() || defaultRunProjectRoot();
  if (input && root && input.value.trim() !== root) input.value = root;
  if (!root) {
    setStatus('status', 'Choose a project folder first', 'error');
    return;
  }
  btnBusy('btnLoadRuns', true, 'Loading...');
  setStatus('status', 'Loading run history...', 'loading');
  try {
    const d = await api('/api/run_history/list', {
      project_root: root,
      view: document.getElementById('runViewFilter').value,
      limit: parseInt(document.getElementById('runLimit').value, 10) || 100,
    });
    if (d.error) throw new Error(d.error);
    _runs = d.runs || [];
    _lastHistoryPath = d.history_path || '';
    _lastProjectRoot = d.project_root || root;
    _selectedRunIndex = -1;
    _selectedManifest = null;
    _selectedManifestPath = '';
    renderRunList();
    updateRunKpis(d.history_path || '');
    document.getElementById('runHistoryMeta').textContent = `${_runs.length} run(s) loaded from ${_lastProjectRoot}`;
    document.getElementById('runDetailTitle').textContent = 'Manifest Details';
    document.getElementById('runDetailStatus').textContent = '';
    document.getElementById('runDetailBody').innerHTML = '<div class="run-empty">Select a run to inspect its inputs, outputs, and parameters.</div>';
    document.getElementById('manifestJson').textContent = '';
    if (_runs.length && opts.selectFirst) {
      await selectRun(0, {quiet: true});
    }
    setStatus('status', _runs.length ? 'Loaded ' + _runs.length + ' run(s)' : 'No run manifests found', _runs.length ? 'ok' : 'warning');
  } catch (e) {
    setStatus('status', 'Error: ' + e.message, 'error');
  } finally {
    btnBusy('btnLoadRuns', false, 'Refresh');
  }
}

function fileRows(records) {
  return (records || []).map(rec => ({
    Name: rec.name || '',
    Type: rec.type || rec.role || rec.ext || '',
    Exists: rec.exists ? 'yes' : 'no',
    Size: rec.size ? runFormatBytes(rec.size) : '',
    Path: rec.rel || rec.path || '',
  }));
}

function paramsPreview(params) {
  const text = JSON.stringify(params || {}, null, 2);
  return `<pre class="manifest-json compact">${dpEscapeHtml(text)}</pre>`;
}

function renderManifest(manifest, manifestPath) {
  _selectedManifest = manifest || null;
  _selectedManifestPath = manifestPath || '';
  document.getElementById('runDetailTitle').textContent = manifest.title || manifest.view || 'Run';
  document.getElementById('runDetailStatus').textContent =
    `${manifest.completed_at || ''} · ${manifest.status || ''} · ${manifestPath || ''}`;

  const inputRows = fileRows(manifest.input_files || []);
  const outputRows = fileRows(manifest.outputs || []);
  const warnings = (manifest.warnings || []).map(dpEscapeHtml).join('<br>');
  const errors = (manifest.errors || []).map(dpEscapeHtml).join('<br>');

  document.getElementById('runDetailBody').innerHTML = `
    <div class="run-action-row">
      <button class="btn-primary" type="button" data-dp-click="openSelectedManifestView()">Open Interface With Parameters</button>
      <button class="btn-secondary" type="button" data-dp-click="openSelectedManifestForRerun()">Prepare Manual Rerun</button>
      <button class="btn-secondary" type="button" data-dp-click="checkSelectedManifestFiles()">Check Files</button>
      <button class="btn-tertiary" type="button" data-dp-click="setCompareBase()">Use As Compare Base</button>
      <button class="btn-secondary" type="button" data-dp-click="compareSelectedWithBase()">Compare With Base</button>
      <button class="btn-secondary" type="button" data-dp-click="writeSelectedManifestReport()">Write Markdown Report</button>
      <button class="btn-secondary" type="button" data-dp-click="packageSelectedManifest()">Create Output Archive</button>
      <button class="btn-tertiary" type="button" data-dp-click="copySelectedManifestPath()">Copy Manifest Path</button>
    </div>
    <div id="runCompareBody"></div>
    <div id="runPreflightBody"></div>
    <div class="run-detail-grid">
      <div>
        <div class="prefs-section-title">Inputs</div>
        ${inputRows.length ? buildTable(inputRows, ['Name', 'Type', 'Exists', 'Size', 'Path']) : '<div class="run-empty">No inputs recorded</div>'}
      </div>
      <div>
        <div class="prefs-section-title">Outputs</div>
        ${outputRows.length ? buildTable(outputRows, ['Name', 'Type', 'Exists', 'Size', 'Path']) : '<div class="run-empty">No outputs recorded</div>'}
      </div>
    </div>
    <div class="prefs-section-title" style="margin-top:12px">Parameters</div>
    ${paramsPreview(manifest.parameters || {})}
    ${warnings ? `<div class="prefs-section-title" style="margin-top:12px">Warnings</div><div class="run-warning">${warnings}</div>` : ''}
    ${errors ? `<div class="prefs-section-title" style="margin-top:12px">Errors</div><div class="run-error">${errors}</div>` : ''}
  `;
  document.getElementById('manifestJson').textContent = JSON.stringify(manifest, null, 2);
}

function flattenForCompare(value, prefix, out) {
  if (value === null || value === undefined) {
    out[prefix || '(root)'] = '';
    return out;
  }
  if (Array.isArray(value)) {
    out[prefix || '(root)'] = JSON.stringify(value);
    return out;
  }
  if (typeof value !== 'object') {
    out[prefix || '(root)'] = String(value);
    return out;
  }
  Object.keys(value).sort().forEach(key => {
    const next = prefix ? `${prefix}.${key}` : key;
    flattenForCompare(value[key], next, out);
  });
  return out;
}

function compareManifests(base, current) {
  const a = flattenForCompare(base?.parameters || {}, '', {});
  const b = flattenForCompare(current?.parameters || {}, '', {});
  const keys = Array.from(new Set(Object.keys(a).concat(Object.keys(b)))).sort();
  return keys
    .filter(key => (a[key] ?? '') !== (b[key] ?? ''))
    .map(key => ({Key: key, Base: a[key] ?? '', Selected: b[key] ?? ''}));
}

function setCompareBase() {
  if (!_selectedManifest) {
    setStatus('status', 'Select a manifest first', 'error');
    return;
  }
  _compareManifest = JSON.parse(JSON.stringify(_selectedManifest));
  setStatus('status', 'Compare base set: ' + (_compareManifest.title || _compareManifest.run_id || 'run'), 'ok');
}

function compareSelectedWithBase() {
  if (!_compareManifest || !_selectedManifest) {
    setStatus('status', 'Set a compare base first, then select another run', 'error');
    return;
  }
  const diff = compareManifests(_compareManifest, _selectedManifest);
  const target = document.getElementById('runCompareBody');
  if (!target) return;
  target.innerHTML = `
    <div class="prefs-section-title">Parameter Diff</div>
    ${diff.length ? buildTable(diff, ['Key', 'Base', 'Selected']) : '<div class="run-empty">No parameter differences found.</div>'}
  `;
  setStatus('status', diff.length ? `Found ${diff.length} parameter difference(s)` : 'No parameter differences found', 'ok');
}

function openSelectedManifestView() {
  if (!_selectedManifest) {
    setStatus('status', 'Select a manifest first', 'error');
    return;
  }
  dpOpenManifestInView(_selectedManifest);
}

function openSelectedManifestForRerun() {
  if (!_selectedManifest) {
    setStatus('status', 'Select a manifest first', 'error');
    return;
  }
  dpOpenManifestInView(_selectedManifest, 'rerun');
}

function checkSummaryPills(check) {
  const s = check?.summary || {};
  const pairs = [
    ['Total', s.total || 0, 'neutral'],
    ['Unchanged', s.unchanged || 0, 'ok'],
    ['Changed', (s.changed || 0) + (s.timestamp_changed || 0), 'warn'],
    ['Missing', s.missing || 0, 'bad'],
    ['New', s.created_after_manifest || 0, 'warn'],
  ];
  return `<div class="run-check-summary">${pairs.map(([label, value, cls]) =>
    `<span class="run-check-pill ${cls}"><strong>${dpEscapeHtml(value)}</strong>${dpEscapeHtml(label)}</span>`
  ).join('')}</div>`;
}

function runCheckStatusLabel(status) {
  const label = String(status || '');
  const cls = label === 'unchanged' ? 'ok'
    : (label === 'missing' || label === 'changed' || label === 'invalid' ? 'bad' : 'warn');
  return `<span class="run-check-status ${cls}">${dpEscapeHtml(label.replaceAll('_', ' '))}</span>`;
}

function runCheckRows(rows) {
  return (rows || []).map(rec => ({
    Kind: dpEscapeHtml(rec.kind || ''),
    Name: dpEscapeHtml(rec.name || ''),
    Role: dpEscapeHtml(rec.role || ''),
    Status: runCheckStatusLabel(rec.status),
    Size: dpEscapeHtml(`${runFormatBytes(rec.recorded_size)} → ${runFormatBytes(rec.current_size)}`.trim()),
    Path: dpEscapeHtml(rec.rel || rec.path || ''),
  }));
}

async function checkSelectedManifestFiles() {
  if (!_selectedManifestPath && !_selectedManifest) {
    setStatus('status', 'Select a manifest first', 'error');
    return;
  }
  const target = document.getElementById('runPreflightBody');
  if (target) target.innerHTML = '<div class="run-empty">Checking recorded files...</div>';
  setStatus('status', 'Checking manifest files...', 'loading');
  try {
    const body = _selectedManifestPath ? {manifest_path: _selectedManifestPath} : {manifest: _selectedManifest};
    const d = await api('/api/run_history/check', body);
    if (d.error) throw new Error(d.error);
    const check = d.check || {};
    const rows = runCheckRows(check.rows || []);
    if (target) {
      target.innerHTML = `
        <div class="prefs-section-title">File Check</div>
        ${checkSummaryPills(check)}
        ${rows.length ? buildTable(rows, ['Kind', 'Name', 'Role', 'Status', 'Size', 'Path']) : '<div class="run-empty">No files recorded in this manifest.</div>'}
      `;
    }
    setStatus('status', check.status === 'ok' ? 'All recorded files look unchanged' : 'Manifest check needs attention', check.status === 'ok' ? 'ok' : 'warning');
  } catch (e) {
    if (target) target.innerHTML = `<div class="run-error">${dpEscapeHtml(e.message)}</div>`;
    setStatus('status', 'Error: ' + e.message, 'error');
  }
}

async function copySelectedManifestPath() {
  if (!_selectedManifestPath) return;
  try {
    await navigator.clipboard.writeText(_selectedManifestPath);
    setStatus('status', 'Manifest path copied', 'ok');
  } catch (e) {
    document.getElementById('manifestJson').textContent = _selectedManifestPath + '\n\n' + JSON.stringify(_selectedManifest || {}, null, 2);
    setStatus('status', 'Clipboard unavailable; path shown in JSON panel', 'warning');
  }
}

async function writeSelectedManifestReport() {
  if (!_selectedManifestPath && !_selectedManifest) {
    setStatus('status', 'Select a manifest first', 'error');
    return;
  }
  setStatus('status', 'Writing markdown report...', 'loading');
  try {
    const body = _selectedManifestPath ? {manifest_path: _selectedManifestPath, include_check: true} : {manifest: _selectedManifest, include_check: true};
    const d = await api('/api/run_history/report', body);
    if (d.error) throw new Error(d.error);
    document.getElementById('manifestJson').textContent = (d.report_path || 'Report preview') + '\n\n' + (d.report || '');
    setStatus('status', d.report_path ? 'Report written: ' + d.report_path : 'Report generated in preview', 'ok');
  } catch (e) {
    setStatus('status', 'Error: ' + e.message, 'error');
  }
}

async function packageSelectedManifest() {
  if (!_selectedManifestPath && !_selectedManifest) {
    setStatus('status', 'Select a manifest first', 'error');
    return;
  }
  setStatus('status', 'Creating output archive...', 'loading');
  try {
    const body = _selectedManifestPath
      ? {manifest_path: _selectedManifestPath, include_outputs: true, include_inputs: false}
      : {manifest: _selectedManifest, include_outputs: true, include_inputs: false};
    const d = await dpRunJobEndpoint('/api/run_history/package_job', body, {
      interval_ms: 1000,
      on_update: job => {
        const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
        const msg = job.message ? ` · ${job.message}` : '';
        setStatus('status', `Creating output archive${pct}${msg}`, 'loading');
      },
    });
    if (d.error) throw new Error(d.error);
    document.getElementById('manifestJson').textContent = JSON.stringify(d.index || {}, null, 2);
    setStatus('status', `Archive written: ${d.package_path || ''} (${d.included_count || 0} file(s), ${d.missing_count || 0} missing)`, d.missing_count ? 'warning' : 'ok');
  } catch (e) {
    setStatus('status', 'Error: ' + e.message, 'error');
  }
}

async function selectRun(i, options) {
  const opts = options || {};
  if (i < 0 || i >= _runs.length) return;
  _selectedRunIndex = i;
  renderRunList();
  const run = _runs[i];
  setStatus('status', 'Loading manifest...', 'loading');
  try {
    const d = await api('/api/run_history/get', {manifest_path: run.manifest_path});
    if (d.error) throw new Error(d.error);
    renderManifest(d.manifest || {}, d.manifest_path || run.manifest_path || '');
    if (!opts.quiet) setStatus('status', 'Manifest loaded', 'ok');
  } catch (e) {
    setStatus('status', 'Error: ' + e.message, 'error');
  }
}

window.addEventListener('load', () => {
  initRunViewFilter();
  document.getElementById('runProjectRoot').value = defaultRunProjectRoot();
  if (runProjectRoot()) loadRuns({selectFirst: true});
  else setStatus('status', 'Ready', 'ok');
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'checkSelectedManifestFiles',
  'checkSummaryPills',
  'compareManifests',
  'compareSelectedWithBase',
  'copySelectedManifestPath',
  'fileRows',
  'flattenForCompare',
  'initRunViewFilter',
  'loadRuns',
  'openSelectedManifestForRerun',
  'openSelectedManifestView',
  'packageSelectedManifest',
  'paramsPreview',
  'renderManifest',
  'renderRunList',
  'runCheckRows',
  'runCheckStatusLabel',
  'runFormatBytes',
  'runProjectRoot',
  'selectRun',
  'setCompareBase',
  'updateRunKpis',
  'useDefaultRunProjectRoot',
  'writeSelectedManifestReport',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
