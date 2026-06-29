let _files = [];
let _latestFile = null;
const POWER_PRESETS = {
  '10-step-normal': '3.71, 2.79, 1.86, 0.995, 0.648, 0.553, 0.308, 0.100, 0.0574, 0.0210',
  '10-step-20x': '92.75, 69.75, 46.50, 24.875, 16.20, 13.825, 7.70, 2.50, 1.435, 0.525',
  '12-step-old': '99.822, 86.014, 44.818, 16.071, 8.828, 3.169, 0.656, 0.556, 0.31, 0.101, 0.058, 0.021',
  '25-step-wide': '103.6, 90.9, 80, 70, 60, 50, 20, 15, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0.86, 0.68, 0.6, 0.47, 0.36, 0.22, 0.075'
};

function applyPowerPreset() {
  const key = document.getElementById('powerPreset').value;
  if (key === 'custom') {
    document.getElementById('powers').value = '';
  } else if (POWER_PRESETS[key]) {
    document.getElementById('powers').value = POWER_PRESETS[key];
  }
}

function syncSegmentControls() {
  const manual = document.getElementById('segmentMode').value === 'manual';
  document.getElementById('segmentT0').disabled = !manual;
  document.getElementById('segmentT1').disabled = !manual;
}

function updateTokenSuggestions(listId, tokens) {
  const list = document.getElementById(listId);
  if (!list) return;
  list.innerHTML = '';
  (tokens || []).forEach(token => {
    const option = document.createElement('option');
    option.value = token;
    list.appendChild(option);
  });
}

function formatDetectedTokens(tokens, counts) {
  return (tokens || []).map(token => {
    const count = counts && counts[token];
    return count ? `${token} (${count})` : token;
  }).join(', ');
}

function tokenListText(tokens) {
  return (tokens || []).filter(Boolean).join(', ');
}

function scanFolder() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) {
    setStatus('status', 'Enter folder path', 'error');
    toast('Enter folder path', true);
    return;
  }

  setStatus('status', 'Scanning folder...', 'loading');
  api('/api/abf_batch/browse', { folder })
    .then(r => {
      if (r.error) throw new Error(r.error);
      _files = DP.liveFolder.normalizeFiles(r.files || []);
      _latestFile = DP.liveFolder.newestFile(_files);
      document.getElementById('fileCount').textContent = _files.length + ' files';
      const refreshInfo = document.getElementById('folderRefreshInfo');
      if (refreshInfo) refreshInfo.textContent = DP.liveFolder.infoText(_files, [], _latestFile);

      if (_files.length === 0) {
        document.getElementById('fileTableWrap').innerHTML = '<div class="file-list-empty">No ABF files found</div>';
        setStatus('status', 'No files found', 'error');
        return;
      }

      const cols = ['File', 'Size (bytes)', 'Channels'];
      const rows = _files.slice(0, 100).map(f => ({
        'File': f.name || f,
        'Size (bytes)': f.size || '\u2014',
        'Channels': f.channels || '\u2014'
      }));
      document.getElementById('fileTableWrap').innerHTML = buildTable(rows, cols);
      setStatus('status', r.truncated ? 'Showing first 300 matching files' : 'Ready', r.truncated ? 'warning' : 'ok');
    })
    .catch(e => {
      setStatus('status', 'Scan failed', 'error');
      toast('Scan failed: ' + e.message, true);
    });
}

function suggestTokens() {
  if (_files.length === 0) {
    toast('Scan folder first', true);
    return;
  }

  setStatus('status', 'Analyzing filenames...', 'loading');
  const fileNames = _files.map(f => typeof f === 'string' ? f : f.name);

  api('/api/abf_batch/scan_tokens', { files: fileNames })
    .then(r => {
      if (r.error) throw new Error(r.error);
      const mains = Array.isArray(r.mains) ? r.mains : [];
      const treats = Array.isArray(r.treats) ? r.treats : [];
      updateTokenSuggestions('mainTokenSuggestions', mains);
      updateTokenSuggestions('treatTokenSuggestions', treats);
      if (mains.length) document.getElementById('mainToken').value = tokenListText(mains);
      if (treats.length) document.getElementById('treatToken').value = tokenListText(treats);

      if (mains.length || treats.length) {
        const parts = [];
        if (mains.length) parts.push('main: ' + formatDetectedTokens(mains, r.main_counts));
        if (treats.length) parts.push('treat: ' + formatDetectedTokens(treats, r.treat_counts));
        setStatus('status', 'Tokens suggested (' + parts.join('; ') + ')', 'ok');
        toast('Tokens auto-detected');
      } else {
        setStatus('status', 'No filename tokens matched the expected pattern', 'warning');
        toast('No matching filename tokens found', true);
      }
    })
    .catch(e => {
      setStatus('status', 'Suggestion failed', 'error');
      toast('Could not suggest tokens: ' + e.message, true);
    });
}

