let _lastEmgAnalysisRenamePreview = null;
let _lastEmgAnalysisRenamePayloadKey = '';

function emgAnalysisRenameChecked(id) {
  return document.getElementById(id)?.checked || false;
}

function emgAnalysisRenameValue(id) {
  return document.getElementById(id)?.value || '';
}

function emgAnalysisCurrentRenameRoot() {
  return document.getElementById('folderPath')?.value.trim() || '';
}

function emgAnalysisDirName(path) {
  return String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
}

function emgAnalysisStemName(pathOrName) {
  return emgAnalysisDirName(pathOrName).replace(/\.[^.\\/]+$/, '');
}

function emgAnalysisRecordingToken(pathOrName) {
  const stem = emgAnalysisStemName(pathOrName);
  const match = stem.match(/^(.+)_\d{6}_\d{4,6}$/);
  return (match ? match[1] : stem).trim();
}

function emgAnalysisSelectedNameSource() {
  if (_currentFile) return _currentFile;
  const active = document.querySelector('#emgAnalysisFileList .file-item.active');
  if (active?.title) {
    const firstPath = String(active.title).split('\n').find(Boolean);
    if (firstPath) return firstPath;
  }
  return emgAnalysisCurrentRenameRoot();
}

function setEmgAnalysisQuickRenameDefaults() {
  const fields = {
    renamePrefix: '',
    renameSuffix: '',
    renameExtensions: '.rhd,.xml,.csv,.txt,.tsv,.json,.png,.svg',
  };
  Object.entries(fields).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (el) el.value = value;
  });
  [
    ['renameRoot', true],
    ['renameFiles', true],
    ['renameDirs', true],
    ['renameRecursive', true],
    ['renameRegex', false],
    ['renameCaseSensitive', true],
  ].forEach(([id, checked]) => {
    const el = document.getElementById(id);
    if (el) el.checked = checked;
  });
}

function useSelectedEmgAnalysisToken() {
  const token = emgAnalysisRecordingToken(emgAnalysisSelectedNameSource());
  document.getElementById('renameFind').value = token;
  setEmgAnalysisQuickRenameDefaults();
  setStatus('status', token ? `Old token set to ${token}` : 'Selected recording token unavailable', token ? 'ok' : 'error');
}

function autoFillEmgAnalysisRenameToken(source) {
  const findEl = document.getElementById('renameFind');
  const replaceEl = document.getElementById('renameReplace');
  if (!findEl || !replaceEl) return '';
  if (findEl.value.trim() || replaceEl.value.trim()) return findEl.value.trim();
  const token = emgAnalysisRecordingToken(source || emgAnalysisSelectedNameSource());
  if (token) {
    findEl.value = token;
    setEmgAnalysisQuickRenameDefaults();
  }
  return token;
}

function useSelectedEmgAnalysisName() {
  const name = emgAnalysisStemName(emgAnalysisSelectedNameSource());
  document.getElementById('renameFind').value = name;
  setEmgAnalysisQuickRenameDefaults();
  setStatus('status', name ? `Old recording name set to ${name}` : 'Selected recording name unavailable', name ? 'ok' : 'error');
}

function useCurrentFolderName() {
  const root = emgAnalysisCurrentRenameRoot();
  if (!root) {
    setStatus('status', 'Choose an EMG recording folder first', 'error');
    return;
  }
  const name = emgAnalysisDirName(root);
  document.getElementById('renameFind').value = name;
  setStatus('status', name ? `Current rename text set to ${name}` : 'Folder name unavailable', name ? 'ok' : 'error');
}

function validateQuickEmgAnalysisRename() {
  setEmgAnalysisQuickRenameDefaults();
  const oldValue = emgAnalysisRenameValue('renameFind').trim();
  const newValue = emgAnalysisRenameValue('renameReplace').trim();
  if (!oldValue) {
    useSelectedEmgAnalysisToken();
  }
  if (!emgAnalysisRenameValue('renameFind').trim()) {
    setStatus('status', 'Choose a recording or enter an old token first', 'error');
    return false;
  }
  if (!newValue) {
    setStatus('status', 'Enter a new recording token', 'error');
    return false;
  }
  return true;
}

function previewQuickEmgAnalysisRename() {
  if (!validateQuickEmgAnalysisRename()) return Promise.resolve(null);
  return previewEmgAnalysisRenames({buttonId: 'btnQuickRenamePreview', idleLabel: 'Preview Quick Rename'});
}

function applyQuickEmgAnalysisRename() {
  if (!validateQuickEmgAnalysisRename()) return Promise.resolve(null);
  return applyEmgAnalysisRenames({buttonId: 'btnQuickRenameApply', idleLabel: 'Apply Quick Rename'});
}

