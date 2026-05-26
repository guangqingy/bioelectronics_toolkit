let _histologyCases = [];
let _selectedCase = null;

function parseSuffixOptions(text) {
  const raw = String(text || '');
  return raw
    .split(/[\n,;]+/)
    .map(s => s.trim())
    .filter(s => !!s);
}

function updateSuffixPick(preferred) {
  const listEl = document.getElementById('suffixList');
  const pickEl = document.getElementById('suffixPick');
  if (!listEl || !pickEl) return;

  const seen = new Set();
  const opts = [];
  for (const s of parseSuffixOptions(listEl.value)) {
    if (!seen.has(s)) { opts.push(s); seen.add(s); }
  }

  const prev = (preferred !== undefined && preferred !== null) ? String(preferred) : (pickEl.value || '');
  pickEl.innerHTML = '';
  if (!opts.length) {
    const o = document.createElement('option');
    o.value = '';
    o.textContent = '(none)';
    pickEl.appendChild(o);
  } else {
    for (const s of opts) {
      const o = document.createElement('option');
      o.value = s;
      o.textContent = s;
      pickEl.appendChild(o);
    }
  }
  pickEl.value = opts.includes(prev) ? prev : (opts[0] || '');

  localStorage.setItem('histology_suffix_list', listEl.value);
  localStorage.setItem('histology_suffix_pick', pickEl.value || '');
}

function onSuffixListChange() {
  updateSuffixPick(localStorage.getItem('histology_suffix_pick') || '');
}

function onSuffixPickChange() {
  const pickEl = document.getElementById('suffixPick');
  localStorage.setItem('histology_suffix_pick', (pickEl && pickEl.value) ? pickEl.value : '');
}

function _readBool(key, fallback=false) {
  const v = localStorage.getItem(key);
  if (v === null || v === undefined) return fallback;
  return v === '1' || v === 'true' || v === 'yes' || v === 'on';
}

function _writeBool(key, value) {
  localStorage.setItem(key, value ? '1' : '0');
}

function getRotateDeg() {
  const el = document.getElementById('rotateDeg');
  const v = parseInt((el && el.value) ? el.value : '0', 10);
  if ([0,90,180,270].includes(v)) return v;
  return 0;
}

function onRotateChange() {
  localStorage.setItem('histology_rotate_deg', String(getRotateDeg()));
  if (_selectedCase) {
    const el = getCaseElementByPath(_selectedCase);
    if (el) selectCase(el, _selectedCase);
  }
}

function getCaseElementByPath(path) {
  return Array.from(document.querySelectorAll('#caseList .file-item')).find(e => e.dataset.path === path) || null;
}

function renderCaseList() {
  const items = (_histologyCases || []).map(c => ({
    path: c.case_dir,
    name: `${c.case_name}  ·  ${c.overview_name}`,
  }));
  buildFileList('caseList', items, el => selectCase(el, el.dataset.path));
  if (_selectedCase) {
    const el = getCaseElementByPath(_selectedCase);
    if (el) el.classList.add('active');
  }
}

