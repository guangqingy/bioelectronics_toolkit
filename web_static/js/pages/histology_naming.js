let _histologyDataProject = null;
let _histologyProjectEntries = [];
let _selectedHistologyProjectEntryId = '';
let _histologyProjectPreviewSeq = 0;

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

function histologyConvertEts() {
  return !!document.getElementById('histologyConvertEts')?.checked;
}

function histologySimpleEntryName(entry) {
  return String(entry?.sample_id || entry?.case_name || entry?.image_name || entry?.entry_id || '').trim();
}

function histologyIsNamingEntry(entry) {
  const name = histologySimpleEntryName(entry).toLowerCase();
  const files = histologyProjectChannelFiles(entry);
  const channels = Object.keys(files).map(k => k.toLowerCase());
  if (channels.length && channels.every(ch => ch.includes('overview') || ch.includes('label'))) return false;
  if (name.includes('_overview_stack') || name.includes('_label_stack')) return false;
  if (/(^|[_\-\s])tray\d+[_\-\s]*slide.*[_\-\s]stack\d+($|[_\-\s])/.test(name)) return false;
  return true;
}

function histologyEntryVsiPath(entry) {
  const direct = String(entry?.overview_vsi_path || entry?.label_vsi_path || '').trim();
  if (direct) return direct;
  const associated = Array.isArray(entry?.associated_files) ? entry.associated_files : [];
  const overview = associated.find(item => String(item.role || '').toLowerCase() === 'overview_vsi');
  const label = associated.find(item => String(item.role || '').toLowerCase() === 'label_vsi');
  return String((overview && overview.path) || (label && label.path) || '').trim();
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
    const title = histologySimpleEntryName(entry);
    const channels = Object.keys(histologyProjectChannelFiles(entry));
    const counts = channels.length ? `channels: ${channels.join(', ')}` : 'case folder';
    const missing = entry.exists ? '' : ' · missing source';
    const warnings = Array.isArray(entry.warnings) ? entry.warnings : [];
    const rebuild = warnings.some(item => String(item || '').includes('Legacy ETS conversion'))
      ? ' · channel rebuild needed'
      : '';
    const vsi = histologyEntryVsiPath(entry);
    const detail = vsi ? vsi.split(/[\\/]/).pop() : (entry.case_dir || entry.source_name || entry.image_path || '');
    return `
      <div class="file-item${active}" data-entry-id="${dpEscapeHtml(entry.entry_id)}" data-dp-click="DP.page.selectHistologyProjectEntry('${dpEscapeHtml(entry.entry_id)}')">
        <div class="histology-file-title">${dpEscapeHtml(title)}</div>
        <div class="histology-file-subline">${dpEscapeHtml(counts + missing + rebuild)}</div>
        <div class="histology-file-path">${dpEscapeHtml(detail)}</div>
      </div>`;
  }).join('');
}

