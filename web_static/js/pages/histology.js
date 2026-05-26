let _histologyDataProject = null;
let _histologyProjectEntries = [];
let _selectedHistologyProjectEntryId = '';
let _histologyProjectPreviewSeq = 0;

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
  }
}

function histologyDataProjectPath() {
  return (document.getElementById('histologyProjectPath')?.value || '').trim();
}

function histologyExportedTiffPath() {
  return (document.getElementById('histologyExportedTiffPath')?.value || '').trim();
}

function histologyRawOlympusPath() {
  return (document.getElementById('histologyRawOlympusPath')?.value || '').trim();
}

function histologyAnalysisPath() {
  return (document.getElementById('histologyAnalysisPath')?.value || '').trim();
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
    const channelCount = entry.image_files ? Object.keys(entry.image_files).length : 0;
    const counts = channelCount
      ? `${channelCount} channel(s) · ${entry.roi_count || 0} ROI`
      : `${entry.roi_count || 0} ROI · ${entry.analysis_count || 0} analyses`;
    const missing = entry.exists ? '' : ' · missing source';
    const assoc = entry.associated_file_count ? ` · ${entry.associated_file_count} associated` : '';
    const detail = [entry.sample_id || entry.case_name || '', entry.source_name || entry.image_path || '']
      .filter(Boolean).join(' · ');
    return `
      <div class="file-item${active}" data-entry-id="${escHtml(entry.entry_id)}" onclick="DP.page.selectHistologyProjectEntry('${escHtml(entry.entry_id)}')">
        <div class="histology-file-title">${escHtml(entry.image_name || entry.entry_id)}</div>
        <div class="histology-file-subline">${escHtml(counts + assoc + missing)}</div>
        <div class="histology-file-path">${escHtml(detail)}</div>
      </div>`;
  }).join('');
}

function histologyEntriesFromScannedSamples(d) {
  return (Array.isArray(d.samples) ? d.samples : []).map(sample => {
    const imageFiles = sample.image_files || {};
    const firstPath = Object.values(imageFiles)[0] || '';
    return {
      entry_id: `scan_${sample.sample_id}`,
      record_type: 'sample',
      sample_id: sample.sample_id,
      image_name: sample.sample_id,
      display_name: sample.sample_id,
      source_name: firstPath ? firstPath.split(/[\\/]/).pop() : '',
      image_path: firstPath,
      source_path: firstPath,
      image_files: imageFiles,
      image_records: sample.images || [],
      analysis_folder: sample.analysis_folder || '',
      warnings: sample.warnings || [],
      exists: !!firstPath,
      roi_count: 0,
      analysis_count: 0,
    };
  });
}

