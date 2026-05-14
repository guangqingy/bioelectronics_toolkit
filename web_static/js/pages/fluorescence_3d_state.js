const FL3D_FLAGS = window.FL3D_FLAGS || {};
const HAS_TIFF = !!FL3D_FLAGS.hasTiff;
const HAS_PIL = !!FL3D_FLAGS.hasPil;

let _availableTiffs = [];
let _availableActiveIndex = -1;
let _currentInfo = null;
let _previewTimer = null;
let _threePromise = null;
let _renderer = null;
let _scene = null;
let _camera = null;
let _controls = null;
let _anim = null;
let _resizeObserver = null;

function escHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fileBasename(path) {
  return String(path || '').split(/[\\/]/).pop() || '';
}

function compactPath(path) {
  const s = String(path || '');
  if (s.length <= 48) return s;
  return '…' + s.slice(-47);
}

function stackDims(info) {
  return (info && info.dimensions) || {};
}

function formatNumber(value, digits) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 'n/a';
  return n.toFixed(digits || 3).replace(/0+$/, '').replace(/\.$/, '');
}

function formatDimText(info) {
  if (!info) return 'not scanned';
  const d = stackDims(info);
  const parts = [`${d.x || 0}x${d.y || 0}`];
  if ((d.z || 1) > 1) parts.push(`Z${d.z}`);
  if ((d.c || 1) > 1) parts.push(`C${d.c}`);
  if ((d.t || 1) > 1) parts.push(`T${d.t}`);
  return parts.join(' · ');
}