function histologyEntriesFromScannedSamples(d) {
  return (Array.isArray(d.samples) ? d.samples : []).map(sample => {
    const imageFiles = sample.image_files || {};
    const firstPath = Object.values(imageFiles)[0] || '';
    const metadata = sample.metadata || {};
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
      raw_olympus_reference: sample.raw_olympus_reference || '',
      case_dir: metadata.case_dir || '',
      physical_rename_dir: metadata.physical_rename_dir || '',
      converted_from_ets: metadata.converted_from_ets || [],
      converted_tiff_paths: metadata.converted_tiff_paths || [],
      conversion_roles: metadata.conversion_roles || {},
      ets_conversion_count: metadata.ets_conversion_count || 0,
      associated_files: metadata.associated_files || [],
      label_vsi_path: metadata.label_vsi_path || '',
      overview_vsi_path: metadata.overview_vsi_path || '',
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
  const entries = Array.isArray(d.entries) ? d.entries : histologyEntriesFromScannedSamples(d);
  _histologyProjectEntries = entries.filter(histologyIsNamingEntry);
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
    `Project ${d.project_name || ''} · ${_histologyProjectEntries.length} case(s)`;
}

function scanHistologyTiffProject() {
  const exportedDir = histologyExportedTiffPath();
  if (!exportedDir) {
    setStatus('status', 'Choose a TIFF/ETS source file or folder first', 'error');
    return;
  }
  btnBusy('btnScanHistologyTiffProject', true, 'Scanning...');
  setStatus('status', 'Scanning source images and converting ETS when needed...', 'loading');
  dpRunJobEndpoint('/api/histology/project/scan_tiff_job', {
    exported_dir: exportedDir,
    raw_dir: histologyRawOlympusPath(),
    analysis_dir: histologyAnalysisPath(),
    convert_ets: histologyConvertEts(),
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Scanning histology source${pct}${msg}`, 'loading');
    },
  }).then(d => {
    btnBusy('btnScanHistologyTiffProject', false, 'Scan Source');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    const warnText = Array.isArray(d.warnings) && d.warnings.length ? ` · ${d.warnings[0]}` : '';
    const converted = d.ets_converted_file_count ? ` · ${d.ets_converted_file_count} ETS TIFF(s)` : '';
    setStatus('status', `Scanned ${d.sample_count || 0} case(s), ${d.image_count || 0} analysis image(s)${converted}${warnText}`, 'ok');
  }).catch(e => {
    btnBusy('btnScanHistologyTiffProject', false, 'Scan Source');
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
    setStatus('status', 'Choose a TIFF/ETS source file or folder first', 'error');
    return;
  }
  btnBusy('btnCreateHistologyProject', true, 'Creating...');
  setStatus('status', 'Creating analysis project and converting ETS when needed...', 'loading');
  dpRunJobEndpoint('/api/histology/project/create_from_tiff_job', {
    project_path: projectPath,
    exported_dir: exportedDir,
    raw_dir: histologyRawOlympusPath(),
    analysis_dir: histologyAnalysisPath(),
    convert_ets: histologyConvertEts(),
  }, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Creating histology project${pct}${msg}`, 'loading');
    },
  }).then(d => {
    btnBusy('btnCreateHistologyProject', false, 'Create Analysis Project');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    const rawText = d.raw_olympus_index_path ? ` · raw index ${d.raw_olympus_index_path}` : '';
    const converted = d.ets_converted_file_count ? ` · ${d.ets_converted_file_count} ETS TIFF(s)` : '';
    setStatus('status', `Created project (${d.entry_count || 0} case(s))${converted}${rawText}`, 'ok');
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
    btnBusy('btnLoadHistologyProject', false, 'Load Existing');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    setStatus('status', `Loaded project (${d.entry_count || 0} case(s))`, 'ok');
  }).catch(e => {
    btnBusy('btnLoadHistologyProject', false, 'Load Existing');
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function setPreviewPlaceholder(containerId, message) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = `<div class="plot-placeholder">${dpEscapeHtml(message || 'Preview not available')}</div>`;
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
  const converted = Array.isArray(entry.converted_from_ets) ? entry.converted_from_ets : [];
  const associated = Array.isArray(entry.associated_files) ? entry.associated_files : [];
  const vsiRows = associated.map(item => `
    <div class="file-item">
      <div class="histology-file-title">${dpEscapeHtml(item.role || 'vsi')}</div>
      <div class="histology-file-path">${dpEscapeHtml(item.path || item.name || '')}</div>
    </div>`).join('');
  const convertedRows = converted.length ? converted.map(item => `
    <div class="file-item">
      <div class="histology-file-title">${dpEscapeHtml(item.role || 'image')}</div>
      <div class="histology-file-path">${dpEscapeHtml(item.output_path || '')}</div>
    </div>`).join('') : Object.entries(channels).map(([channel, path]) => `
    <div class="file-item">
      <div class="histology-file-title">${dpEscapeHtml(channel)}</div>
      <div class="histology-file-path">${dpEscapeHtml(path)}</div>
    </div>`).join('');
  panel.innerHTML = `
    <div class="histology-channel-panel">
      <div class="histology-card-meta">VSI label files</div>
      <div class="file-list">${vsiRows || '<div class="file-list-empty">No VSI label file recorded</div>'}</div>
      <div class="histology-card-meta histology-stack-gap">Converted TIFF files</div>
      <div class="file-list">${convertedRows || '<div class="file-list-empty">No converted TIFF recorded</div>'}</div>
    </div>`;
}

function renderHistologyProjectEntryNotes(entry, extraNotes) {
  const notesEl = document.getElementById('notesArea');
  if (!notesEl || !entry) return;
  const channels = histologyProjectChannelFiles(entry);
  const channelText = Object.keys(channels).length
    ? `\nChannels:\n${Object.entries(channels).map(([channel, path]) => `- ${channel}: ${path}`).join('\n')}`
    : '';
  const vsiPath = histologyEntryVsiPath(entry);
  const vsiText = vsiPath ? `\nLabel VSI: ${vsiPath}` : '';
  const rawText = entry.raw_olympus_reference ? `\nRaw Olympus reference: ${entry.raw_olympus_reference}` : '';
  const converted = Array.isArray(entry.converted_from_ets) ? entry.converted_from_ets : [];
  const convertedText = converted.length
    ? `\nConverted ETS:\n${converted.map(item => `- ${item.role || 'image'}: ${item.output_path || ''}`).join('\n')}`
    : '';
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
    `Case: ${histologySimpleEntryName(entry)}${vsiText}${channelText}${rawText}${convertedText}${analysisText}${manifestText}${warnings}${previewNotes}${cacheText}`;
}

function loadHistologyProjectEntryPreview(entry) {
  const vsiPath = histologyEntryVsiPath(entry);
  const seq = ++_histologyProjectPreviewSeq;
  document.getElementById('mainSource').textContent = vsiPath ? `label: ${vsiPath}` : '';
  document.getElementById('labelSource').textContent = '';
  renderHistologyProjectChannelPanel(entry);
  if (!vsiPath) {
    setPreviewPlaceholder('mainPreview', 'No VSI label preview recorded for this case');
    renderHistologyProjectEntryNotes(entry, ['No VSI label preview recorded for this case.']);
    setStatus('status', 'No VSI label preview recorded for this case', 'error');
    return;
  }
  setPreviewPlaceholder('mainPreview', 'Loading label preview...');
  setStatus('status', 'Loading VSI label preview...', 'loading');

  api('/api/histology/label_preview', {
    overview_path: vsiPath,
    rotate_deg: 0,
    do_ocr: true,
    ocr_lang: 'eng',
  }).then(d => {
    if (seq !== _histologyProjectPreviewSeq) return;
    if (d.error) throw new Error(d.error);
    const img = d.label_b64 || '';
    const previewNotes = Array.isArray(d.notes) ? d.notes : [];
    document.getElementById('mainSource').textContent = d.label_source ? `label: ${d.label_source}` : `label: ${vsiPath}`;
    setPreview('mainPreview', img);
    renderHistologyProjectEntryNotes(entry, previewNotes);
    setStatus('status', img ? 'VSI label preview loaded' : 'No label preview available (see Notes)', img ? 'ok' : 'error');
  }).catch(e => {
    if (seq !== _histologyProjectPreviewSeq) return;
    setPreviewPlaceholder('mainPreview', 'Label preview not available');
    renderHistologyProjectEntryNotes(entry, [e.message]);
    setStatus('status', 'Error: ' + e.message, 'error');
  });
}

function selectHistologyProjectEntry(entryId) {
  _selectedHistologyProjectEntryId = String(entryId || '');
  const entry = _histologyProjectEntries.find(e => String(e.entry_id) === _selectedHistologyProjectEntryId);
  renderHistologyProjectImageList();
  document.getElementById('histologyProjectEntryName').value = entry ? histologySimpleEntryName(entry) : '';
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
    setStatus('status', 'Enter a case name', 'error');
    return;
  }
  btnBusy('btnRenameHistologyProjectEntry', true, 'Renaming...');
  setStatus('status', 'Renaming case...', 'loading');
  api('/api/histology/project/rename_entry', {
    project_path: projectPath,
    entry_id: entryId,
    display_name: displayName,
  }).then(d => {
    btnBusy('btnRenameHistologyProjectEntry', false, 'Rename Case');
    if (d.error) throw new Error(d.error);
    applyHistologyProjectPayload(d);
    _selectedHistologyProjectEntryId = entryId;
    selectHistologyProjectEntry(entryId);
    const physical = d.physical_rename && d.physical_rename.renamed ? ' · TIFF/folder renamed' : '';
    setStatus('status', `Case renamed to ${displayName}${physical}`, 'ok');
    toast('Case renamed');
    recordRunHistory({
      view: 'histology_naming',
      title: 'Histology Case Rename',
      status: 'ok',
      project_root: d.project_path || projectPath,
      input_files: [{path: d.renamed_entry?.image_path || '', role: 'histology_project_image'}],
      outputs: dpAsPathRecords([d.project_path, d.cache_dir], 'histology_project'),
      parameters: {entry_id: entryId, display_name: displayName},
    });
  }).catch(e => {
    btnBusy('btnRenameHistologyProjectEntry', false, 'Rename Case');
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
  renderHistologyProjectImageList();
  setStatus('status', 'Ready', 'ok');
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'applyHistologyProjectPayload',
  'createHistologyTiffProject',
  'histologyDataProjectPath',
  'histologyExportedTiffPath',
  'histologyProjectChannelFiles',
  'histologyProjectPrimaryImagePath',
  'histologyRawOlympusPath',
  'histologyAnalysisPath',
  'histologyConvertEts',
  'loadHistologyDataProject',
  'loadHistologyProjectEntryPreview',
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
