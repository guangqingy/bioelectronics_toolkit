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

function previewRhdRenames() {
  const payload = buildRhdRenamePayload();
  if (!payload.root) {
    _lastRhdRenamePreview = null;
    setStatus('status', 'Choose an RHD folder first', 'error');
    return Promise.resolve(null);
  }
  _lastRhdRenamePreview = null;
  _lastRhdRenamePayloadKey = rhdRenamePayloadKey(payload);
  btnBusy('btnRenamePreview', true, 'Previewing...');
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
    .finally(() => btnBusy('btnRenamePreview', false, 'Preview Renames'));
}

async function applyRhdRenames() {
  const basePayload = buildRhdRenamePayload();
  if (!_lastRhdRenamePreview || _lastRhdRenamePayloadKey !== rhdRenamePayloadKey(basePayload)) {
    await previewRhdRenames();
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
  btnBusy('btnRenameApply', true, 'Applying...');
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
      const rootChange = changes.find(row => row.source_path === payload.root);
      if (rootChange?.target_path) {
        document.getElementById('folderPath').value = rootChange.target_path;
        document.getElementById('renameFind').value = rhdDirName(rootChange.target_path);
        scanFolder();
      }
      renderRhdRenamePreview({
        root: document.getElementById('folderPath').value.trim(),
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
        project_root: data.root || payload.root,
        input_files: [{path: data.root || payload.root, role: 'rhd_recording_folder'}],
        outputs: data.outputs || [],
        parameters: payload,
        metadata: {renamed_count: data.renamed_count || 0},
      });
      _lastRhdRenamePreview = null;
      _lastRhdRenamePayloadKey = '';
    })
    .catch(e => setStatus('status', 'Error: ' + e.message, 'error'))
    .finally(() => btnBusy('btnRenameApply', false, 'Apply Renames'));
}

window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'applyRhdRenames',
  'buildRhdRenamePayload',
  'previewRhdRenames',
  'renderRhdRenamePreview',
  'useCurrentFolderName',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
