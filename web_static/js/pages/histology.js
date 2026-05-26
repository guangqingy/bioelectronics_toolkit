let _histologyCases = [];
let _selectedCase = null;
let _histologyDataProject = null;
let _histologyProjectEntries = [];
let _selectedHistologyProjectEntryId = '';
let _histologyProjectPreviewSeq = 0;

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
  const projectEntry = _histologyProjectEntries.find(e => String(e.entry_id) === String(_selectedHistologyProjectEntryId));
  if (projectEntry) {
    loadHistologyProjectEntryPreview(projectEntry);
  } else if (_selectedCase) {
    const el = getCaseElementByPath(_selectedCase);
    if (el) selectCase(el, _selectedCase);
  }
}

function getCaseElementByPath(path) {
  return Array.from(document.querySelectorAll('#caseList .file-item')).find(e => e.dataset.path === path) || null;
}

function histologyDataProjectPath() {
  return (document.getElementById('histologyProjectPath')?.value || '').trim();
}

function renderHistologyProjectImageList() {
  const el = document.getElementById('histologyProjectImageList');
  if (!el) return;
  if (!_histologyProjectEntries.length) {
    el.innerHTML = '<div class="file-list-empty">No project entries yet</div>';
    return;
  }
  el.innerHTML = _histologyProjectEntries.map(entry => {
    const active = String(entry.entry_id) === String(_selectedHistologyProjectEntryId) ? ' active' : '';
    const counts = `${entry.roi_count || 0} ROI · ${entry.analysis_count || 0} analyses`;
    const missing = entry.exists ? '' : ' · missing source';
    const assoc = entry.associated_file_count ? ` · ${entry.associated_file_count} associated` : '';
    const detail = [entry.case_name || '', entry.source_name || entry.image_path || '']
      .filter(Boolean).join(' · ');
    return `
      <div class="file-item${active}" data-entry-id="${escHtml(entry.entry_id)}" onclick="DP.page.selectHistologyProjectEntry('${escHtml(entry.entry_id)}')">
        <div class="histology-file-title">${escHtml(entry.image_name || entry.entry_id)}</div>
        <div class="histology-file-subline">${escHtml(counts + assoc + missing)}</div>
        <div class="histology-file-path">${escHtml(detail)}</div>
      </div>`;
  }).join('');
}

function applyHistologyProjectPayload(d) {
  _histologyDataProject = d;
  _histologyProjectEntries = Array.isArray(d.entries) ? d.entries : [];
  if (d.project_path) document.getElementById('histologyProjectPath').value = d.project_path;
  if (_selectedHistologyProjectEntryId && !_histologyProjectEntries.some(e => String(e.entry_id) === String(_selectedHistologyProjectEntryId))) {
    _selectedHistologyProjectEntryId = '';
  }
  renderHistologyProjectImageList();
  if (_selectedHistologyProjectEntryId) {
    selectHistologyProjectEntry(_selectedHistologyProjectEntryId);
  } else if (_histologyProjectEntries.length) {
    selectHistologyProjectEntry(_histologyProjectEntries[0].entry_id);
  }
  document.getElementById('histologyMeta').textContent =
    `Project ${d.project_name || ''} · ${_histologyProjectEntries.length} image(s)`;
}

