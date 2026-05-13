async function pickFolderForSettings(inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  try {
    const d = await api('/api/system/select_folder', {start: input.value || ''});
    if (d.error) throw new Error(d.error);
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
    if (d.error) throw new Error(d.error);
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
    if (d.error) throw new Error(d.error);
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
    if (!defaultDir) return;
    if (!el.value || !el.value.trim()) el.value = defaultDir;
  });
}

window.addEventListener('load', () => {
  if (GENERIC_SETTINGS_ENABLED) {
    setTimeout(loadGenericPageDefaults, 80);
  }
});
