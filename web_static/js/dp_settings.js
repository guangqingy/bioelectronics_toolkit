/* Shared GUI settings and page-default helpers. */

function ensurePrefsShape(prefs) {
  const out = prefs && typeof prefs === 'object' ? prefs : {};
  out.version = out.version || 1;
  out.global = out.global && typeof out.global === 'object' ? out.global : {};
  out.views = out.views && typeof out.views === 'object' ? out.views : {};
  return out;
}

async function loadPreferences() {
  const d = await api('/api/preferences/get', {});
  if (d.error) throw new Error(d.error);
  _prefsCache = ensurePrefsShape(d.prefs || {});
  _prefsPath = d.path || '';
  return _prefsCache;
}

async function savePreferences(prefs) {
  const d = await api('/api/preferences/save', {prefs: ensurePrefsShape(prefs)});
  if (d.error) throw new Error(d.error);
  _prefsCache = ensurePrefsShape(d.prefs || {});
  _prefsPath = d.path || '';
  return _prefsCache;
}

async function loadViewPreferences(view) {
  const d = await api('/api/preferences/view_get', {view});
  if (d.error) throw new Error(d.error);
  return d.data || {};
}

async function saveViewPreferences(view, data) {
  const d = await api('/api/preferences/view_save', {view, data});
  if (d.error) throw new Error(d.error);
  if (_prefsCache) {
    _prefsCache.views = _prefsCache.views || {};
    _prefsCache.views[view] = data;
  }
  return d.data || {};
}

function settingsViewLabel(view) {
  return SETTINGS_VIEW_LABELS[view] || view.replaceAll('_', ' ');
}

function settingFieldOptions(field) {
  const targetId = field.element || field.id;
  const el = targetId ? document.getElementById(targetId) : null;
  if (el && el.tagName === 'SELECT' && el.options.length) {
    return [...el.options].map(o => [o.value, o.textContent || o.value]);
  }
  return (field.options || []).map(o => Array.isArray(o) ? o : [o, o]);
}

function settingFieldTypeFromElement(el) {
  if (!el) return 'text';
  if (el.type === 'checkbox') return 'checkbox';
  if (el.tagName === 'SELECT') return 'select';
  if (el.tagName === 'TEXTAREA') return 'textarea';
  if (el.type === 'number' || el.type === 'range') return 'number';
  if (el.classList.contains('input-path') || /path|folder|dir/i.test(el.id || el.name || '')) return 'path';
  return 'text';
}

function guessSettingLabel(el) {
  if (!el) return 'Field';
  const direct = el.closest('label');
  if (direct) return direct.textContent.replace(/\s+/g, ' ').trim();
  const row = el.closest('.param-row, .input-row');
  const rowLabel = row ? row.querySelector('.param-label, .form-label') : null;
  if (rowLabel) return rowLabel.textContent.replace(/\s+/g, ' ').trim();
  const section = el.closest('.ctrl-section');
  const sectionLabel = section ? section.querySelector('.ctrl-label') : null;
  const key = el.id || el.name || 'field';
  const readable = key.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ');
  return sectionLabel ? `${sectionLabel.textContent.trim()} ${readable}` : readable;
}

function currentPageSettingElements() {
  const panel = document.getElementById('ctrlPanel');
  if (!panel) return [];
  return [...panel.querySelectorAll('input, select, textarea')].filter(el => {
    if (!el.id && !el.name) return false;
    if (el.closest('#prefsOverlay') || el.closest('#genericSettingsSection') || el.closest('#genericFileProfileSection')) return false;
    if (el.type === 'button' || el.type === 'submit' || el.type === 'file' || el.type === 'range') return false;
    if ((el.id || '').startsWith('prefs')) return false;
    return true;
  });
}

function settingFieldsForView(view) {
  const fields = (SETTINGS_SCHEMAS[view] || []).map(f => ({...f}));
  if (view === CURRENT_VIEW) {
    const known = new Set();
    fields.forEach(f => {
      if (f.id) known.add(f.id);
      if (f.element) known.add(f.element);
    });
    currentPageSettingElements().forEach(el => {
      const key = el.id || el.name;
      if (known.has(key)) return;
      known.add(key);
      fields.push({
        id: key,
        element: el.id || key,
        label: guessSettingLabel(el),
        type: settingFieldTypeFromElement(el),
      });
    });
  }
  return fields;
}

function getElementValueForSettings(el) {
  if (!el) return '';
  if (el.type === 'checkbox') return !!el.checked;
  if (el.type === 'radio') return el.checked ? el.value : undefined;
  if (el.tagName === 'SELECT' && el.multiple) return [...el.selectedOptions].map(o => o.value);
  return el.value ?? '';
}

