function renderPrefsGlobalFields() {
  if (!_prefsCache) return;
  const global = _prefsCache.global || {};
  const folder = document.getElementById('prefsGlobalDefaultFolder');
  const useEmpty = document.getElementById('prefsUseDefaultOnEmpty');
  const telemetry = document.getElementById('prefsTelemetryEnabled');
  if (folder) folder.value = global.default_folder || '';
  if (useEmpty) useEmpty.checked = global.use_default_folder_for_empty_paths !== false;
  if (telemetry) telemetry.checked = global.telemetry_enabled === true;
}

function collectPrefsGlobalFields() {
  _prefsCache.global = _prefsCache.global && typeof _prefsCache.global === 'object' ? _prefsCache.global : {};
  _prefsCache.global.default_folder = (document.getElementById('prefsGlobalDefaultFolder')?.value || '').trim();
  _prefsCache.global.use_default_folder_for_empty_paths = !!document.getElementById('prefsUseDefaultOnEmpty')?.checked;
  _prefsCache.global.telemetry_enabled = document.getElementById('prefsTelemetryEnabled')?.checked === true;
  _prefsCache.global.updated_at = new Date().toISOString();
}

function renderSettingField(field, defaults) {
  if (field.section) return `<div class="prefs-form-section">${dpEscapeHtml(field.section)}</div>`;
  const key = field.id;
  const type = field.type || 'text';
  const value = defaults && Object.prototype.hasOwnProperty.call(defaults, key) ? defaults[key] : '';
  const label = dpEscapeHtml(field.label || key);
  const help = field.help ? `<div class="prefs-field-help">${dpEscapeHtml(field.help)}</div>` : '';
  const common = `data-pref-key="${dpEscapeHtml(key)}" data-pref-type="${dpEscapeHtml(type)}"`;
  if (type === 'checkbox') {
    return `<label class="prefs-field prefs-field-check"><input class="dp-check" type="checkbox" ${common}${value ? ' checked' : ''}><span>${label}</span></label>`;
  }
  if (type === 'textarea') {
    return `<label class="prefs-field"><span>${label}</span><textarea ${common} rows="4">${dpEscapeHtml(value)}</textarea>${help}</label>`;
  }
  if (type === 'select') {
    const options = settingFieldOptions(field);
    if (!options.length && (value === '' || value === undefined || value === null)) {
      return `<label class="prefs-field"><span>${label}</span><input type="text" ${common} value="">${help}</label>`;
    }
    const seen = new Set(options.map(o => String(o[0])));
    if (value !== '' && value !== undefined && value !== null && !seen.has(String(value))) options.unshift([String(value), String(value)]);
    const opts = options.map(([val, text]) =>
      `<option value="${dpEscapeHtml(val)}"${String(val) === String(value) ? ' selected' : ''}>${dpEscapeHtml(text)}</option>`
    ).join('');
    return `<label class="prefs-field"><span>${label}</span><select ${common}>${opts}</select>${help}</label>`;
  }
  const inputType = type === 'number' ? 'number' : 'text';
  const cls = type === 'path' ? ' class="input-path"' : '';
  return `<label class="prefs-field"><span>${label}</span><input type="${inputType}"${cls} ${common} value="${dpEscapeHtml(value)}">${help}</label>`;
}

function renderPrefsForm(view) {
  const form = document.getElementById('prefsVisualForm');
  const sub = document.getElementById('prefsFormSub');
  if (!form) return;
  const defaults = viewDefaultsForSettings(view);
  if (view === 'global') {
    form.innerHTML = '<div class="prefs-empty">Global defaults are edited on the left. Choose an interface to edit page parameters.</div>';
    if (sub) sub.textContent = 'These values affect every file and folder picker.';
    return;
  }
  const fields = settingFieldsForView(view);
  if (!fields.length) {
    form.innerHTML = '<div class="prefs-empty">No editable defaults are registered for this interface yet. Open that page and use Load From Page to capture its visible controls.</div>';
    if (sub) sub.textContent = 'Advanced JSON remains available below.';
    return;
  }
  form.innerHTML = fields.map(f => renderSettingField(f, defaults)).join('');
  if (sub) sub.textContent = `${settingsViewLabel(view)} defaults. Blank values stay blank unless that page has its own fallback.`;
}