function createHistologyDataProject() {
  const projectPath = histologyDataProjectPath();
  if (!projectPath) {
    setStatus('status', 'Choose a project file or folder first', 'error');
    return;
  }
  btnBusy('btnCreateHistologyProject', true, 'Loading…');
  setStatus('status', 'Creating/loading histology project…', 'loading');
  api('/api/histology/project/create', {project_path: projectPath}).then(d => {
    btnBusy('btnCreateHistologyProject', false, 'Create / Load File');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    const cacheText = d.cache_dir ? ` · cache ${d.cache_dir}` : '';
    setStatus('status', `Project file ready: ${d.project_path || projectPath}${cacheText}`, 'ok');
    toast('Histology project file ready');
  }).catch(e => {
    btnBusy('btnCreateHistologyProject', false, 'Create / Load File');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function loadHistologyDataProject() {
  const projectPath = histologyDataProjectPath();
  if (!projectPath) {
    setStatus('status', 'Choose a histology project first', 'error');
    return;
  }
  btnBusy('btnCreateHistologyProject', true, 'Loading…');
  setStatus('status', 'Loading histology project…', 'loading');
  api('/api/histology/project/load', {project_path: projectPath}).then(d => {
    btnBusy('btnCreateHistologyProject', false, 'Create / Load File');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    setStatus('status', `Loaded project (${d.entry_count || 0} image(s))`, 'ok');
  }).catch(e => {
    btnBusy('btnCreateHistologyProject', false, 'Create / Load File');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function addHistologyDataProjectPath() {
  const projectPath = histologyDataProjectPath();
  const addPath = (document.getElementById('histologyProjectAddPath')?.value || '').trim();
  if (!projectPath) {
    setStatus('status', 'Create or choose a histology project first', 'error');
    return;
  }
  if (!addPath) {
    setStatus('status', 'Choose an ETS/TIFF file or folder to add', 'error');
    return;
  }
  btnBusy('btnAddHistologyProjectPath', true, 'Adding…');
  setStatus('status', 'Adding ETS references to project…', 'loading');
  api('/api/histology/project/add_paths', {project_path: projectPath, paths: [addPath]}).then(d => {
    btnBusy('btnAddHistologyProjectPath', false, 'Add ETS To Project');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    const skipped = d.skipped_count ? ` · ${d.skipped_count} already in project` : '';
    const warningText = Array.isArray(d.warnings) && d.warnings.length ? ` · ${d.warnings[0]}` : '';
    setStatus('status', `Added ${d.added_count || 0} ETS image(s)${skipped}${warningText}`, 'ok');
    toast(`Added ${d.added_count || 0} ETS image(s) to project`);
    recordRunHistory({
      view: 'histology_naming',
      title: 'Histology Project Add Images',
      status: 'ok',
      project_root: d.project_path || projectPath,
      input_files: [{path: addPath, role: 'histology_source_path'}],
      outputs: dpAsPathRecords([d.project_path, d.cache_dir], 'histology_project'),
      metadata: {added_count: d.added_count || 0, skipped_count: d.skipped_count || 0},
    });
  }).catch(e => {
    btnBusy('btnAddHistologyProjectPath', false, 'Add ETS To Project');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function histologyProjectAssociatedFiles(entry) {
  return Array.isArray(entry?.associated_files) ? entry.associated_files : [];
}

function histologyProjectAssociatedPath(entry, role) {
  const directKey = role === 'overview_vsi' ? 'overview_vsi_path' : 'label_vsi_path';
  const direct = String(entry?.[directKey] || '').trim();
  if (direct) return direct;
  const match = histologyProjectAssociatedFiles(entry).find(item => String(item?.role || '') === role);
  return String(match?.path || '').trim();
}

function histologyProjectPreviewPath(entry) {
  return (
    histologyProjectAssociatedPath(entry, 'overview_vsi') ||
    histologyProjectAssociatedPath(entry, 'label_vsi') ||
    String(entry?.image_path || entry?.source_path || '').trim()
  );
}

function setPreviewPlaceholder(containerId, message) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = `<div class="plot-placeholder">${escHtml(message || 'Preview not available')}</div>`;
}

function renderHistologyProjectEntryNotes(entry, extraNotes) {
  const notesEl = document.getElementById('notesArea');
  if (!notesEl || !entry) return;
  const associated = histologyProjectAssociatedFiles(entry);
  const associatedText = associated.length
    ? `\nAssociated:\n${associated.map(item => `- ${item.role || 'file'}: ${item.path || item.name || ''}`).join('\n')}`
    : '';
  const previewPath = histologyProjectPreviewPath(entry);
  const previewText = previewPath ? `\nPreview source: ${previewPath}` : '';
  const cacheText = _histologyDataProject?.cache_dir ? `\nCache: ${_histologyDataProject.cache_dir}` : '';
  const previewNotes = Array.isArray(extraNotes) && extraNotes.length
    ? `\nPreview notes:\n${extraNotes.join('\n')}`
    : '';
  notesEl.textContent =
    `Project entry: ${entry.image_name || entry.entry_id}\nSource: ${entry.image_path || ''}\nROI: ${entry.roi_count || 0}\nAnalyses: ${entry.analysis_count || 0}${associatedText}${previewText}${previewNotes}${cacheText}`;
}

function loadHistologyProjectEntryPreview(entry) {
  const previewPath = histologyProjectPreviewPath(entry);
  const labelPath = histologyProjectAssociatedPath(entry, 'label_vsi');
  const overviewPath = histologyProjectAssociatedPath(entry, 'overview_vsi');
  const seq = ++_histologyProjectPreviewSeq;
  document.getElementById('mainSource').textContent = overviewPath ? `source: ${overviewPath}` : '';
  document.getElementById('labelSource').textContent = labelPath ? `source: ${labelPath}` : '';
  if (!previewPath) {
    setPreviewPlaceholder('mainPreview', 'No preview source recorded for this project entry');
    setPreviewPlaceholder('labelPreview', 'No associated label image recorded');
    renderHistologyProjectEntryNotes(entry, ['No preview source recorded for this project entry.']);
    setStatus('status', 'No preview source recorded for this project entry', 'error');
    return;
  }
  setPreviewPlaceholder('mainPreview', 'Loading project preview...');
  setPreviewPlaceholder('labelPreview', 'Loading associated preview...');
  setStatus('status', 'Loading project entry preview...', 'loading');
  const mainRequest = api('/api/histology/preview', {
    case_path: previewPath,
    rotate_deg: getRotateDeg(),
    do_ocr: false,
  });
  const labelRequest = (labelPath && labelPath !== previewPath)
    ? api('/api/histology/preview', {
        case_path: labelPath,
        rotate_deg: getRotateDeg(),
        do_ocr: false,
      }).catch(e => ({error: e.message || String(e)}))
    : Promise.resolve(null);
  Promise.all([mainRequest, labelRequest]).then(([d, labelD]) => {
    if (seq !== _histologyProjectPreviewSeq) return;
    if (d.error) throw new Error(d.error);
    const labelB64 = d.label_b64 || labelD?.main_b64 || labelD?.label_b64 || '';
    const hasAny = !!(d.main_b64 || labelB64);
    const previewNotes = [];
    if (Array.isArray(d.notes)) previewNotes.push(...d.notes);
    if (labelD?.error) previewNotes.push(`Label preview: ${labelD.error}`);
    if (Array.isArray(labelD?.notes)) previewNotes.push(...labelD.notes.map(note => `Label preview: ${note}`));
    document.getElementById('mainSource').textContent =
      d.main_source ? `source: ${d.main_source}` : (overviewPath ? `source: ${overviewPath}` : '');
    document.getElementById('labelSource').textContent =
      d.label_source ? `source: ${d.label_source}` : (
        labelD?.main_source ? `source: ${labelD.main_source}` : (
          labelD?.label_source ? `source: ${labelD.label_source}` : (labelPath ? `source: ${labelPath}` : '')
        )
      );
    setPreview('mainPreview', d.main_b64);
    setPreview('labelPreview', labelB64);
    renderHistologyProjectEntryNotes(entry, previewNotes);
    setStatus('status', hasAny ? 'Project entry preview loaded' : 'No preview image available (see Notes)', hasAny ? 'ok' : 'error');
  }).catch(e => {
    if (seq !== _histologyProjectPreviewSeq) return;
    setPreviewPlaceholder('mainPreview', 'Preview not available');
    setPreviewPlaceholder('labelPreview', 'Associated preview not available');
    renderHistologyProjectEntryNotes(entry, [e.message]);
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function selectHistologyProjectEntry(entryId) {
  _selectedHistologyProjectEntryId = String(entryId || '');
  const entry = _histologyProjectEntries.find(e => String(e.entry_id) === _selectedHistologyProjectEntryId);
  _selectedCase = null;
  document.querySelectorAll('#caseList .file-item').forEach(e => e.classList.remove('active'));
  renderHistologyProjectImageList();
  document.getElementById('histologyProjectEntryName').value = entry ? (entry.image_name || '') : '';
  if (entry) {
    renderHistologyProjectEntryNotes(entry);
    loadHistologyProjectEntryPreview(entry);
  }
}

function renameHistologyDataProjectEntry() {
  const projectPath = histologyDataProjectPath();
  const entryId = _selectedHistologyProjectEntryId;
  const displayName = (document.getElementById('histologyProjectEntryName')?.value || '').trim();
  if (!projectPath) {
    setStatus('status', 'Choose a histology project first', 'error');
    return;
  }
  if (!entryId) {
    setStatus('status', 'Select a project entry first', 'error');
    return;
  }
  if (!displayName) {
    setStatus('status', 'Enter a project display name', 'error');
    return;
  }
  btnBusy('btnRenameHistologyProjectEntry', true, 'Renaming…');
  setStatus('status', 'Renaming project entry…', 'loading');
  api('/api/histology/project/rename_entry', {
    project_path: projectPath,
    entry_id: entryId,
    display_name: displayName,
  }).then(d => {
    btnBusy('btnRenameHistologyProjectEntry', false, 'Rename In Project');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    _selectedHistologyProjectEntryId = entryId;
    selectHistologyProjectEntry(entryId);
    setStatus('status', `Project entry renamed to ${displayName}`, 'ok');
    toast('Project entry renamed');
    recordRunHistory({
      view: 'histology_naming',
      title: 'Histology Project Entry Rename',
      status: 'ok',
      project_root: d.project_path || projectPath,
      input_files: [{path: d.renamed_entry?.image_path || '', role: 'histology_project_image'}],
      outputs: dpAsPathRecords([d.project_path, d.cache_dir], 'histology_project'),
      parameters: {entry_id: entryId, display_name: displayName},
    });
  }).catch(e => {
    btnBusy('btnRenameHistologyProjectEntry', false, 'Rename In Project');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
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
    setStatus('status', 'The case folder field needs a folder; choose .qpproj in the optional QuPath field', 'error');
    return;
  }
  _selectedCase = null;
  document.getElementById('newName').value = '';
  setStatus('status', 'Scanning case folder…', 'loading');
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
  _histologyProjectPreviewSeq += 1;
  _selectedHistologyProjectEntryId = '';
  renderHistologyProjectImageList();
  const projectEntryName = document.getElementById('histologyProjectEntryName');
  if (projectEntryName) projectEntryName.value = '';
  document.querySelectorAll('#caseList .file-item').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  _selectedCase = casePath;
  setStatus('status', 'Loading preview…', 'loading');
  api('/api/histology/preview', {case_path: casePath, rotate_deg: getRotateDeg(), do_ocr: true}).then(d => {
    if (d.error) throw new Error(d.error);
    document.getElementById('newName').value = '';
    document.getElementById('histologyMeta').textContent = `${d.case_name} · ${d.qupath_name || 'optional QuPath name not found'}`;
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
  el.innerHTML = `<img src="data:image/png;base64,${b64}" alt="preview" class="histology-preview-img"/>`;
}

function histologyProjectRoot() {
  return histologyDataProjectPath() || document.getElementById('projectPath').value.trim();
}

function syncQuPathNames() {
  if (!_histologyCases || !_histologyCases.length) {
    setStatus('status', 'Load a case folder first', 'error');
    return;
  }
  if (!document.getElementById('updateQuPath').checked) {
    setStatus('status', 'Enable “Also update QuPath display names” first', 'error');
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
  renderHistologyProjectImageList();
  setStatus('status', 'Ready', 'ok');
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  '_readBool',
  '_writeBool',
  'addHistologyDataProjectPath',
  'applyRename',
  'applyHistologyProjectPayload',
  'createHistologyDataProject',
  'getCaseElementByPath',
  'getRotateDeg',
  'histologyDataProjectPath',
  'histologyProjectAssociatedFiles',
  'histologyProjectAssociatedPath',
  'histologyProjectPreviewPath',
  'histologyProjectRoot',
  'loadHistologyDataProject',
  'loadHistologyProjectEntryPreview',
  'onRotateChange',
  'onSuffixListChange',
  'onSuffixPickChange',
  'parseSuffixOptions',
  'renderCaseList',
  'renderHistologyProjectEntryNotes',
  'renderHistologyProjectImageList',
  'renameHistologyDataProjectEntry',
  'scanProject',
  'selectCase',
  'selectHistologyProjectEntry',
  'setPreview',
  'setPreviewPlaceholder',
  'syncQuPathNames',
  'updateSuffixPick',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
