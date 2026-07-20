/* EChem Quantification page.
 *
 * Scans a session folder, parses each recording filename into condition tokens,
 * and quantifies every file in one pass. Token filters narrow which recordings
 * are quantified, so a comparison can be restricted to a single light timing or
 * concentration without renaming or moving files.
 */

let _records = [];
let _facets = [];
let _tokenLabels = {};
let _filters = {};
let _rows = [];

/* Columns shown before the token columns; the rest are discovered per result. */
const METRIC_COLUMNS = [
  ['token_technique', 'Technique'],
  ['token_session', 'Session'],
  ['label', 'Condition'],
  ['n_pulses', 'Pulses'],
  ['amplitude_nA_cm2', 'Amplitude (nA cm⁻²)'],
  ['amplitude_sd_nA_cm2', 'SD (nA cm⁻²)'],
  ['charge_nC_cm2', 'Charge (nC cm⁻²)'],
  ['amplitude_nA', 'Amplitude (nA)'],
  ['amplitude_sd_nA', 'SD (nA)'],
  ['charge_nC', 'Charge (nC)'],
  ['p2p_nA', 'P2P (nA)'],
  ['n_cycles', 'Cycles'],
  ['amplitude_mV', 'Amplitude (mV)'],
  ['amplitude_sd_mV', 'SD (mV)'],
  ['spike_nA_cm2', 'Spike (nA cm⁻²)'],
  ['plateau_nA_cm2', 'Plateau (nA cm⁻²)'],
  ['spike_nA', 'Spike (nA)'],
  ['plateau_nA', 'Plateau (nA)'],
  ['Ipa_uA_cm2', 'Ipa (µA cm⁻²)'],
  ['Ipa_uA', 'Ipa (µA)'],
  ['Epa_V', 'Epa (V)'],
  ['anodic_status', 'CV peak QC'],
  ['period_ms', 'Period (ms)'],
  ['period_source', 'Period from'],
  ['detection_preset', 'Detection preset'],
  ['n_clipped', 'Clipped'],
  ['n_rejected_gaps', 'Dropped'],
  ['status', 'Status'],
];

function fmt(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') {
    if (!isFinite(value)) return '—';
    const abs = Math.abs(value);
    if (abs !== 0 && (abs < 1e-3 || abs >= 1e6)) return value.toExponential(3);
    return String(Math.round(value * 1000) / 1000);
  }
  return String(value);
}

function filterKey(token, value) {
  return `${token}=${value}`;
}

function renderFacets() {
  const el = document.getElementById('facetList');
  if (!el) return;
  if (!_facets.length) {
    el.innerHTML = '<div class="file-list-empty">Scan a folder to list tokens</div>';
    return;
  }
  el.innerHTML = _facets.map(facet => {
    const values = facet.values.map(entry => {
      const key = filterKey(facet.token, entry.value);
      const active = Boolean(_filters[key]);
      return `<div class="file-item${active ? ' active' : ''}" data-dp-click="DP.page.toggleFilter('${dpEscapeHtml(facet.token)}','${dpEscapeHtml(entry.value)}')">`
        + `${active ? '✓ ' : ''}${dpEscapeHtml(entry.value)} <span style="color:var(--silver)">· ${entry.count}</span></div>`;
    }).join('');
    return `<div class="ctrl-label" style="margin-top:8px">${dpEscapeHtml(facet.label || facet.token)}</div>${values}`;
  }).join('');
}

/* A record matches when, for every filtered token, it carries one of the
 * selected values. Values within a token are OR-ed, tokens are AND-ed. */
function matchesFilters(record) {
  const active = {};
  Object.keys(_filters).forEach(key => {
    if (!_filters[key]) return;
    const [token, value] = key.split(/=(.*)/s);
    (active[token] = active[token] || []).push(value);
  });
  return Object.keys(active).every(token => {
    const raw = record.fields ? record.fields[token] : undefined;
    if (raw === undefined) return false;
    const owned = Array.isArray(raw) ? raw.map(String) : [formatFieldValue(raw)];
    return active[token].some(value => owned.indexOf(value) !== -1);
  });
}

/* Mirrors the server-side token formatting so filter values compare equal. */
function formatFieldValue(value) {
  if (typeof value === 'number') return String(value);
  return String(value);
}

function selectedPaths() {
  return _records.filter(matchesFilters).map(record => record.path);
}

function updateScanInfo() {
  const el = document.getElementById('scanInfo');
  if (!el) return;
  const matched = selectedPaths().length;
  el.textContent = _records.length
    ? `${matched} of ${_records.length} recordings match`
    : '';
}