function setElementValueForSettings(el, value) {
  if (!el) return;
  if (el.type === 'checkbox') {
    el.checked = !!value;
  } else if (el.type === 'radio') {
    el.checked = el.value === String(value);
  } else if (el.tagName === 'SELECT' && el.multiple && Array.isArray(value)) {
    [...el.options].forEach(o => { o.selected = value.includes(o.value); });
  } else {
    el.value = value ?? '';
  }
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
}

function viewDataForSettings(view) {
  if (!_prefsCache) return {};
  if (view === 'global') return _prefsCache.global || {};
  return (_prefsCache.views || {})[view] || {};
}

function viewDefaultsForSettings(view) {
  const data = viewDataForSettings(view);
  if (view === 'global') return data;
  if (data && data.defaults && typeof data.defaults === 'object' && !Array.isArray(data.defaults)) return data.defaults;
  return {};
}

function collectCurrentPageSettings(schema) {
  const out = {};
  (schema || settingFieldsForView(CURRENT_VIEW)).forEach(field => {
    if (field.section || !field.id) return;
    const targetId = field.element || field.id;
    const el = document.getElementById(targetId);
    const value = getElementValueForSettings(el);
    if (value !== undefined) out[field.id] = value;
  });
  currentPageSettingElements().forEach(el => {
    const key = el.id || el.name;
    if (Object.prototype.hasOwnProperty.call(out, key)) return;
    const value = getElementValueForSettings(el);
    if (value !== undefined) out[key] = value;
  });
  return out;
}

function renderPrefsGlobalFields() {
  if (!_prefsCache) return;
  const global = _prefsCache.global || {};
  const folder = document.getElementById('prefsGlobalDefaultFolder');
  const useEmpty = document.getElementById('prefsUseDefaultOnEmpty');
  if (folder) folder.value = global.default_folder || '';
  if (useEmpty) useEmpty.checked = global.use_default_folder_for_empty_paths !== false;
}

function collectPrefsGlobalFields() {
  _prefsCache.global = _prefsCache.global && typeof _prefsCache.global === 'object' ? _prefsCache.global : {};
  _prefsCache.global.default_folder = (document.getElementById('prefsGlobalDefaultFolder')?.value || '').trim();
  _prefsCache.global.use_default_folder_for_empty_paths = !!document.getElementById('prefsUseDefaultOnEmpty')?.checked;
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
    return `<label class="prefs-field prefs-field-check"><input type="checkbox" ${common}${value ? ' checked' : ''}><span>${label}</span></label>`;
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
  await loadPrefsModal();
}

