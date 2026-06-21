let _lastRhdRenamePreview = null;
let _lastRhdRenamePayloadKey = '';

function rhdRenameChecked(id) {
  return document.getElementById(id)?.checked || false;
}

function rhdRenameValue(id) {
  return document.getElementById(id)?.value || '';
}

function rhdCurrentRenameRoot() {
  return document.getElementById('folderPath')?.value.trim() || '';
}

function rhdDirName(path) {
  return String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
}

function rhdStemName(pathOrName) {
  return rhdDirName(pathOrName).replace(/\.[^.\\/]+$/, '');
}

function rhdRecordingToken(pathOrName) {
  const stem = rhdStemName(pathOrName);
  const match = stem.match(/^(.+)_\d{6}_\d{4,6}$/);
  return (match ? match[1] : stem).trim();
}

function rhdSelectedNameSource() {
  if (_currentFile) return _currentFile;
  const active = document.querySelector('#rhdList .file-item.active');
  if (active?.title) {
    const firstPath = String(active.title).split('\n').find(Boolean);
    if (firstPath) return firstPath;
  }
  return rhdCurrentRenameRoot();
}

function setRhdQuickRenameDefaults() {
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

function useSelectedRhdToken() {
  const token = rhdRecordingToken(rhdSelectedNameSource());
  document.getElementById('renameFind').value = token;
  setRhdQuickRenameDefaults();
  setStatus('status', token ? `Old token set to ${token}` : 'Selected recording token unavailable', token ? 'ok' : 'error');
}

function autoFillRhdRenameToken(source) {
  const findEl = document.getElementById('renameFind');
  const replaceEl = document.getElementById('renameReplace');
  if (!findEl || !replaceEl) return '';
  if (findEl.value.trim() || replaceEl.value.trim()) return findEl.value.trim();
  const token = rhdRecordingToken(source || rhdSelectedNameSource());
  if (token) {
    findEl.value = token;
    setRhdQuickRenameDefaults();
  }
  return token;
}

function useSelectedRhdName() {
  const name = rhdStemName(rhdSelectedNameSource());
  document.getElementById('renameFind').value = name;
  setRhdQuickRenameDefaults();
  setStatus('status', name ? `Old recording name set to ${name}` : 'Selected recording name unavailable', name ? 'ok' : 'error');
}

function useCurrentFolderName() {
  const root = rhdCurrentRenameRoot();
  if (!root) {
    setStatus('status', 'Choose an RHD folder first', 'error');
    return;
  }
  const name = rhdDirName(root);
  document.getElementById('renameFind').value = name;
  setStatus('status', name ? `Current rename text set to ${name}` : 'Folder name unavailable', name ? 'ok' : 'error');
}

function validateQuickRhdRename() {
  setRhdQuickRenameDefaults();
  const oldValue = rhdRenameValue('renameFind').trim();
  const newValue = rhdRenameValue('renameReplace').trim();
  if (!oldValue) {
    useSelectedRhdToken();
  }
  if (!rhdRenameValue('renameFind').trim()) {
    setStatus('status', 'Choose a recording or enter an old token first', 'error');
    return false;
  }
  if (!newValue) {
    setStatus('status', 'Enter a new recording token', 'error');
    return false;
  }
  return true;
}

function previewQuickRhdRename() {
  if (!validateQuickRhdRename()) return Promise.resolve(null);
  return previewRhdRenames({buttonId: 'btnQuickRenamePreview', idleLabel: 'Preview Quick Rename'});
}

function applyQuickRhdRename() {
  if (!validateQuickRhdRename()) return Promise.resolve(null);
  return applyRhdRenames({buttonId: 'btnQuickRenameApply', idleLabel: 'Apply Quick Rename'});
}

function buildRhdRenamePayload(extra) {
  return Object.assign({
    root: rhdCurrentRenameRoot(),
    find: rhdRenameValue('renameFind'),
    replace: rhdRenameValue('renameReplace'),
    prefix: rhdRenameValue('renamePrefix'),
    suffix: rhdRenameValue('renameSuffix'),
    recursive: rhdRenameChecked('renameRecursive'),
    include_root: rhdRenameChecked('renameRoot'),
    include_files: rhdRenameChecked('renameFiles'),
    include_dirs: rhdRenameChecked('renameDirs'),
    use_regex: rhdRenameChecked('renameRegex'),
    case_sensitive: rhdRenameChecked('renameCaseSensitive'),
    preserve_extension: true,
    skip_hidden: true,
    extensions: rhdRenameValue('renameExtensions'),
    max_items: 5000,
  }, extra || {});
}

function rhdRenamePayloadKey(payload) {
  const copy = Object.assign({}, payload || {});
  delete copy.confirm;
  return JSON.stringify(copy);
}

function rhdPathHasPrefix(path, prefix) {
  const text = String(path || '');
  const root = String(prefix || '').replace(/[\\/]+$/, '');
  if (!text || !root) return false;
  return text === root || text.startsWith(root + '/') || text.startsWith(root + '\\');
}

function remapRhdPathAfterRename(path, changes) {
  const text = String(path || '');
  if (!text || !Array.isArray(changes) || !changes.length) return text;

  const exact = changes.find(row => row && row.source_path === text && row.target_path);
  if (exact) return exact.target_path;

  const folders = changes
    .filter(row => row && row.kind === 'folder' && row.source_path && row.target_path)
    .sort((a, b) => String(b.source_path).length - String(a.source_path).length);
  for (const row of folders) {
    const source = String(row.source_path).replace(/[\\/]+$/, '');
    if (rhdPathHasPrefix(text, source)) {
      return String(row.target_path).replace(/[\\/]+$/, '') + text.slice(source.length);
    }
  }
  return text;
}

function remapRhdPathListAfterRename(paths, changes) {
  const seen = new Set();
  const out = [];
  (paths || []).forEach(path => {
    const mapped = remapRhdPathAfterRename(path, changes);
    if (mapped && !seen.has(mapped)) {
      seen.add(mapped);
      out.push(mapped);
    }
  });
  return out;
}

function rhdRenameStatusClass(status) {
  if (status === 'ready' || status === 'renamed') return 'ok';
  if (status === 'target_exists' || status === 'duplicate_target' || status === 'invalid') return 'bad';
  return 'warn';
}

function renderRhdRenamePreview(data) {
  _lastRhdRenamePreview = data || null;
  const area = document.getElementById('renamePreviewArea');
  if (!area) return;
  const rows = Array.isArray(data?.changes) ? data.changes : [];
  if (!rows.length) {
    area.innerHTML = '<div class="plot-placeholder">No matching RHD/EMG names found</div>';
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
              <td><span class="run-check-status ${rhdRenameStatusClass(row.status)}">${escHtml(row.status || '')}</span>${row.reason ? `<div class="run-history-meta">${escHtml(row.reason)}</div>` : ''}</td>
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

function previewRhdRenames(options) {
  const opts = options || {};
  const buttonId = opts.buttonId || 'btnRenamePreview';
  const idleLabel = opts.idleLabel || 'Preview Renames';
  const payload = buildRhdRenamePayload();
  if (!payload.root) {
    _lastRhdRenamePreview = null;
    setStatus('status', 'Choose an RHD folder first', 'error');
    return Promise.resolve(null);
  }
  _lastRhdRenamePreview = null;
  _lastRhdRenamePayloadKey = rhdRenamePayloadKey(payload);
  btnBusy(buttonId, true, 'Previewing...');
  setStatus('status', 'Building rename preview...', 'loading');
  return api('/api/rhd/rename/preview', payload)
    .then(data => {
      if (data.error) throw new Error(data.error);
      renderRhdRenamePreview(data);
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
      _lastRhdRenamePreview = null;
      setStatus('status', 'Error: ' + e.message, 'error');
      return null;
    })
    .finally(() => btnBusy(buttonId, false, idleLabel));
}

async function applyRhdRenames(options) {
  const opts = options || {};
  const buttonId = opts.buttonId || 'btnRenameApply';
  const idleLabel = opts.idleLabel || 'Apply Renames';
  const basePayload = buildRhdRenamePayload();
  if (!_lastRhdRenamePreview || _lastRhdRenamePayloadKey !== rhdRenamePayloadKey(basePayload)) {
    await previewRhdRenames(opts.previewOptions || options);
  }
  if (!_lastRhdRenamePreview) return;
  if (Number(_lastRhdRenamePreview.conflict_count || 0) > 0) {
    setStatus('status', 'Resolve rename conflicts before applying', 'error');
    return;
  }
  if (!Number(_lastRhdRenamePreview.ready_count || 0)) {
    setStatus('status', 'No renames to apply', 'error');
    return;
  }

  const ok = await dpConfirmAction({
    title: 'Apply RHD renames',
    subtitle: `${_lastRhdRenamePreview.ready_count || 0} path(s) will be renamed.`,
    message: 'This changes folder and file names on disk.',
    confirmText: 'Apply Renames',
    danger: true,
  });
  if (!ok) return;

  const payload = buildRhdRenamePayload({confirm: true});
  btnBusy(buttonId, true, 'Applying...');
  setStatus('status', 'Applying RHD renames...', 'loading');
  dpRunJobEndpoint('/api/rhd/rename/apply_job', payload, {
    interval_ms: 800,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      setStatus('status', `Applying RHD renames${pct}${msg}`, 'loading');
    },
  })
    .then(data => {
      if (data.error) throw new Error(data.error);
      const changes = data.changes || [];
      const updatedRoot = data.updated_root || remapRhdPathAfterRename(payload.root, changes);
      if (updatedRoot) {
        document.getElementById('folderPath').value = updatedRoot;
      }
      if (updatedRoot && updatedRoot !== payload.root) {
        document.getElementById('renameFind').value = rhdDirName(updatedRoot);
      }
      _currentFile = remapRhdPathAfterRename(_currentFile, changes);
      _rhdFiles = remapRhdPathListAfterRename(_rhdFiles, changes);
      _queueFiles = remapRhdPathListAfterRename(_queueFiles, changes);
      queueRender();
      scanFolder({
        preserveChannel: true,
        loadProfile: true,
        selectedPath: _currentFile,
      });
      renderRhdRenamePreview({
        root: updatedRoot || document.getElementById('folderPath').value.trim(),
        scanned_count: changes.length,
        changed_count: changes.length,
        ready_count: changes.length,
        conflict_count: 0,
        changes: changes.map(row => Object.assign({status: 'renamed'}, row)),
      });
      setStatus('status', `Renamed ${data.renamed_count || 0} RHD/EMG path(s)`, 'ok');
      recordRunHistory({
        view: 'rhd_viewer',
        title: 'RHD Recording Rename',
        status: 'ok',
        project_root: updatedRoot || data.root || payload.root,
        input_files: [{path: updatedRoot || data.root || payload.root, role: 'rhd_recording_folder'}],
        outputs: data.outputs || [],
        parameters: Object.assign({}, payload, {
          original_root: payload.root,
          root: updatedRoot || payload.root,
        }),
        metadata: {renamed_count: data.renamed_count || 0},
      });
      _lastRhdRenamePreview = null;
      _lastRhdRenamePayloadKey = '';
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'))
    .finally(() => btnBusy(buttonId, false, idleLabel));
}

window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'applyRhdRenames',
  'applyQuickRhdRename',
  'autoFillRhdRenameToken',
  'buildRhdRenamePayload',
  'previewQuickRhdRename',
  'previewRhdRenames',
  'rhdRecordingToken',
  'renderRhdRenamePreview',
  'setRhdQuickRenameDefaults',
  'useCurrentFolderName',
  'useSelectedRhdName',
  'useSelectedRhdToken',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