function techniqueSummary(counts) {
  return ['CA', 'CP', 'CV', 'COR']
    .filter(key => counts && counts[key])
    .map(key => `${key} ${counts[key]}`)
    .join(' · ');
}

function renderScanSummary(counts) {
  const card = document.getElementById('scanSummaryCard');
  const body = document.getElementById('scanSummaryBody');
  if (!card || !body) return;
  const breakdown = techniqueSummary(counts || {});
  body.textContent = _records.length
    ? `${_records.length} source recordings are ready (${breakdown}). Use token filters to select a comparison, or quantify the complete project. Archives, reports, and exported segment folders were skipped.`
    : 'No source recordings were found in this folder.';
  card.style.display = '';
}

async function scanFolder() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) {
    setStatus('status', 'Choose a folder first', 'err');
    return;
  }
  setStatus('status', 'Scanning…');
  try {
    const data = await api('/api/echem/tokens/scan', {folder});
    _records = data.records || [];
    _facets = data.facets || [];
    _tokenLabels = data.token_labels || {};
    _filters = {};
    _rows = [];
    renderResults();
    const warningBox = document.getElementById('warnBox');
    if (warningBox) warningBox.style.display = 'none';
    renderFacets();
    updateScanInfo();
    renderUnparsed(data.n_unparsed);
    renderScanSummary(data.technique_counts || {});
    const breakdown = techniqueSummary(data.technique_counts || {});
    setStatus('status', `${_records.length} recordings parsed${breakdown ? ` · ${breakdown}` : ''}`, 'ok');
  } catch (error) {
    setStatus('status', String(error && error.message ? error.message : error), 'err');
  }
}

function renderUnparsed(count) {
  const card = document.getElementById('unparsedCard');
  const body = document.getElementById('unparsedBody');
  if (!card || !body) return;
  const offenders = _records.filter(record => (record.unparsed || []).length);
  if (!offenders.length) {
    card.style.display = 'none';
    return;
  }
  card.style.display = '';
  body.innerHTML = `${count || offenders.length} file(s) contain name fragments no token rule claimed. `
    + 'They are still listed and quantified, but they will not appear under any token filter.<br><br>'
    + offenders.slice(0, 20).map(record =>
        `<code>${dpEscapeHtml(record.file)}</code> → ${dpEscapeHtml((record.unparsed || []).join(', '))}`
      ).join('<br>');
}

function toggleFilter(token, value) {
  const key = filterKey(token, value);
  _filters[key] = !_filters[key];
  renderFacets();
  updateScanInfo();
}

function clearFilters() {
  _filters = {};
  renderFacets();
  updateScanInfo();
}

async function quantify() {
  const paths = selectedPaths();
  if (!paths.length) {
    setStatus('status', 'No recordings match the current filters', 'err');
    return;
  }
  const expected = document.getElementById('expectedPeriod').value;
  const payload = {
    preset: document.getElementById('preset').value,
    signed: document.getElementById('signed').checked,
    electrode_area_cm2: parseFloat(document.getElementById('electrodeArea').value),
    cv_window_low_V: parseFloat(document.getElementById('cvWindowLow').value),
    cv_window_high_V: parseFloat(document.getElementById('cvWindowHigh').value),
  };
  if (expected !== '') payload.expected_period_s = parseFloat(expected);

  const button = document.getElementById('btnQuantify');
  if (button) {
    button.disabled = true;
    button.textContent = 'Quantifying…';
  }
  _rows = [];
  try {
    // Short requests keep the GUI responsive on the 10 kHz CorrTest files and
    // make progress visible across a whole multi-session project.
    const chunkSize = 8;
    let ok = 0;
    for (let start = 0; start < paths.length; start += chunkSize) {
      const chunk = paths.slice(start, start + chunkSize);
      setStatus('status', `Quantifying ${Math.min(start + chunk.length, paths.length)} of ${paths.length}…`);
      const data = await api('/api/echem/metrics/batch', {...payload, paths: chunk});
      _rows = _rows.concat(data.rows || []);
      ok += data.n_ok || 0;
      renderResults();
    }
    renderWarnings();
    setStatus('status', `${ok} of ${_rows.length} passed QC`, ok === _rows.length ? 'ok' : 'warning');
  } catch (error) {
    setStatus('status', String(error && error.message ? error.message : error), 'err');
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = button.dataset.label || 'Quantify Folder';
    }
  }
}

