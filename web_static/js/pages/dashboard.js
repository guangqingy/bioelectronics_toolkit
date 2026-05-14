const GUI_ITEMS = [
  {
    id: 'abf_viewer',
    name: 'ABF Viewer',
    desc: 'Browse .abf files, select sweep and channel, preview with baseline subtraction and R-normalization. Export SVG / PNG / CSV.',
    why: 'Use this when you want to inspect individual patch-clamp recordings before exporting a figure or table.',
    tag: 'pyabf',
    domain: 'ABF',
    href: '/abf/viewer',
  },
  {
    id: 'abf_batch',
    name: 'Batch Processor',
    desc: 'Scan a folder of .abf files, auto-parse filenames, extract photocurrent peaks, and generate per-file and summary CSV outputs.',
    why: 'Use this for structured ABF folders where filenames encode main group, treatment, sample, spot, and sequence.',
    tag: 'Batch CSV',
    domain: 'ABF',
    href: '/abf/batch',
  },
  {
    id: 'abf_figure',
    name: 'Figure Generator',
    desc: 'Load summary CSVs, queue multiple folders, and plot peak/integral metrics with linear and log x-ranges.',
    why: 'Use this after batch processing to compare summary metrics across conditions.',
    tag: 'Comparison',
    domain: 'ABF',
    href: '/abf/figure',
  },
  {
    id: 'abf_peaks',
    name: 'Peak Detection',
    desc: 'R-normalized peak detection with configurable window and threshold controls, exporting peak summary CSV.',
    why: 'Use this when peak picking needs manual review or tuned thresholds on ABF sweeps.',
    tag: 'scipy peaks',
    domain: 'ABF',
    href: '/abf/peaks',
  },
  {
    id: 'echem_pc',
    name: 'Photocurrent Detector',
    desc: 'Detect POS/NEG threshold events from electrochemistry traces and export paired segments for review.',
    why: 'Use this for current traces where positive and negative events should be paired by timing.',
    tag: 'txt csv',
    domain: 'EChem',
    href: '/echem/photocurrent',
  },
  {
    id: 'echem_pv',
    name: 'Photovoltage Detector',
    desc: 'Detect square-pulse photovoltage signals with detrending options and export positive/negative pulses.',
    why: 'Use this for voltage traces that need pulse detection and optional baseline detrending.',
    tag: 'Detrend SG',
    domain: 'EChem',
    href: '/echem/photovoltage',
  },
  {
    id: 'echem_lineshape',
    name: 'EChem Lineshape',
    desc: 'Compare electrochemistry trace shapes and normalized responses across files.',
    why: 'Use this when the shape of the response curve matters more than single peak values.',
    tag: 'Shape',
    domain: 'EChem',
    href: '/echem/lineshape',
  },
  {
    id: 'rhd_viewer',
    name: 'Intan RHD Viewer',
    desc: 'Browse .rhd recordings, preview channels, and export selected/all channels as CSV.',
    why: 'Use this first when raw Intan recordings need channel inspection or conversion.',
    tag: 'rhd channels',
    domain: 'EMG',
    href: '/emg/rhd',
  },
  {
    id: 'emg_peaks',
    name: 'RHD Peak Selector',
    desc: 'Load channel CSVs, detect peaks with filters, group patterns, and export centered event segments.',
    why: 'Use this after RHD export when EMG events need peak-centered windows.',
    tag: 'Groups Export',
    domain: 'EMG',
    href: '/emg/peaks',
  },
  {
    id: 'csv_viewer',
    name: 'CSV Viewer',
    desc: 'Browse CSV files, choose X/Y columns, and overlay multiple files with export options.',
    why: 'Use this as a general trace viewer for quick CSV inspection and merged plots.',
    tag: 'CSV Merge',
    domain: 'Data',
    href: '/csv',
  },
  {
    id: 'fluorescence',
    name: 'TIFF Stack Browser',
    desc: 'Browse fluorescence TIFF stacks, tune display settings, and export selected stacks with Fiji macros.',
    why: 'Use this as the fluorescence entry point when starting from TIFF stack files.',
    tag: 'TIFF',
    domain: 'Imaging',
    href: '/fluorescence',
  },
  {
    id: 'fluorescence_lif',
    name: 'Leica LIF Browser',
    desc: 'Open Leica .lif projects, browse subfiles in acquisition-time order, preview channels, and save the ordered manifest.',
    why: 'Use this when the input is a Leica LIF project rather than plain TIFF files.',
    tag: 'Leica LIF',
    domain: 'Imaging',
    href: '/fluorescence/lif',
  },
  {
    id: 'fluorescence_roi',
    name: 'Fluorescence ROI Analysis',
    desc: 'Select TIFF stack pairs, draw ROIs, run sequence metrics, and export CSV, plots, previews, and GIFs.',
    why: 'Use this for per-ROI intensity time courses across fluorescence stack sequences.',
    tag: 'ROI',
    domain: 'Imaging',
    href: '/fluorescence/roi',
  },
  {
    id: 'fluorescence_gif',
    name: 'Annotated GIF Export',
    desc: 'Queue TIFF stacks, add scale bars, timestamps, optional ROI overlays, and export publication-preview GIFs.',
    why: 'Use this when the final artifact is a time-lapse GIF or ROI/kymograph preview.',
    tag: 'GIF',
    domain: 'Imaging',
    href: '/fluorescence/gif',
  },
  {
    id: 'fluorescence_3d_stacking',
    name: '3D Stack Builder',
    desc: 'Preview volume slices and export reconstructed fluorescence stack views.',
    why: 'Use this when the fluorescence workflow is volume-oriented rather than sequence-oriented.',
    tag: '3D',
    domain: 'Imaging',
    href: '/fluorescence/3d-stacking',
  },
  {
    id: 'histology',
    name: 'Histology Namer',
    desc: 'Preview Overview.vsi main and label images, then manually rename folders and QuPath display names.',
    why: 'Use this for histology folder cleanup before downstream image analysis.',
    tag: 'VSI rename',
    domain: 'Histology',
    href: '/histology',
  },
];