function applyHistologyProjectPayload(d) {
  _histologyDataProject = d;
  _histologyProjectEntries = Array.isArray(d.entries) ? d.entries : histologyEntriesFromScannedSamples(d);
  if (d.project_path) document.getElementById('histologyProjectPath').value = d.project_path;
  if (d.exported_dir) document.getElementById('histologyExportedTiffPath').value = d.exported_dir;
  if (d.raw_dir) document.getElementById('histologyRawOlympusPath').value = d.raw_dir;
  if (d.analysis_dir) document.getElementById('histologyAnalysisPath').value = d.analysis_dir;
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

function scanHistologyTiffProject() {
  const exportedDir = histologyExportedTiffPath();
  if (!exportedDir) {
    setStatus('status', 'Choose exported TIFF files or a folder first', 'error');
    return;
  }
  btnBusy('btnScanHistologyTiffProject', true, 'Scanning…');
  setStatus('status', 'Scanning exported TIFF/images…', 'loading');
  api('/api/histology/project/scan_tiff', {
    exported_dir: exportedDir,
    raw_dir: histologyRawOlympusPath(),
    analysis_dir: histologyAnalysisPath(),
  }).then(d => {
    btnBusy('btnScanHistologyTiffProject', false, 'Scan');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    const warnText = Array.isArray(d.warnings) && d.warnings.length ? ` · ${d.warnings[0]}` : '';
    setStatus('status', `Scanned ${d.sample_count || 0} sample(s), ${d.image_count || 0} image(s)${warnText}`, 'ok');
  }).catch(e => {
    btnBusy('btnScanHistologyTiffProject', false, 'Scan');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function createHistologyTiffProject() {
  const projectPath = histologyDataProjectPath();
  const exportedDir = histologyExportedTiffPath();
  if (!projectPath) {
    setStatus('status', 'Choose a project file or save folder first', 'error');
    return;
  }
  if (!exportedDir) {
    setStatus('status', 'Choose exported TIFF files or a folder first', 'error');
    return;
  }
  btnBusy('btnCreateHistologyProject', true, 'Creating…');
  setStatus('status', 'Creating analysis project and manifests…', 'loading');
  api('/api/histology/project/create_from_tiff', {
    project_path: projectPath,
    exported_dir: exportedDir,
    raw_dir: histologyRawOlympusPath(),
    analysis_dir: histologyAnalysisPath(),
  }).then(d => {
    btnBusy('btnCreateHistologyProject', false, 'Create Analysis Project');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    const rawText = d.raw_olympus_index_path ? ` · raw index ${d.raw_olympus_index_path}` : '';
    setStatus('status', `Created project (${d.entry_count || 0} sample(s))${rawText}`, 'ok');
    toast('Histology analysis project created');
  }).catch(e => {
    btnBusy('btnCreateHistologyProject', false, 'Create Analysis Project');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function loadHistologyDataProject() {
  const projectPath = histologyDataProjectPath();
  if (!projectPath) {
    setStatus('status', 'Choose a histology project first', 'error');
    return;
  }
  btnBusy('btnLoadHistologyProject', true, 'Loading…');
  setStatus('status', 'Loading histology project…', 'loading');
  api('/api/histology/project/load', {project_path: projectPath}).then(d => {
    btnBusy('btnLoadHistologyProject', false, 'Load File');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    setStatus('status', `Loaded project (${d.entry_count || 0} image(s))`, 'ok');
  }).catch(e => {
    btnBusy('btnLoadHistologyProject', false, 'Load File');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function setPreviewPlaceholder(containerId, message) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = `<div class="plot-placeholder">${escHtml(message || 'Preview not available')}</div>`;
}

function histologyProjectChannelFiles(entry) {
  const raw = entry?.image_files || {};
  if (!raw || typeof raw !== 'object') return {};
  const out = {};
  Object.entries(raw).forEach(([channel, path]) => {
    const text = String(path || '').trim();
    if (text) out[channel] = text;
  });
  return out;
}

function histologyProjectPrimaryImagePath(entry) {
  const direct = String(entry?.image_path || entry?.source_path || '').trim();
  if (direct) return direct;
  const files = histologyProjectChannelFiles(entry);
  return Object.values(files)[0] || '';
}

function renderHistologyProjectChannelPanel(entry) {
  const panel = document.getElementById('labelPreview');
  if (!panel || !entry) return;
  const channels = histologyProjectChannelFiles(entry);
  const warnings = Array.isArray(entry.warnings) ? entry.warnings : [];
  const channelRows = Object.entries(channels).map(([channel, path]) => `
    <div class="file-item">
      <div class="histology-file-title">${escHtml(channel)}</div>
      <div class="histology-file-path">${escHtml(path)}</div>
    </div>`).join('');
  const warningRows = warnings.length
    ? `<div class="histology-warning-list">${warnings.map(item => `<div>${escHtml(item)}</div>`).join('')}</div>`
    : '<div class="histology-card-meta">No channel warnings recorded.</div>';
  panel.innerHTML = `
    <div class="histology-channel-panel">
      <div class="histology-card-meta">Exported image channels</div>
      <div class="file-list">${channelRows || '<div class="file-list-empty">No channel files recorded</div>'}</div>
      <div class="histology-card-meta histology-stack-gap">Warnings</div>
      ${warningRows}
    </div>`;
}

function renderHistologyProjectEntryNotes(entry, extraNotes) {
  const notesEl = document.getElementById('notesArea');
  if (!notesEl || !entry) return;
  const channels = histologyProjectChannelFiles(entry);
  const channelText = Object.keys(channels).length
    ? `\nChannels:\n${Object.entries(channels).map(([channel, path]) => `- ${channel}: ${path}`).join('\n')}`
    : '';
  const previewPath = histologyProjectPrimaryImagePath(entry);
  const previewText = previewPath ? `\nPreview source: ${previewPath}` : '';
  const rawText = entry.raw_olympus_reference ? `\nRaw Olympus reference: ${entry.raw_olympus_reference}` : '';
  const analysisText = entry.analysis_folder ? `\nAnalysis folder: ${entry.analysis_folder}` : '';
  const manifestText = entry.manifest_path ? `\nManifest: ${entry.manifest_path}` : '';
  const warnings = Array.isArray(entry.warnings) && entry.warnings.length
    ? `\nWarnings:\n${entry.warnings.join('\n')}`
    : '';
  const cacheText = _histologyDataProject?.cache_dir ? `\nCache: ${_histologyDataProject.cache_dir}` : '';
  const previewNotes = Array.isArray(extraNotes) && extraNotes.length
    ? `\nPreview notes:\n${extraNotes.join('\n')}`
    : '';
  notesEl.textContent =
    `Project entry: ${entry.image_name || entry.entry_id}\nSource: ${entry.image_path || ''}\nROI: ${entry.roi_count || 0}\nAnalyses: ${entry.analysis_count || 0}${channelText}${previewText}${rawText}${analysisText}${manifestText}${warnings}${previewNotes}${cacheText}`;
}

function loadHistologyProjectEntryPreview(entry) {
  const previewPath = histologyProjectPrimaryImagePath(entry);
  const projectPath = histologyDataProjectPath();
  const isScanOnly = String(entry?.entry_id || '').startsWith('scan_');
  const seq = ++_histologyProjectPreviewSeq;
  document.getElementById('mainSource').textContent = previewPath ? `source: ${previewPath}` : '';
  document.getElementById('labelSource').textContent = entry.raw_olympus_reference
    ? `raw index: ${entry.raw_olympus_reference}`
    : '';
  renderHistologyProjectChannelPanel(entry);
  if (!previewPath) {
    setPreviewPlaceholder('mainPreview', 'No preview source recorded for this project entry');
    renderHistologyProjectEntryNotes(entry, ['No preview source recorded for this project entry.']);
    setStatus('status', 'No preview source recorded for this project entry', 'error');
    return;
  }
  setPreviewPlaceholder('mainPreview', 'Loading image preview...');
  setStatus('status', 'Loading project entry preview...', 'loading');

  const request = (projectPath && !isScanOnly)
    ? api('/api/histology/project/image_preview', {
        project_path: projectPath,
        entry_id: entry.entry_id,
        max_side: 1600,
      })
    : api('/api/histology/file/image_preview', {
        image_path: previewPath,
        max_side: 1600,
      });

  request.then(d => {
    if (seq !== _histologyProjectPreviewSeq) return;
    if (d.error) throw new Error(d.error);
    const hasAny = !!d.img;
    const previewNotes = Array.isArray(d.warnings) ? d.warnings : [];
    document.getElementById('mainSource').textContent =
      d.image_path ? `source: ${d.image_path}` : (previewPath ? `source: ${previewPath}` : '');
    if (!isScanOnly && Array.isArray(d.warnings) && d.warnings.length) {
      entry.warnings = Array.from(new Set([...(entry.warnings || []), ...d.warnings]));
      renderHistologyProjectChannelPanel(entry);
    }
    setPreview('mainPreview', d.img);
    renderHistologyProjectEntryNotes(entry, previewNotes);
    setStatus('status', hasAny ? 'Project entry preview loaded' : 'No preview image available (see Notes)', hasAny ? 'ok' : 'error');
  }).catch(e => {
    if (seq !== _histologyProjectPreviewSeq) return;
    setPreviewPlaceholder('mainPreview', 'Preview not available');
    renderHistologyProjectEntryNotes(entry, [e.message]);
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function selectHistologyProjectEntry(entryId) {
  _selectedHistologyProjectEntryId = String(entryId || '');
  const entry = _histologyProjectEntries.find(e => String(e.entry_id) === _selectedHistologyProjectEntryId);
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

function setPreview(containerId, b64) {
  const el = document.getElementById(containerId);
  if (!b64) {
    el.innerHTML = '<div class="plot-placeholder">Preview not available</div>';
    return;
  }
  el.innerHTML = `<img src="data:image/png;base64,${b64}" alt="preview" class="histology-preview-img"/>`;
}

window.addEventListener('load', () => {
  const rot = parseInt(localStorage.getItem('histology_rotate_deg') || '0', 10);
  const rotateEl = document.getElementById('rotateDeg');
  if (rotateEl) rotateEl.value = [0,90,180,270].includes(rot) ? String(rot) : '0';
  renderHistologyProjectImageList();
  setStatus('status', 'Ready', 'ok');
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'applyHistologyProjectPayload',
  'createHistologyTiffProject',
  'getRotateDeg',
  'histologyDataProjectPath',
  'histologyExportedTiffPath',
  'histologyProjectChannelFiles',
  'histologyProjectPrimaryImagePath',
  'histologyRawOlympusPath',
  'histologyAnalysisPath',
  'loadHistologyDataProject',
  'loadHistologyProjectEntryPreview',
  'onRotateChange',
  'renderHistologyProjectEntryNotes',
  'renderHistologyProjectChannelPanel',
  'renderHistologyProjectImageList',
  'renameHistologyDataProjectEntry',
  'scanHistologyTiffProject',
  'selectHistologyProjectEntry',
  'setPreview',
  'setPreviewPlaceholder',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
