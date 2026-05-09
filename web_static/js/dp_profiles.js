/* Shared file-profile helpers for generic analysis pages. */

async function loadFileProfile(view, filePath, projectRoot, profileName) {
  const d = await api('/api/file_profiles/get', {
    view,
    file_path: filePath || '',
    project_root: projectRoot || '',
    profile_name: profileName || '',
  });
  if (d.error) throw new Error(d.error);
  return d;
}

async function saveFileProfile(view, filePath, projectRoot, profileName, settings, payload) {
  const d = await api('/api/file_profiles/save', {
    view,
    file_path: filePath || '',
    project_root: projectRoot || '',
    profile_name: profileName || 'default',
    settings: settings || {},
    payload: payload || {},
    make_last: true,
  });
  if (d.error) throw new Error(d.error);
  return d;
}

async function deleteFileProfile(view, filePath, projectRoot, profileName) {
  const d = await api('/api/file_profiles/delete', {
    view,
    file_path: filePath || '',
    project_root: projectRoot || '',
    profile_name: profileName || '',
  });
  if (d.error) throw new Error(d.error);
  return d;
}

function fileProfileOptionsHtml(profiles, selected) {
  const names = Object.keys(profiles || {}).sort((a, b) => a.localeCompare(b));
  if (selected && !names.includes(selected)) names.unshift(selected);
  if (!names.length) return '<option value="">No saved file profiles</option>';
  return names.map(name => `<option value="${dpEscapeHtml(name)}"${name === selected ? ' selected' : ''}>${dpEscapeHtml(name)}</option>`).join('');
}

function promptProfileName(currentName) {
  const name = window.prompt('Profile name', currentName || 'default');
  return name ? name.trim() : '';
}

function genericFileProfileCurrentPath() {
  if (typeof window.dpCurrentFilePath === 'function') return String(window.dpCurrentFilePath() || '').trim();
  return '';
}

function genericFileProfileProjectRoot() {
  if (typeof window.dpCurrentProjectRoot === 'function') return String(window.dpCurrentProjectRoot() || '').trim();
  const folder = document.getElementById('folderPath');
  if (folder && folder.value) return folder.value.trim();
  const baseDir = document.getElementById('baseDir');
  if (baseDir && baseDir.value) return baseDir.value.trim();
  return '';
}

function genericFileProfilePayload() {
  if (typeof window.dpCollectFileProfilePayload === 'function') return window.dpCollectFileProfilePayload() || {};
  return {};
}

function applyGenericFileProfilePayload(payload) {
  if (typeof window.dpApplyFileProfilePayload === 'function') window.dpApplyFileProfilePayload(payload || {});
}

function renderGenericFileProfileOptions(data) {
  _genericFileProfileState = data || null;
  const select = document.getElementById('genericFileProfileSelect');
  if (!select) return;
  if (!genericFileProfileCurrentPath()) {
    select.innerHTML = '<option value="">No file selected</option>';
    return;
  }
  const selected = data?.selected_profile || data?.last_profile || '';
  select.innerHTML = fileProfileOptionsHtml(data?.profiles || {}, selected);
}

async function loadGenericFileProfileForCurrent(auto) {
  if (!GENERIC_FILE_PROFILE_ENABLED) return null;
  const path = genericFileProfileCurrentPath();
  if (!path) {
    renderGenericFileProfileOptions(null);
    setStatus('genericFileProfileStatus', 'No file selected.', '');
    return null;
  }
  try {
    const data = await loadFileProfile(CURRENT_VIEW, path, genericFileProfileProjectRoot(), '');
    renderGenericFileProfileOptions(data);
    const shouldApply = !!data.profile && (!auto || document.getElementById('genericAutoLoadProfile')?.checked);
    if (shouldApply) {
      applyGenericPageDefaults(data.profile.settings || {});
      applyGenericFileProfilePayload(data.profile.payload || {});
      if (typeof window.dpAfterFileProfileApplied === 'function') window.dpAfterFileProfileApplied(auto);
      setStatus('genericFileProfileStatus', `Loaded file profile: ${data.selected_profile || data.last_profile || 'default'}`, data.stale ? 'warning' : 'ok');
    } else if (auto && document.getElementById('genericAutoLoadProfile')?.checked && !data.profile) {
      const created = await saveFileProfile(CURRENT_VIEW, path, genericFileProfileProjectRoot(), 'default', collectGenericPageDefaults(), genericFileProfilePayload());
      renderGenericFileProfileOptions(created);
      setStatus('genericFileProfileStatus', 'Created default file profile for this file.', 'ok');
    } else {
      setStatus('genericFileProfileStatus', 'No saved profile for this file yet.', '');
    }
    return data;
  } catch (e) {
    setStatus('genericFileProfileStatus', 'File profile not loaded: ' + e.message, 'warning');
    return null;
  }
}

async function loadSelectedGenericFileProfile(auto) {
  const path = genericFileProfileCurrentPath();
  if (!path) {
    setStatus('genericFileProfileStatus', 'Select a file first.', 'error');
    return;
  }
  const name = document.getElementById('genericFileProfileSelect')?.value || '';
  if (!name) {
    setStatus('genericFileProfileStatus', 'No profile selected.', 'error');
    return;
  }
  try {
    const data = await loadFileProfile(CURRENT_VIEW, path, genericFileProfileProjectRoot(), name);
    renderGenericFileProfileOptions(data);
    if (data.profile) {
      applyGenericPageDefaults(data.profile.settings || {});
      applyGenericFileProfilePayload(data.profile.payload || {});
      if (typeof window.dpAfterFileProfileApplied === 'function') window.dpAfterFileProfileApplied(auto);
      setStatus('genericFileProfileStatus', `Loaded file profile: ${name}`, data.stale ? 'warning' : 'ok');
    }
  } catch (e) {
    setStatus('genericFileProfileStatus', 'Load failed: ' + e.message, 'error');
  }
}

async function saveGenericFileProfile(saveAs) {
  const path = genericFileProfileCurrentPath();
  if (!path) {
    setStatus('genericFileProfileStatus', 'Select a file first.', 'error');
    return;
  }
  let name = document.getElementById('genericFileProfileSelect')?.value || 'default';
  if (saveAs) {
    name = promptProfileName(name);
    if (!name) return;
  }
  try {
    const data = await saveFileProfile(CURRENT_VIEW, path, genericFileProfileProjectRoot(), name, collectGenericPageDefaults(), genericFileProfilePayload());
    renderGenericFileProfileOptions(data);
    setStatus('genericFileProfileStatus', `Saved file profile: ${name}`, 'ok');
    toast('File profile saved');
  } catch (e) {
    setStatus('genericFileProfileStatus', 'Save failed: ' + e.message, 'error');
  }
}

async function deleteSelectedGenericFileProfile() {
  const path = genericFileProfileCurrentPath();
  const name = document.getElementById('genericFileProfileSelect')?.value || '';
  if (!path || !name || !confirm(`Delete file profile "${name}"?`)) return;
  try {
    const data = await deleteFileProfile(CURRENT_VIEW, path, genericFileProfileProjectRoot(), name);
    renderGenericFileProfileOptions(data);
    setStatus('genericFileProfileStatus', `Deleted file profile: ${name}`, 'ok');
  } catch (e) {
    setStatus('genericFileProfileStatus', 'Delete failed: ' + e.message, 'error');
  }
}
