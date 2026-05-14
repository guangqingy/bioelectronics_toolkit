const LIF_VIEWER_FLAGS = window.LIF_VIEWER_FLAGS || {};
const HAS_READLIF = !!LIF_VIEWER_FLAGS.hasReadlif;
const HAS_PIL = !!LIF_VIEWER_FLAGS.hasPil;
const HAS_TIFF = !!LIF_VIEWER_FLAGS.hasTiff;

let _lifPath = '';
let _lifFiles = [];
let _lifRecords = [];
let _lifDisplayRecords = [];
let _lifRenameMap = {};
let _lifActiveIndex = null;
let _lifPreviewDebounce = null;

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/`/g, '&#96;');
}

function renameStorageKey(path) {
  return 'dp_lif_rename_v1:' + String(path || '');
}

function loadRenameMap(path) {
  try {
    const raw = localStorage.getItem(renameStorageKey(path));
    const parsed = raw ? JSON.parse(raw) : {};
    _lifRenameMap = (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) ? parsed : {};
  } catch {
    _lifRenameMap = {};
  }
}

function saveRenameMap() {
  if (!_lifPath) return;
  localStorage.setItem(renameStorageKey(_lifPath), JSON.stringify(_lifRenameMap));
}

function displayName(rec) {
  if (!rec) return '';
  const custom = _lifRenameMap[String(rec.index)];
  return (custom && String(custom).trim()) || rec.name || ('Subfile ' + (Number(rec.index) + 1));
}

function hasCustomName(rec) {
  return !!(rec && _lifRenameMap[String(rec.index)] && String(_lifRenameMap[String(rec.index)]).trim());
}

function planeCount(rec) {
  const planeDims = Array.isArray(rec.plane_dimensions) ? rec.plane_dimensions : [];
  let total = Math.max(1, Number(rec.channels || 1));
  if (planeDims.length) {
    planeDims.forEach(dim => { total *= Math.max(1, Number(dim.count || 1)); });
    return total;
  }
  const d = rec.dimensions || {};
  return total *
    Math.max(1, Number(d.t || 1)) *
    Math.max(1, Number(d.z || 1)) *
    Math.max(1, Number(d.m || 1));
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'displayName',
  'escapeAttr',
  'escapeHtml',
  'hasCustomName',
  'loadRenameMap',
  'planeCount',
  'renameStorageKey',
  'saveRenameMap',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