function closePrefsModal() {
  document.getElementById('prefsOverlay').classList.remove('show');
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

async function pickFolderForSettings(inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  try {
    const d = await api('/api/system/select_folder', {start: input.value || ''});
    if (d && d.path) input.value = d.path;
  } catch (e) {
    toast('Folder picker failed: ' + e.message, true);
  }
}

function genericSettingElements() {
  const panel = document.getElementById('ctrlPanel');
  if (!panel || !GENERIC_SETTINGS_ENABLED) return [];
  return [...panel.querySelectorAll('input, select, textarea')].filter(el => {
    if (!el.id && !el.name) return false;
    if (el.closest('#genericSettingsSection')) return false;
    if (el.closest('#genericFileProfileSection')) return false;
    if (el.closest('#prefsOverlay')) return false;
    if (el.type === 'button' || el.type === 'submit' || el.type === 'file') return false;
    return true;
  });
}

function collectGenericPageDefaults() {
  const out = {};
  genericSettingElements().forEach(el => {
    const key = el.id || el.name;
    if (el.type === 'checkbox') {
      out[key] = !!el.checked;
    } else if (el.type === 'radio') {
      if (el.checked) out[key] = el.value;
    } else if (el.tagName === 'SELECT' && el.multiple) {
      out[key] = [...el.selectedOptions].map(o => o.value);
    } else {
      out[key] = el.value;
    }
  });
  return out;
}

function applyGenericPageDefaults(defaults) {
  if (!defaults || typeof defaults !== 'object') return;
  genericSettingElements().forEach(el => {
    const key = el.id || el.name;
    if (!Object.prototype.hasOwnProperty.call(defaults, key)) return;
    const value = defaults[key];
    if (el.type === 'checkbox') {
      el.checked = !!value;
    } else if (el.type === 'radio') {
      el.checked = el.value === String(value);
    } else if (el.tagName === 'SELECT' && el.multiple && Array.isArray(value)) {
      [...el.options].forEach(o => { o.selected = value.includes(o.value); });
    } else {
      el.value = value ?? '';
    }
  });
  document.dispatchEvent(new CustomEvent('dp:generic-settings-applied', {detail: {view: CURRENT_VIEW, defaults}}));
}

async function loadGenericPageDefaults() {
  if (!GENERIC_SETTINGS_ENABLED) return;
  try {
    _genericPrefs = await loadViewPreferences(CURRENT_VIEW);
    if (!_genericPrefs || typeof _genericPrefs !== 'object') _genericPrefs = {};
    if (_genericPrefs.defaults) {
      applyGenericPageDefaults(_genericPrefs.defaults);
      setStatus('genericPrefsStatus', 'Defaults loaded.', 'ok');
    } else {
      setStatus('genericPrefsStatus', 'No saved defaults.', '');
    }
  } catch (e) {
    setStatus('genericPrefsStatus', 'Defaults not loaded.', 'warning');
  }
}

async function saveGenericPageDefaults() {
  if (!GENERIC_SETTINGS_ENABLED) return;
  try {
    _genericPrefs = _genericPrefs && typeof _genericPrefs === 'object' ? _genericPrefs : {};
    _genericPrefs.defaults = collectGenericPageDefaults();
    _genericPrefs.updated_at = new Date().toISOString();
    await saveViewPreferences(CURRENT_VIEW, _genericPrefs);
    setStatus('genericPrefsStatus', 'Defaults saved.', 'ok');
    toast('Page defaults saved');
  } catch (e) {
    setStatus('genericPrefsStatus', e.message || 'Save failed', 'err');
    toast('Page defaults save failed', true);
  }
}

async function restoreGenericPageDefaults() {
  if (!GENERIC_SETTINGS_ENABLED) return;
  if (!_genericPrefs || !_genericPrefs.defaults) await loadGenericPageDefaults();
  if (_genericPrefs && _genericPrefs.defaults) {
    applyGenericPageDefaults(_genericPrefs.defaults);
    setStatus('genericPrefsStatus', 'Defaults restored.', 'ok');
  }
}

async function resetGenericPageDefaults() {
  if (!GENERIC_SETTINGS_ENABLED) return;
  try {
    _genericPrefs = _genericPrefs && typeof _genericPrefs === 'object' ? _genericPrefs : {};
    delete _genericPrefs.defaults;
    _genericPrefs.updated_at = new Date().toISOString();
    await saveViewPreferences(CURRENT_VIEW, _genericPrefs);
    setStatus('genericPrefsStatus', 'Defaults reset.', 'ok');
    toast('Page defaults reset');
  } catch (e) {
    setStatus('genericPrefsStatus', e.message || 'Reset failed', 'err');
  }
}

async function pickerStartForInput(input) {
  const current = (input && input.value ? input.value : '').trim();
  if (current) return current;
  try {
    if (!_prefsCache) await loadPreferences();
    const global = _prefsCache && _prefsCache.global ? _prefsCache.global : {};
    if (global.use_default_folder_for_empty_paths === false) return '';
    return global.default_folder || DEFAULT_DATA_DIR;
  } catch (e) {
    return DEFAULT_DATA_DIR;
  }
}

async function pickFolder(inputId, afterFn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  try {
    const d = await api('/api/system/select_folder', {start: await pickerStartForInput(input)});
    if (d && d.path) {
      input.value = d.path;
      if (afterFn && typeof window[afterFn] === 'function') window[afterFn]();
    }
  } catch (e) {
    toast('Folder picker failed: ' + e.message, true);
  }
}

async function pickFile(inputId, afterFn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  try {
    const d = await api('/api/system/select_file', {start: await pickerStartForInput(input)});
    if (d && d.path) {
      input.value = d.path;
      if (afterFn && typeof window[afterFn] === 'function') window[afterFn]();
    }
  } catch (e) {
    toast('File picker failed: ' + e.message, true);
  }
}

async function applyInitialPathDefaults() {
  let defaultDir = DEFAULT_DATA_DIR;
  try {
    if (!_prefsCache) await loadPreferences();
    const global = _prefsCache && _prefsCache.global ? _prefsCache.global : {};
    if (global.use_default_folder_for_empty_paths !== false && global.default_folder) {
      defaultDir = global.default_folder;
    }
  } catch (e) {
    defaultDir = DEFAULT_DATA_DIR;
  }
  document.querySelectorAll('input.input-path').forEach(el => {
    const key = `${el.id || ''} ${el.name || ''} ${el.placeholder || ''}`.toLowerCase();
    const looksLikePath = /(path|folder|directory|dir|file)/.test(key);
    const explicitlyNotPath = /(token|power|prefix|label|sequence)/.test(key);
    if (el.dataset.pathDefault === 'false' || !looksLikePath || explicitlyNotPath) return;
    if (!el.value || !el.value.trim()) el.value = defaultDir;
  });
}

window.addEventListener('load', () => {
  if (GENERIC_SETTINGS_ENABLED) {
    setTimeout(loadGenericPageDefaults, 80);
  }
});