/* Only columns that at least one row actually populated are shown, so a CA
 * batch does not carry empty CP columns and vice versa. */
function activeColumns() {
  const present = new Set();
  _rows.forEach(row => Object.keys(row).forEach(key => {
    if (row[key] !== null && row[key] !== undefined) present.add(key);
  }));
  const metrics = METRIC_COLUMNS.filter(([key]) => present.has(key));
  const fixedKeys = new Set(metrics.map(([key]) => key));
  const tokenKeys = Array.from(present)
    .filter(key => key.indexOf('token_') === 0 && !fixedKeys.has(key)).sort();
  const tokenCols = tokenKeys.map(key => {
    const token = key.slice('token_'.length);
    return [key, _tokenLabels[token] || token];
  });
  return metrics.concat(tokenCols);
}

function renderResults() {
  const card = document.getElementById('resultCard');
  const table = document.getElementById('resultTable');
  if (!card || !table) return;
  if (!_rows.length) {
    card.style.display = 'none';
    return;
  }
  const columns = activeColumns();
  table.querySelector('thead tr').innerHTML =
    columns.map(([, label]) => `<th>${dpEscapeHtml(label)}</th>`).join('');
  table.querySelector('tbody').innerHTML = _rows.map(row =>
    `<tr data-status="${dpEscapeHtml(row.status || '')}">`
      + columns.map(([key]) => `<td>${dpEscapeHtml(fmt(row[key]))}</td>`).join('') + '</tr>'
  ).join('');
  card.style.display = '';
  const summaryCard = document.getElementById('scanSummaryCard');
  if (summaryCard) summaryCard.style.display = 'none';
  const count = document.getElementById('resultCount');
  if (count) count.textContent = `${_rows.length} recording(s)`;
}

/* Surface QC and protocol-comparability issues without hiding usable rows. */
function renderWarnings() {
  const box = document.getElementById('warnBox');
  if (!box) return;
  const messages = [];
  const inferred = _rows.filter(row => row.period_source === 'inferred').length;
  if (inferred) {
    messages.push(`${inferred} chronopotentiometry recording(s) had no reliable automatic period. `
      + 'Set "CP period (s)" and re-run those rows before using their amplitudes.');
  }
  const duties = new Set(_rows.filter(row => row.token_light_duty !== undefined)
    .map(row => row.token_light_duty));
  if (duties.size > 1) {
    messages.push(`These results mix ${duties.size} light duty cycles. Spike amplitudes stay `
      + 'comparable, but plateau values do not: the plateau estimator assumes ON and OFF occupy '
      + 'similar fractions of a cycle, so a plateau trend across mixed timings reflects the '
      + 'protocol rather than the sample.');
  }
  const failed = _rows.filter(row => String(row.status || '').indexOf('error') === 0).length;
  if (failed) messages.push(`${failed} recording(s) failed to quantify; see the Status column.`);

  const empty = _rows.filter(row => String(row.status || '').indexOf('no ') === 0).length;
  if (empty) {
    messages.push(`${empty} recording(s) yielded no detected events. That can mean the preset `
      + 'does not match the acquisition, or that the recording genuinely carries no pulse train. '
      + 'Auto mode already tried all detection presets; inspect these traces before treating them as zero response.');
  }

  if (!messages.length) {
    box.style.display = 'none';
    return;
  }
  box.style.display = '';
  box.innerHTML = messages.map(text => `⚠ ${dpEscapeHtml(text)}`).join('<br><br>');
}

function exportCSV() {
  if (!_rows.length) {
    setStatus('status', 'Nothing to export', 'err');
    return;
  }
  const columns = activeColumns();
  const escape = value => {
    const text = value === null || value === undefined ? '' : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const lines = [columns.map(([key]) => escape(key)).join(',')];
  _rows.forEach(row => lines.push(columns.map(([key]) => escape(row[key])).join(',')));

  const blob = new Blob([lines.join('\n')], {type: 'text/csv'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'echem_quantification.csv';
  link.click();
  URL.revokeObjectURL(url);
  setStatus('status', `Exported ${_rows.length} row(s)`, 'ok');
}

document.addEventListener('DOMContentLoaded', () => {
  setStatus('status', 'Ready', 'ok');
  const params = new URLSearchParams(window.location.search);
  const folder = params.get('folder');
  if (folder) {
    document.getElementById('folderPath').value = folder;
    scanFolder();
  }
});

window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'clearFilters',
  'exportCSV',
  'quantify',
  'scanFolder',
  'toggleFilter',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