function buildEmgAnalysisRenamePayload(extra) {
  return Object.assign({
    root: emgAnalysisCurrentRenameRoot(),
    find: emgAnalysisRenameValue('renameFind'),
    replace: emgAnalysisRenameValue('renameReplace'),
    prefix: emgAnalysisRenameValue('renamePrefix'),
    suffix: emgAnalysisRenameValue('renameSuffix'),
    recursive: emgAnalysisRenameChecked('renameRecursive'),
    include_root: emgAnalysisRenameChecked('renameRoot'),
    include_files: emgAnalysisRenameChecked('renameFiles'),
    include_dirs: emgAnalysisRenameChecked('renameDirs'),
    use_regex: emgAnalysisRenameChecked('renameRegex'),
    case_sensitive: emgAnalysisRenameChecked('renameCaseSensitive'),
    preserve_extension: true,
    skip_hidden: true,
    extensions: emgAnalysisRenameValue('renameExtensions'),
    max_items: 5000,
  }, extra || {});
}

function emgAnalysisRenamePayloadKey(payload) {
  const copy = Object.assign({}, payload || {});
  delete copy.confirm;
  return JSON.stringify(copy);
}

function emgAnalysisPathHasPrefix(path, prefix) {
  const text = String(path || '');
  const root = String(prefix || '').replace(/[\\/]+$/, '');
  if (!text || !root) return false;
  return text === root || text.startsWith(root + '/') || text.startsWith(root + '\\');
}

function remapEmgAnalysisPathAfterRename(path, changes) {
  const text = String(path || '');
  if (!text || !Array.isArray(changes) || !changes.length) return text;

  const exact = changes.find(row => row && row.source_path === text && row.target_path);
  if (exact) return exact.target_path;

  const folders = changes
    .filter(row => row && row.kind === 'folder' && row.source_path && row.target_path)
    .sort((a, b) => String(b.source_path).length - String(a.source_path).length);
  for (const row of folders) {
    const source = String(row.source_path).replace(/[\\/]+$/, '');
    if (emgAnalysisPathHasPrefix(text, source)) {
      return String(row.target_path).replace(/[\\/]+$/, '') + text.slice(source.length);
    }
  }
  return text;
}

function remapEmgAnalysisPathListAfterRename(paths, changes) {
  const seen = new Set();
  const out = [];
  (paths || []).forEach(path => {
    const mapped = remapEmgAnalysisPathAfterRename(path, changes);
    if (mapped && !seen.has(mapped)) {
      seen.add(mapped);
      out.push(mapped);
    }
  });
  return out;
}

function emgAnalysisRenameStatusClass(status) {
  if (status === 'ready' || status === 'renamed') return 'ok';
  if (status === 'target_exists' || status === 'duplicate_target' || status === 'invalid') return 'bad';
  return 'warn';
}