const DEFAULT_RECENT = ['abf_viewer', 'echem_pc', 'rhd_viewer', 'fluorescence', 'fluorescence_roi', 'csv_viewer'];
const USAGE_KEY = 'dp_gui_usage_v2';
const PIN_KEY = 'dp_gui_pins_v1';

function loadJsonMap(key) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function saveJsonMap(key, value) {
  localStorage.setItem(key, JSON.stringify(value || {}));
}

function getPins() {
  try {
    const parsed = JSON.parse(localStorage.getItem(PIN_KEY) || '[]');
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set();
  }
}

function savePins(pins) {
  localStorage.setItem(PIN_KEY, JSON.stringify(Array.from(pins)));
}

function trackOpen(id) {
  const usage = loadJsonMap(USAGE_KEY);
  const rec = usage[id] || {count: 0, last: 0};
  usage[id] = {count: Number(rec.count || 0) + 1, last: Date.now()};
  saveJsonMap(USAGE_KEY, usage);
}

function scoreItem(item, usage, pins) {
  const rec = usage[item.id] || {};
  return [
    pins.has(item.id) ? 1 : 0,
    Number(rec.last || 0),
    Number(rec.count || 0),
    item.name,
  ];
}

function byUsage(a, b, usage, pins) {
  const aa = scoreItem(a, usage, pins);
  const bb = scoreItem(b, usage, pins);
  return (bb[0] - aa[0]) || (bb[1] - aa[1]) || (bb[2] - aa[2]) || aa[3].localeCompare(bb[3]);
}

function makeGuiCard(item, pins) {
  const card = document.createElement('div');
  card.className = 'dash-card gui-card';
  const pinned = pins.has(item.id);
  card.innerHTML = `
    <a class="gui-link" href="${item.href}" data-tool-id="${item.id}">
      <div class="dash-card-name">${item.name}</div>
      <div class="dash-card-desc">${item.desc}</div>
    </a>
    <details>
      <summary>What's this for?</summary>
      <div>${item.why}</div>
    </details>
    <div class="gui-card-footer">
      <div class="gui-tag-wrap">
        <span class="dash-card-tag" style="margin-top:0;">${item.tag}</span>
        <span class="gui-domain">${item.domain}</span>
      </div>
      <button class="btn-secondary gui-pin" type="button">${pinned ? 'Unpin' : 'Pin'}</button>
    </div>
  `;
  card.querySelector('a').addEventListener('click', () => trackOpen(item.id));
  card.querySelector('button').addEventListener('click', ev => {
    ev.preventDefault();
    ev.stopPropagation();
    if (pins.has(item.id)) pins.delete(item.id);
    else pins.add(item.id);
    savePins(pins);
    renderGuiBoards();
  });
  return card;
}

function renderGrid(id, items, pins, emptyText) {
  const grid = document.getElementById(id);
  grid.innerHTML = '';
  if (!items.length) {
    grid.innerHTML = `<div class="empty-recent">${emptyText}</div>`;
    return;
  }
  items.forEach(item => grid.appendChild(makeGuiCard(item, pins)));
}

function renderGuiBoards() {
  const usage = loadJsonMap(USAGE_KEY);
  const pins = getPins();
  const usedItems = GUI_ITEMS.filter(item => usage[item.id] || pins.has(item.id));
  const recentItems = (usedItems.length ? usedItems : GUI_ITEMS.filter(item => DEFAULT_RECENT.includes(item.id)))
    .slice()
    .sort((a, b) => byUsage(a, b, usage, pins))
    .slice(0, 8);
  const allItems = GUI_ITEMS.slice().sort((a, b) => byUsage(a, b, usage, pins));

  renderGrid('recentGuiGrid', recentItems, pins, 'Open a tool and it will appear here.');
  renderGrid('allGuiGrid', allItems, pins, '');
  document.getElementById('recentCount').textContent = `(${recentItems.length})`;
  document.getElementById('allCount').textContent = `(${allItems.length})`;
}

document.addEventListener('DOMContentLoaded', () => {
  renderGuiBoards();
  document.getElementById('resetGuiUsage').addEventListener('click', () => {
    localStorage.removeItem(USAGE_KEY);
    renderGuiBoards();
    toast('Tool usage reset');
  });
  document.querySelectorAll('.demo-actions a').forEach(link => {
    link.addEventListener('click', () => {
      toast('Opening demo data from examples/');
    });
  });
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'byUsage',
  'getPins',
  'loadJsonMap',
  'makeGuiCard',
  'renderGrid',
  'renderGuiBoards',
  'saveJsonMap',
  'savePins',
  'scoreItem',
  'trackOpen',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
