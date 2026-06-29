/* Shared path, run-history, and manifest restore helpers. */

function dpPathDir(path) {
  const text = String(path || '').trim();
  if (!text) return '';
  const normalized = text.replaceAll('\\', '/');
  const idx = normalized.lastIndexOf('/');
  if (idx <= 0) return '';
  return text.slice(0, idx);
}

function dpFirstRecordPath(records) {
  if (!Array.isArray(records)) return '';
  for (const rec of records) {
    const path = typeof rec === 'string' ? rec : (rec && (rec.path || rec.output_path || rec.input_path));
    if (path) return String(path);
  }
  return '';
}

function dpAsPathRecords(paths, type) {
  if (!Array.isArray(paths)) return [];
  return paths
    .map(item => {
      if (!item) return null;
      if (typeof item === 'string') return {path: item, type: type || ''};
      if (typeof item === 'object') return Object.assign({type: type || item.type || ''}, item);
      return null;
    })
    .filter(Boolean);
}

function dpViewUrl(view) {
  return VIEW_URLS[view] || '/runs';
}

function dpPendingManifestKey(view) {
  return 'dpPendingRunManifest:' + (view || '');
}

function dpSnakeToCamel(key) {
  return String(key || '').replace(/_([a-zA-Z0-9])/g, (_, ch) => ch.toUpperCase());
}

function dpCamelToSnake(key) {
  return String(key || '').replace(/[A-Z]/g, ch => '_' + ch.toLowerCase());
}

function dpControlAliases(key) {
  const raw = String(key || '');
  const camel = dpSnakeToCamel(raw);
  const snake = dpCamelToSnake(raw);
  const aliases = new Set([raw, camel, snake]);
  const manual = {
    x_col: ['xCol'],
    y_col: ['yCol'],
    x_min: ['xMin'],
    x_max: ['xMax'],
    y_min: ['yMin'],
    y_max: ['yMax'],
    invert_signal: ['invertSignal'],
    i_ch: ['iCh'],
    v_ch: ['vCh'],
    bl_pre0: ['blPre0'],
    bl_pre1: ['blPre1'],
    base_dir: ['baseDir'],
    main_folder: ['mainFolder'],
    output_name: ['outputName'],
    x_lin_ranges: ['xLin'],
    x_log_ranges: ['xLog'],
    project_root: ['folderPath', 'projectPath', 'runProjectRoot'],
  };
  (manual[raw] || manual[camel] || []).forEach(x => aliases.add(x));
  return [...aliases];
}

function dpSetControlValue(id, value) {
  const el = document.getElementById(id);
  if (!el || value === undefined || value === null) return false;
  if (el.type === 'checkbox') {
    el.checked = !!value;
    try { el.dispatchEvent(new Event('change')); } catch (_) {}
    return true;
  }
  const text = Array.isArray(value) ? value.join(', ') : String(value);
  if (el.tagName === 'SELECT' && text && ![...el.options].some(o => o.value === text)) {
    const opt = document.createElement('option');
    opt.value = text;
    opt.textContent = text;
    el.appendChild(opt);
  }
  el.value = text;
  try { el.dispatchEvent(new Event('change')); } catch (_) {}
  return true;
}

function dpApplyObjectToControls(obj) {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return 0;
  let applied = 0;
  Object.entries(obj).forEach(([key, value]) => {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      applied += dpApplyObjectToControls(value);
      return;
    }
    for (const id of dpControlAliases(key)) {
      if (dpSetControlValue(id, value)) {
        applied += 1;
        break;
      }
    }
  });
  return applied;
}

function dpFirstManifestInput(manifest) {
  const records = manifest && Array.isArray(manifest.input_files) ? manifest.input_files : [];
  return dpFirstRecordPath(records);
}

function dpApplyRunManifestFallback(manifest) {
  const params = manifest?.parameters || {};
  const inputPath = dpFirstManifestInput(manifest);
  const root = manifest?.project_root || dpPathDir(inputPath);
  ['folderPath', 'dataDir', 'baseDir', 'mainFolder', 'projectPath'].forEach(id => {
    const el = document.getElementById(id);
    if (el && root) el.value = root;
  });
  let applied = dpApplyObjectToControls(params);
  if (params.settings) applied += dpApplyObjectToControls(params.settings);
  if (params.display) applied += dpApplyObjectToControls(params.display);
  if (params.export_options) applied += dpApplyObjectToControls(params.export_options);
  document.dispatchEvent(new CustomEvent('dp:run-manifest-applied', {detail: {manifest, applied}}));
  const msg = applied
    ? `Loaded ${applied} parameter(s) from run manifest.`
    : 'Run manifest loaded; no matching controls were found on this page.';
  const statusEl = document.getElementById('status') ? 'status' : (document.getElementById('prefsStatus') ? 'prefsStatus' : '');
  if (statusEl) setStatus(statusEl, msg, applied ? 'ok' : 'warning');
  toast(msg, !applied);
  return applied;
}

function dpOpenManifestInView(manifest, intent) {
  if (!manifest || !manifest.view) return;
  sessionStorage.setItem(dpPendingManifestKey(manifest.view), JSON.stringify({manifest, intent: intent || 'restore'}));
  window.location.href = dpViewUrl(manifest.view);
}

