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