function renderOperationPlan(plan) {
  if (!plan || !plan.length) {
    return '<div class="file-list-empty">No filesystem moves or renames planned.</div>';
  }
  const rows = plan.slice(0, 100).map(op => ({
    'Action': op.action || '',
    'From': op.source || '',
    'To': op.destination || ''
  }));
  return buildTable(rows, ['Action', 'From', 'To']);
}

async function runBatch(options) {
  const opts = options || {};
  const pure_csv = Boolean(opts.pureCsv);
  const folder = document.getElementById('folderPath').value.trim();
  const main = document.getElementById('mainToken').value.trim();
  const treat = document.getElementById('treatToken').value.trim();
  const powers = document.getElementById('powers').value.trim();
  const i_ch = parseInt(document.getElementById('iCh').value, 10);
  const v_ch = parseInt(document.getElementById('vCh').value, 10);
  const analog_ch = parseInt(document.getElementById('analogCh').value, 10);
  const segment_mode = document.getElementById('segmentMode').value;
  const segment_t0 = parseFloat(document.getElementById('segmentT0').value);
  const segment_t1 = parseFloat(document.getElementById('segmentT1').value);
  const move_files = document.getElementById('moveFiles').checked;
  const reindex_seq = document.getElementById('reindexSeq').checked;
  const dry_run = document.getElementById('dryRun').checked;

  if (!folder || !main || !treat) {
    setStatus('status', 'Enter folder, main token, and treatment token before running batch', 'error');
    toast('Enter all required parameters', true);
    return;
  }

  const payload = {
    folder, main, treat, powers,
    i_ch, v_ch, analog_ch,
    segment_mode, segment_t0, segment_t1, save_segments: true,
    pure_csv,
    move_files, reindex_seq, dry_run
  };

  if ((move_files || reindex_seq) && !dry_run) {
    const total = _files.length || 'all matching';
    setStatus('status', 'Waiting for confirmation before moving or renaming files...', 'warning');
    const confirmed = await dpConfirmAction({
      title: 'Apply ABF filesystem changes?',
      subtitle: 'Dry run is off, so this can move or rename files on disk.',
      danger: true,
      confirmText: 'Apply Changes',
      html: `
        <div>This run may modify <strong>${dpEscapeHtml(total)}</strong> ABF file(s).</div>
        <ul>
          <li>Move destination pattern: <code>${dpEscapeHtml(folder)}/${dpEscapeHtml(main)}_${dpEscapeHtml(treat)}/sample_{id}/</code></li>
          <li>Sequence renumbering: <strong>${reindex_seq ? 'enabled' : 'disabled'}</strong></li>
          <li>A small operation log is written only when files are moved or renamed.</li>
        </ul>
      `,
    });
    if (!confirmed) {
      setStatus('status', 'Batch cancelled before filesystem changes', 'warning');
      return;
    }
  }

  const busyButtonId = pure_csv ? 'btnPureCsv' : 'btnRun';
  const idleLabel = pure_csv ? 'Pure CSV Conversion' : 'Run Batch';
  btnBusy(busyButtonId, true, dry_run ? 'Planning...' : (pure_csv ? 'Converting...' : 'Processing...'));
  setStatus('status', dry_run ? 'Planning batch operation...' : (pure_csv ? 'Running pure CSV conversion...' : 'Running batch analysis...'), 'loading');

  dpRunJobEndpoint('/api/abf_batch/process_job', payload, {
    interval_ms: 1000,
    on_update: job => {
      const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
      const msg = job.message ? ` · ${job.message}` : '';
      const jobVerb = pure_csv ? 'Running pure CSV conversion' : 'Running batch analysis';
      setStatus('status', `${jobVerb}${pct}${msg}`, 'loading');
    },
  })
    .then(r => {
      if (r.error) throw new Error(r.error);
      if (r.dry_run) {
        document.getElementById('resultBody').innerHTML = `
          ${renderOperationPlan(r.plan || [])}
          <div class="status-bar status-ok" style="padding-top:8px">
            <span class="status-text">Dry run does not write an operation log.</span>
          </div>
        `;
        document.getElementById('resultCard').style.display = 'block';
        document.getElementById('resultHeader').textContent = 'Dry Run Plan (' + (r.planned_count || 0) + ' operation(s))';
        setStatus('status', 'Dry run complete: ' + (r.planned_count || 0) + ' operation(s) planned', 'ok');
        toast('Dry run complete');
        recordRunHistory({
          view: 'abf_batch',
          title: 'ABF Batch Dry Run',
          status: 'ok',
          project_root: folder,
          input_files: (_files || []).map(f => ({path: (typeof f === 'string' ? f : f.path), role: 'source_abf'})).filter(f => f.path),
          outputs: r.operation_log_path ? [{path: r.operation_log_path, type: 'operation_log'}] : [],
          parameters: payload,
          warnings: r.warnings || [],
          metadata: {
            planned_count: r.planned_count || 0,
            moved_count: r.moved_count || 0,
            renamed_count: r.renamed_count || 0,
          },
        });
        return;
      }
      const rows = (r.results || []).map(row => ({
        'File': row.file || '',
        'Status': row.status || 'ok',
        'Main': row.main_val || '\u2014',
        'Treat': row.treat_val || '\u2014',
        'Segment': row.segment_csv ? 'saved' : '\u2014'
      }));

      const emptyMessage = rows.length === 0
        ? '<div class="status-bar status-warning"><span class="status-text">' + dpEscapeHtml(r.message || 'No matching files processed. Check filename tokens and expected _sample_ filename pattern.') + '</span></div>'
        : '';
      const warnings = Array.isArray(r.warnings) && r.warnings.length
        ? '<div class="status-bar status-warning" style="margin-top:8px">' + r.warnings.map(dpEscapeHtml).join('<br>') + '</div>'
        : '';
      document.getElementById('resultBody').innerHTML = emptyMessage + buildTable(rows, ['File', 'Status', 'Main', 'Treat', 'Segment']) + warnings;
      document.getElementById('resultCard').style.display = 'block';
      const mv = Number(r.moved_count || 0);
      const rn = Number(r.renamed_count || 0);
      const wn = Array.isArray(r.warnings) ? r.warnings.length : 0;
      document.getElementById('resultHeader').textContent = (pure_csv ? 'Pure CSV Results' : 'Results') + ' (' + rows.length + ' files | moved=' + mv + ' | renamed=' + rn + ' | warn=' + wn + ')';
      setStatus('status', rows.length ? (r.message || (pure_csv ? 'Pure CSV conversion complete' : 'Batch complete')) : (r.message || 'No matching files processed'), rows.length ? 'ok' : 'warning');
      toast(pure_csv ? 'Pure CSV conversion complete' : 'Batch processing complete');
      recordRunHistory({
        view: 'abf_batch',
        title: pure_csv ? 'ABF Pure CSV Conversion' : 'ABF Batch Processing',
        status: wn ? 'warning' : 'ok',
        project_root: folder,
        input_files: (_files || []).map(f => ({path: (typeof f === 'string' ? f : f.path), role: 'source_abf'})).filter(f => f.path),
        outputs: Array.isArray(r.outputs) ? r.outputs : [
          ...(r.csv_path ? [{path: r.csv_path, type: 'summary_csv'}] : []),
          ...(r.operation_log_path ? [{path: r.operation_log_path, type: 'operation_log'}] : []),
        ],
        parameters: payload,
        warnings: r.warnings || [],
        metadata: {
          processed: r.n || rows.length,
          moved_count: mv,
          renamed_count: rn,
        },
      });
    })
    .catch(e => {
      setStatus('status', pure_csv ? 'Pure CSV conversion failed' : 'Batch failed', 'error');
      toast((pure_csv ? 'Pure CSV conversion failed: ' : 'Batch failed: ') + e.message, true);
    })
    .finally(() => btnBusy(busyButtonId, false, idleLabel));
}

window.addEventListener('load', () => {
  document.getElementById('powerPreset').value = 'custom';
  syncSegmentControls();
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'applyPowerPreset',
  'renderOperationPlan',
  'runBatch',
  'scanFolder',
  'suggestTokens',
  'syncSegmentControls',
  'tokenListText',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