function renderEmgAnalysisRenamePreview(data) {
  _lastEmgAnalysisRenamePreview = data || null;
  const area = document.getElementById('renamePreviewArea');
  if (!area) return;
  const rows = Array.isArray(data?.changes) ? data.changes : [];
  if (!rows.length) {
    area.innerHTML = '<div class="plot-placeholder">No matching EMG names found</div>';
    return;
  }
  const summary = `${data.ready_count || 0} ready · ${data.conflict_count || 0} conflict(s) · ${data.scanned_count || rows.length} scanned`;
  const shown = rows.slice(0, 300);
  area.innerHTML = `
    <div class="run-history-meta" style="margin-bottom:8px;">${escHtml(summary)}</div>
    <div class="data-table-wrap">
      <table class="dp-table">
        <thead>
          <tr>
            <th>Status</th>
            <th>Type</th>
            <th>Current path</th>
            <th>New path</th>
          </tr>
        </thead>
        <tbody>
          ${shown.map(row => `
            <tr>
              <td><span class="run-check-status ${emgAnalysisRenameStatusClass(row.status)}">${escHtml(row.status || '')}</span>${row.reason ? `<div class="run-history-meta">${escHtml(row.reason)}</div>` : ''}</td>
              <td>${escHtml(row.kind || '')}</td>
              <td><code>${escHtml(row.source_path || '')}</code></td>
              <td><code>${escHtml(row.target_path || '')}</code></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function previewEmgAnalysisRenames(options) {
  const opts = options || {};
  const buttonId = opts.buttonId || 'btnRenamePreview';
  const idleLabel = opts.idleLabel || 'Preview Renames';
  const payload = buildEmgAnalysisRenamePayload();
  if (!payload.root) {
    _lastEmgAnalysisRenamePreview = null;
    setStatus('status', 'Choose an EMG recording folder first', 'error');
    return Promise.resolve(null);
  }
  _lastEmgAnalysisRenamePreview = null;
  _lastEmgAnalysisRenamePayloadKey = emgAnalysisRenamePayloadKey(payload);
  btnBusy(buttonId, true, 'Previewing...');
  setStatus('status', 'Building rename preview...', 'loading');
  return api('/api/emg/analysis/rename/preview', payload)
    .then(data => {
      if (data.error) throw new Error(data.error);
      renderEmgAnalysisRenamePreview(data);
      const conflicts = Number(data.conflict_count || 0);
      const ready = Number(data.ready_count || 0);
      setStatus(
        'status',
        conflicts ? `${conflicts} rename conflict(s); adjust the rule first` : `${ready} rename(s) ready`,
        conflicts ? 'error' : 'ok'
      );
      return data;
    })
    .catch(e => {
      _lastEmgAnalysisRenamePreview = null;
      setStatus('status', 'Error: ' + e.message, 'error');
      return null;
    })
    .finally(() => btnBusy(buttonId, false, idleLabel));
}

async function applyEmgAnalysisRenames(options) {
  const opts = options || {};
  const buttonId = opts.buttonId || 'btnRenameApply';
  const idleLabel = opts.idleLabel || 'Apply Renames';
  const basePayload = buildEmgAnalysisRenamePayload();
  if (!_lastEmgAnalysisRenamePreview || _lastEmgAnalysisRenamePayloadKey !== emgAnalysisRenamePayloadKey(basePayload)) {
    await previewEmgAnalysisRenames(opts.previewOptions || options);
  }
  if (!_lastEmgAnalysisRenamePreview) return;
  if (Number(_lastEmgAnalysisRenamePreview.conflict_count || 0) > 0) {
    setStatus('status', 'Resolve rename conflicts before applying', 'error');
    return;
  }
  if (!Number(_lastEmgAnalysisRenamePreview.ready_count || 0)) {
    setStatus('status', 'No renames to apply', 'error');
    return;
  }

  const ok = await dpConfirmAction({
    title: 'Apply EMG analysis renames',
    subtitle: `${_lastEmgAnalysisRenamePreview.ready_count || 0} path(s) will be renamed.`,
    message: 'This changes folder and file names on disk.',
    confirmText: 'Apply Renames',
    danger: true,
  });
  if (!ok) return;

  const payload = buildEmgAnalysisRenamePayload({confirm: true});
  btnBusy(buttonId, true, 'Applying...');
  setStatus('status', 'Applying EMG analysis renames...', 'loading');
  dpRunJobEndpoint('/api/emg/analysis/rename/apply_job', payload, {
    interval_ms: 800,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Applying EMG analysis renames${pct}${msg}`, 'loading');
    },
  })
    .then(data => {
      if (data.error) throw new Error(data.error);
      const changes = data.changes || [];
      const updatedRoot = data.updated_root || remapEmgAnalysisPathAfterRename(payload.root, changes);
      if (updatedRoot) {
        document.getElementById('folderPath').value = updatedRoot;
      }
      if (updatedRoot && updatedRoot !== payload.root) {
        document.getElementById('renameFind').value = emgAnalysisDirName(updatedRoot);
      }
      _currentFile = remapEmgAnalysisPathAfterRename(_currentFile, changes);
      _emgAnalysisFiles = remapEmgAnalysisPathListAfterRename(_emgAnalysisFiles, changes);
      _queueFiles = remapEmgAnalysisPathListAfterRename(_queueFiles, changes);
      queueRender();
      scanFolder({
        preserveChannel: true,
        loadProfile: true,
        selectedPath: _currentFile,
      });
      renderEmgAnalysisRenamePreview({
        root: updatedRoot || document.getElementById('folderPath').value.trim(),
        scanned_count: changes.length,
        changed_count: changes.length,
        ready_count: changes.length,
        conflict_count: 0,
        changes: changes.map(row => Object.assign({status: 'renamed'}, row)),
      });
      setStatus('status', `Renamed ${data.renamed_count || 0} EMG recording path(s)`, 'ok');
      recordRunHistory({
        view: 'emg_analysis',
        title: 'EMG Analysis Recording Rename',
        status: 'ok',
        project_root: updatedRoot || data.root || payload.root,
        input_files: [
          {path: updatedRoot || data.root || payload.root, role: 'emg_recording_folder'},
        ],
        outputs: data.outputs || [],
        parameters: Object.assign({}, payload, {
          original_root: payload.root,
          root: updatedRoot || payload.root,
        }),
        metadata: {renamed_count: data.renamed_count || 0},
      });
      _lastEmgAnalysisRenamePreview = null;
      _lastEmgAnalysisRenamePayloadKey = '';
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'))
    .finally(() => btnBusy(buttonId, false, idleLabel));
}

window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'applyEmgAnalysisRenames',
  'applyQuickEmgAnalysisRename',
  'autoFillEmgAnalysisRenameToken',
  'buildEmgAnalysisRenamePayload',
  'previewQuickEmgAnalysisRename',
  'previewEmgAnalysisRenames',
  'emgAnalysisRecordingToken',
  'renderEmgAnalysisRenamePreview',
  'setEmgAnalysisQuickRenameDefaults',
  'useCurrentFolderName',
  'useSelectedEmgAnalysisName',
  'useSelectedEmgAnalysisToken',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