function scanProject() {
  const folder = document.getElementById('projectPath').value.trim();
  if (!folder) {
    setStatus('status', 'Choose a histology case folder first', 'error');
    return;
  }
  if (folder.toLowerCase().endsWith('.qpproj')) {
    setStatus('status', 'The case folder field needs a folder; choose .qpproj in the QuPath field', 'error');
    return;
  }
  _selectedCase = null;
  document.getElementById('newName').value = '';
  setStatus('status', 'Scanning project…', 'loading');
  btnBusy('btnScan', true, 'Loading…');
  api('/api/histology/browse', {folder}).then(d => {
    btnBusy('btnScan', false, 'Load Case Folder');
    if (d.error) throw new Error(d.error);
    _histologyCases = d.cases || [];
    renderCaseList();
    setStatus('status', `${_histologyCases.length} case(s) found`, 'ok');
    document.getElementById('histologyMeta').textContent = `Loaded ${_histologyCases.length} case(s) from ${folder}`;
  }).catch(e => {
    btnBusy('btnScan', false, 'Load Case Folder');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function selectCase(el, casePath) {
  document.querySelectorAll('#caseList .file-item').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  _selectedCase = casePath;
  setStatus('status', 'Loading preview…', 'loading');
  api('/api/histology/preview', {case_path: casePath, rotate_deg: getRotateDeg(), do_ocr: true}).then(d => {
    if (d.error) throw new Error(d.error);
    document.getElementById('newName').value = '';
    document.getElementById('histologyMeta').textContent = `${d.case_name} · ${d.qupath_name || 'no QuPath name found'}`;
    document.getElementById('mainSource').textContent = d.main_source ? `source: ${d.main_source}` : '';
    document.getElementById('labelSource').textContent = d.label_source ? `source: ${d.label_source}` : '';
    setPreview('mainPreview', d.main_b64);
    setPreview('labelPreview', d.label_b64);
    const notes = (d.notes || []).length ? d.notes.join('\n') : 'No preview notes.';
    document.getElementById('notesArea').textContent = notes;
    const hasAny = !!(d.main_b64 || d.label_b64);
    setStatus('status', hasAny ? 'Preview loaded' : 'No preview available (see Notes)', hasAny ? 'ok' : 'error');
  }).catch(e => {
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function setPreview(containerId, b64) {
  const el = document.getElementById(containerId);
  if (!b64) {
    el.innerHTML = '<div class="plot-placeholder">Preview not available</div>';
    return;
  }
  el.innerHTML = `<img src="data:image/png;base64,${b64}" alt="preview" style="max-width:100%;max-height:100%;object-fit:contain;display:block;"/>`;
}

function histologyProjectRoot() {
  return document.getElementById('projectPath').value.trim();
}

function syncQuPathNames() {
  if (!_histologyCases || !_histologyCases.length) {
    setStatus('status', 'Load a project first', 'error');
    return;
  }
  if (!document.getElementById('updateQuPath').checked) {
    setStatus('status', 'Enable “Update QuPath project name” first', 'error');
    return;
  }
  const qp = (document.getElementById('qupathProject').value || '').trim();
  if (!qp) {
    setStatus('status', 'Select a QuPath project.qpproj first', 'error');
    return;
  }

  btnBusy('btnSyncQuPath', true, 'Syncing…');
  setStatus('status', 'Syncing QuPath names…', 'loading');

  const cases = (_histologyCases || []).map(c => ({
    case_dir: c.case_dir,
    case_name: c.case_name,
  }));

  dpRunJobEndpoint('/api/histology/sync_qupath_names_job', {
    qupath_project: qp,
    cases,
    update_server_json: true,
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Syncing QuPath names${pct}${msg}`, 'loading');
    },
  }).then(d => {
    btnBusy('btnSyncQuPath', false, 'Sync QuPath Names');
    if (d.error) throw new Error(d.error);
    const n = parseInt(d.updated_images || '0', 10) || 0;
    setStatus('status', `Synced QuPath names (${n} updated)`, 'ok');
    toast(`Synced QuPath names (${n} updated). Reopen the QuPath project to refresh.`);
    const serverJson = [];
    (d.results || []).forEach(item => (item.updated_server_json || []).forEach(p => serverJson.push(p)));
    recordRunHistory({
      view: 'histology_naming',
      title: 'Histology QuPath Name Sync',
      status: 'ok',
      project_root: histologyProjectRoot(),
      input_files: cases.map(c => ({path: c.case_dir, role: 'case_folder'})),
      outputs: dpAsPathRecords((d.updated_projects || []).concat(serverJson), 'updated_project_metadata'),
      parameters: {
        qupath_project: qp,
        update_server_json: true,
        cases,
      },
      metadata: {
        updated_images: n,
        matched_images: d.matched_images || 0,
        unmatched_images: d.unmatched_images || 0,
        unmatched_cases: d.unmatched_cases || 0,
      },
    });
  }).catch(e => {
    btnBusy('btnSyncQuPath', false, 'Sync QuPath Names');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function applyRename() {
  if (!_selectedCase) {
    setStatus('status', 'Select a case first', 'error');
    return;
  }
  let newName = document.getElementById('newName').value.trim();
  if (!newName) {
    setStatus('status', 'Enter a new name', 'error');
    return;
  }

  const suffix = (document.getElementById('suffixPick').value || '').trim();
  const useSuffix = document.getElementById('useSuffix').checked;
  _writeBool('histology_use_suffix', useSuffix);
  if (useSuffix && suffix) {
    newName = newName.replace(/[_-]+$/g, '');
    const cleanSuffix = suffix.replace(/^[_-]+/g, '');
    if (cleanSuffix) newName = `${newName}_${cleanSuffix}`;
  }

  btnBusy('btnRename', true, 'Renaming…');
  setStatus('status', 'Renaming…', 'loading');
  const oldPath = _selectedCase;
  dpRunJobEndpoint('/api/histology/rename_job', {
    case_path: _selectedCase,
    new_name: newName,
    update_server_json: document.getElementById('updateQuPath').checked,
    qupath_project: (document.getElementById('qupathProject').value || '').trim(),
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Renaming${pct}${msg}`, 'loading');
    },
  }).then(d => {
    btnBusy('btnRename', false, 'Rename Folder');
    if (d.error) throw new Error(d.error);
    setStatus('status', `Renamed to ${d.new_name}`, 'ok');
    toast(`Renamed to ${d.new_name}`);
    recordRunHistory({
      view: 'histology_naming',
      title: 'Histology Case Rename',
      status: 'ok',
      project_root: histologyProjectRoot(),
      input_files: [{path: oldPath, role: 'old_case_folder'}],
      outputs: dpAsPathRecords(
        [d.new_path, d.rename_map].concat(d.updated_server_json || []).concat(d.updated_qupath_projects || []),
        'histology_rename_output'
      ),
      parameters: {
        old_path: oldPath,
        new_name: newName,
        update_server_json: document.getElementById('updateQuPath').checked,
        qupath_project: (document.getElementById('qupathProject').value || '').trim(),
      },
      metadata: {
        updated_server_json_count: (d.updated_server_json || []).length,
        updated_qupath_project_count: (d.updated_qupath_projects || []).length,
      },
    });

    const idx = (_histologyCases || []).findIndex(c => c.case_dir === oldPath);
    if (idx >= 0) {
      _histologyCases[idx].case_dir = d.new_path;
      _histologyCases[idx].case_name = d.new_name;
      if (_histologyCases[idx].overview_path && String(_histologyCases[idx].overview_path).startsWith(oldPath)) {
        _histologyCases[idx].overview_path = d.new_path + String(_histologyCases[idx].overview_path).slice(oldPath.length);
      }
    }
    _selectedCase = d.new_path;
    renderCaseList();
    const el = getCaseElementByPath(_selectedCase);
    if (el) selectCase(el, _selectedCase);
  }).catch(e => {
    btnBusy('btnRename', false, 'Rename Folder');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

window.addEventListener('load', () => {
  const rot = parseInt(localStorage.getItem('histology_rotate_deg') || '0', 10);
  document.getElementById('rotateDeg').value = [0,90,180,270].includes(rot) ? String(rot) : '0';
  let suffixList = localStorage.getItem('histology_suffix_list') || '';
  if (!suffixList) {
    const legacy = localStorage.getItem('histology_suffix_text') || '';
    if (legacy) suffixList = legacy;
  }
  document.getElementById('suffixList').value = suffixList;
  updateSuffixPick(localStorage.getItem('histology_suffix_pick') || '');
  document.getElementById('useSuffix').checked = _readBool('histology_use_suffix', false);
  setStatus('status', 'Ready', 'ok');
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  '_readBool',
  '_writeBool',
  'applyRename',
  'getCaseElementByPath',
  'getRotateDeg',
  'histologyProjectRoot',
  'onRotateChange',
  'onSuffixListChange',
  'onSuffixPickChange',
  'parseSuffixOptions',
  'renderCaseList',
  'scanProject',
  'selectCase',
  'setPreview',
  'syncQuPathNames',
  'updateSuffixPick',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