function collectPrefsFormDefaults() {
  const out = {};
  document.querySelectorAll('#prefsVisualForm [data-pref-key]').forEach(el => {
    const key = el.dataset.prefKey;
    const type = el.dataset.prefType;
    if (!key) return;
    if (type === 'checkbox') out[key] = !!el.checked;
    else out[key] = el.value ?? '';
  });
  return out;
}

function fillPrefsForm(defaults) {
  document.querySelectorAll('#prefsVisualForm [data-pref-key]').forEach(el => {
    const key = el.dataset.prefKey;
    if (!Object.prototype.hasOwnProperty.call(defaults, key)) return;
    const value = defaults[key];
    if (el.dataset.prefType === 'checkbox') el.checked = !!value;
    else el.value = value ?? '';
  });
}

function syncPrefsJson() {
  if (!_prefsCache) return;
  const view = document.getElementById('prefsViewSelect').value || CURRENT_VIEW || 'index';
  const data = viewDataForSettings(view);
  document.getElementById('prefsJson').value = JSON.stringify(data, null, 2);
}

async function openPrefsModal() {
  document.getElementById('prefsOverlay').classList.add('show');
  openPrefsTab('defaults');
  await loadPrefsModal();
}

function closePrefsModal() {
  document.getElementById('prefsOverlay').classList.remove('show');
}

function openPrefsTab(tab) {
  const active = tab || 'defaults';
  document.querySelectorAll('.prefs-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.prefsTab === active);
  });
  document.querySelectorAll('[data-prefs-tab-panel]').forEach(panel => {
    panel.hidden = panel.dataset.prefsTabPanel !== active;
  });
}

async function loadPrefsModal() {
  try {
    const prefs = await loadPreferences();
    const views = new Set(SETTINGS_VIEW_ORDER);
    Object.keys(prefs.views || {}).forEach(v => views.add(v));
    views.add(CURRENT_VIEW || 'index');
    const selected = document.getElementById('prefsViewSelect').value || CURRENT_VIEW || 'index';
    const ordered = [...views].sort((a, b) => {
      const ia = SETTINGS_VIEW_ORDER.indexOf(a);
      const ib = SETTINGS_VIEW_ORDER.indexOf(b);
      if (ia !== -1 || ib !== -1) return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
      return settingsViewLabel(a).localeCompare(settingsViewLabel(b));
    });
    document.getElementById('prefsViewSelect').innerHTML = ordered.map(v =>
      `<option value="${dpEscapeHtml(v)}"${v === selected ? ' selected' : ''}>${dpEscapeHtml(settingsViewLabel(v))}</option>`
    ).join('');
    document.getElementById('prefsFilePath').textContent = _prefsPath || 'Settings file not loaded';
    renderPrefsGlobalFields();
    renderPrefsView();
    setStatus('prefsStatus', 'Loaded.', 'ok');
    loadRunHistoryForCurrentProject(true);
    if (typeof loadBackgroundJobs === 'function') loadBackgroundJobs(true);
  } catch (e) {
    setStatus('prefsStatus', e.message || 'Unable to load settings', 'err');
  }
}

function renderPrefsView() {
  if (!_prefsCache) return;
  const view = document.getElementById('prefsViewSelect').value || CURRENT_VIEW || 'index';
  renderPrefsGlobalFields();
  renderPrefsForm(view);
  syncPrefsJson();
}