async function dpConsumePendingRunManifest() {
  const key = dpPendingManifestKey(CURRENT_VIEW);
  const raw = sessionStorage.getItem(key);
  if (!raw) return;
  sessionStorage.removeItem(key);
  let manifest = null;
  let intent = 'restore';
  try {
    const parsed = JSON.parse(raw);
    if (parsed && parsed.manifest && typeof parsed.manifest === 'object') {
      manifest = parsed.manifest;
      intent = parsed.intent || 'restore';
    } else {
      manifest = parsed;
    }
  } catch (e) {
    toast('Saved manifest could not be parsed', true);
    return;
  }
  window.dpPendingRunIntent = intent;
  if (typeof window.dpApplyRunManifest === 'function') {
    try {
      await window.dpApplyRunManifest(manifest);
      if (intent === 'rerun') toast('Parameters loaded. Review them, then run the export again.', false);
      return;
    } catch (e) {
      console.warn('Custom manifest apply failed:', e);
    }
  }
  dpApplyRunManifestFallback(manifest);
  if (intent === 'rerun') toast('Parameters loaded. Review them, then run the export again.', false);
}

function dpSelectedFileProfileName() {
  const ids = ['genericFileProfileSelect', 'tiffProfileSelect', 'roiFileProfileSelect', 'gifFileProfileSelect'];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el && el.value) return el.value;
  }
  return '';
}

function dpRunProjectRoot(entry) {
  if (entry && entry.project_root) return String(entry.project_root);
  if (typeof window.dpCurrentProjectRoot === 'function') {
    const root = String(window.dpCurrentProjectRoot() || '').trim();
    if (root) return root;
  }
  if (typeof genericFileProfileProjectRoot === 'function') {
    const root = String(genericFileProfileProjectRoot() || '').trim();
    if (root) return root;
  }
  for (const id of ['folderPath', 'dataDir', 'baseDir', 'mainFolder', 'outputDir']) {
    const el = document.getElementById(id);
    if (el && el.value) return el.value.trim();
  }
  const firstPath = dpFirstRecordPath(entry?.input_files) || dpFirstRecordPath(entry?.outputs);
  return dpPathDir(firstPath);
}

async function recordRunHistory(entry) {
  const payload = Object.assign(
    {view: CURRENT_VIEW || 'unknown', status: 'ok', profile_name: dpSelectedFileProfileName()},
    entry || {}
  );
  payload.project_root = dpRunProjectRoot(payload);
  payload.source = Object.assign(
    {url: window.location.pathname, title: document.title, user_agent: navigator.userAgent},
    payload.source || {}
  );
  try {
    const d = await api('/api/run_history/record', payload);
    if (d.error) {
      console.warn('Run history was not saved:', d.error);
      return null;
    }
    return d;
  } catch (e) {
    console.warn('Run history was not saved:', e);
    return null;
  }
}

function runHistoryRootForCurrentProject() {
  return dpRunProjectRoot({}) || '';
}

function renderRunHistoryList(runs) {
  const list = document.getElementById('runHistoryList');
  const details = document.getElementById('runHistoryDetails');
  if (!list) return;
  if (details) {
    details.textContent = '';
    details.style.display = 'none';
  }
  if (!Array.isArray(runs) || !runs.length) {
    list.innerHTML = '<div class="prefs-sub">No run manifests found for this project yet.</div>';
    return;
  }
  list.innerHTML = runs.slice(0, 20).map(run => `
    <div class="run-history-row" data-manifest="${dpEscapeHtml(run.manifest_path || '')}" data-dp-click="loadRunManifestDetails(this.dataset.manifest)">
      <div class="run-history-title">${dpEscapeHtml(run.title || run.view || 'Run')}</div>
      <div class="run-history-meta">${dpEscapeHtml(run.completed_at || '')} · ${dpEscapeHtml(run.status || '')} · ${Number(run.output_count || 0)} output file(s)</div>
    </div>
  `).join('');
}

async function loadRunHistoryForCurrentProject(silent) {
  const root = runHistoryRootForCurrentProject();
  const rootEl = document.getElementById('runHistoryRoot');
  if (rootEl) rootEl.textContent = root || 'Project folder not detected';
  if (!root) {
    renderRunHistoryList([]);
    if (!silent) setStatus('prefsStatus', 'Choose a project folder first.', 'warning');
    return;
  }
  try {
    const d = await api('/api/run_history/list', {project_root: root, limit: 50});
    if (d.error) throw new Error(d.error);
    if (rootEl) rootEl.textContent = d.history_path || root;
    renderRunHistoryList(d.runs || []);
    if (!silent) setStatus('prefsStatus', `Loaded ${Number((d.runs || []).length)} run record(s).`, 'ok');
  } catch (e) {
    renderRunHistoryList([]);
    if (!silent) setStatus('prefsStatus', 'Run history unavailable: ' + e.message, 'warning');
  }
}

async function loadRunManifestDetails(path) {
  if (!path) return;
  const details = document.getElementById('runHistoryDetails');
  try {
    const d = await api('/api/run_history/get', {manifest_path: path});
    if (d.error) throw new Error(d.error);
    if (details) {
      details.textContent = JSON.stringify(d.manifest || {}, null, 2);
      details.style.display = 'block';
    }
  } catch (e) {
    if (details) {
      details.textContent = 'Unable to load manifest: ' + e.message;
      details.style.display = 'block';
    }
  }
}
