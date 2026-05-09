/* Lightweight background job monitor used by the Settings panel. */

function dpJobStatusClass(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'succeeded') return 'ok';
  if (s === 'failed' || s === 'cancelled') return 'bad';
  if (s === 'running' || s === 'pending') return 'warn';
  return '';
}

function renderBackgroundJobs(jobs) {
  const list = document.getElementById('backgroundJobList');
  if (!list) return;
  if (!Array.isArray(jobs) || !jobs.length) {
    list.innerHTML = '<div class="prefs-sub">No background jobs in memory.</div>';
    return;
  }
  list.innerHTML = jobs.slice(0, 25).map(job => {
    const status = dpEscapeHtml(job.status || '');
    const cls = dpJobStatusClass(job.status);
    const progress = job.progress === null || job.progress === undefined ? '' : ` · ${Math.round(Number(job.progress || 0) * 100)}%`;
    const canCancel = ['pending', 'running'].includes(String(job.status || '').toLowerCase());
    const outputs = Array.isArray(job.outputs) ? job.outputs : [];
    const firstOutput = outputs.length
      ? (typeof outputs[0] === 'string' ? outputs[0] : (outputs[0] && (outputs[0].path || outputs[0].output_path || outputs[0].saved_path)))
      : '';
    const outputLine = outputs.length
      ? `<div class="run-history-meta">Outputs: ${outputs.length}${firstOutput ? ` · <code>${dpEscapeHtml(firstOutput)}</code>` : ''}</div>`
      : '';
    return `
      <div class="run-history-row">
        <div class="run-history-title">${dpEscapeHtml(job.title || job.kind || 'Job')}</div>
        <div class="run-history-meta">
          <span class="run-check-status ${cls}">${status}</span>${progress} · ${dpEscapeHtml(job.created_at || '')}
        </div>
        <div class="run-history-meta">${dpEscapeHtml(job.message || job.error || job.job_id || '')}</div>
        ${outputLine}
        ${canCancel ? `<button class="btn-secondary btn-block" style="margin-top:6px" type="button" onclick="cancelBackgroundJob('${dpEscapeHtml(job.job_id || '')}')">Cancel</button>` : ''}
      </div>
    `;
  }).join('');
}

async function loadBackgroundJobs(silent) {
  try {
    const jobs = await dpJobList(50, true);
    renderBackgroundJobs(jobs);
    if (!silent) setStatus('prefsStatus', `Loaded ${jobs.length} background job(s).`, 'ok');
  } catch (e) {
    renderBackgroundJobs([]);
    if (!silent) setStatus('prefsStatus', 'Job list unavailable: ' + e.message, 'warning');
  }
}

async function cancelBackgroundJob(jobId) {
  if (!jobId) return;
  try {
    await dpJobCancel(jobId);
    await loadBackgroundJobs(true);
    setStatus('prefsStatus', 'Cancel requested for job ' + jobId, 'ok');
  } catch (e) {
    setStatus('prefsStatus', 'Cancel failed: ' + e.message, 'error');
  }
}