async function savePrefsVisual() {
  try {
    if (!_prefsCache) await loadPreferences();
    const view = document.getElementById('prefsViewSelect').value || CURRENT_VIEW || 'index';
    collectPrefsGlobalFields();
    _prefsCache.views = _prefsCache.views || {};
    if (view !== 'global') {
      const data = _prefsCache.views[view] && typeof _prefsCache.views[view] === 'object' ? _prefsCache.views[view] : {};
      data.defaults = collectPrefsFormDefaults();
      data.updated_at = new Date().toISOString();
      _prefsCache.views[view] = data;
      if (view === CURRENT_VIEW && GENERIC_SETTINGS_ENABLED) _genericPrefs = data;
    }
    await savePreferences(_prefsCache);
    renderPrefsView();
    setStatus('prefsStatus', 'Saved.', 'ok');
    toast('GUI defaults saved');
    document.dispatchEvent(new CustomEvent('dp:prefs-saved', {detail: {view, data: viewDataForSettings(view)}}));
  } catch (e) {
    setStatus('prefsStatus', e.message || 'Unable to save settings', 'err');
    toast('Settings save failed', true);
  }
}

async function savePrefsJson() {
  try {
    if (!_prefsCache) await loadPreferences();
    const view = document.getElementById('prefsViewSelect').value || CURRENT_VIEW || 'index';
    const raw = document.getElementById('prefsJson').value;
    const data = raw.trim() ? JSON.parse(raw) : {};
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Settings must be a JSON object.');
    if (view === 'global') _prefsCache.global = data;
    else {
      _prefsCache.views = _prefsCache.views || {};
      _prefsCache.views[view] = data;
      if (view === CURRENT_VIEW && GENERIC_SETTINGS_ENABLED) _genericPrefs = data;
    }
    await savePreferences(_prefsCache);
    renderPrefsView();
    setStatus('prefsStatus', 'JSON saved.', 'ok');
    toast('Settings JSON saved');
    document.dispatchEvent(new CustomEvent('dp:prefs-saved', {detail: {view, data}}));
  } catch (e) {
    setStatus('prefsStatus', e.message || 'Unable to save JSON', 'err');
    toast('Settings JSON save failed', true);
  }
}

function loadCurrentPageIntoSettings() {
  const view = document.getElementById('prefsViewSelect').value || CURRENT_VIEW || 'index';
  if (view !== CURRENT_VIEW) {
    setStatus('prefsStatus', `Open ${settingsViewLabel(view)} before loading values from that page.`, 'warning');
    return;
  }
  const schema = settingFieldsForView(view);
  const defaults = collectCurrentPageSettings(schema);
  fillPrefsForm(defaults);
  const data = viewDataForSettings(view);
  const preview = {...data, defaults: {...(data.defaults || {}), ...defaults}};
  document.getElementById('prefsJson').value = JSON.stringify(preview, null, 2);
  setStatus('prefsStatus', 'Loaded current page values into the form. Press Save Defaults to keep them.', 'ok');
}

function applyPrefsToCurrentPage() {
  const view = document.getElementById('prefsViewSelect').value || CURRENT_VIEW || 'index';
  if (view !== CURRENT_VIEW) {
    setStatus('prefsStatus', `Switch to ${settingsViewLabel(view)} before applying these defaults.`, 'warning');
    return;
  }
  const defaults = collectPrefsFormDefaults();
  const schema = settingFieldsForView(view);
  const fieldsByKey = new Map(schema.filter(f => f.id).map(f => [f.id, f]));
  Object.entries(defaults).forEach(([key, value]) => {
    const field = fieldsByKey.get(key) || {id: key};
    const targetId = field.element || field.id;
    const el = document.getElementById(targetId);
    if (el) setElementValueForSettings(el, value);
  });
  setStatus('prefsStatus', 'Applied defaults to this page.', 'ok');
  toast('Defaults applied');
  document.dispatchEvent(new CustomEvent('dp:prefs-defaults-applied', {detail: {view, defaults}}));
}
