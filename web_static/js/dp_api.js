function dpNormalizeApiPayload(payload, responseOk) {
  if (!payload || typeof payload !== 'object') {
    return {
      ok: !!responseOk,
      data: {},
      outputs: [],
      warnings: [],
      error: responseOk ? null : 'Request failed',
      value: payload,
      _envelope: null,
    };
  }

  const isEnvelope = ['ok', 'data', 'outputs', 'warnings', 'error'].every(key =>
    Object.prototype.hasOwnProperty.call(payload, key)
  );
  if (!isEnvelope) {
    const ok = responseOk !== false && !payload.error;
    return Object.assign({}, payload, {
      ok,
      data: ok ? Object.assign({}, payload) : {},
      outputs: Array.isArray(payload.outputs) ? payload.outputs : [],
      warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
      error: ok ? null : (payload.error || 'Request failed'),
      _envelope: null,
    });
  }

  const data = payload.data && typeof payload.data === 'object' && !Array.isArray(payload.data)
    ? payload.data
    : {};
  const legacyOk = Object.prototype.hasOwnProperty.call(data, 'ok') ? data.ok : payload.ok;
  return Object.assign({}, data, payload, {
    ok: legacyOk === false ? false : payload.ok !== false,
    outputs: Array.isArray(payload.outputs) ? payload.outputs : [],
    warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
    error: payload.error || null,
    _envelope: payload,
  });
}

async function api(url, body, options) {
  const opts = options || {};
  const method = opts.method || 'POST';
  const fetchOptions = {
    method,
    headers: Object.assign({'Content-Type': 'application/json'}, opts.headers || {}),
  };
  if (method.toUpperCase() !== 'GET') {
    fetchOptions.body = JSON.stringify(body || {});
  }
  const r = await fetch(url, fetchOptions);
  const payload = await r.json().catch(() => ({}));
  const d = dpNormalizeApiPayload(payload, r.ok);
  if (!r.ok && !d.error) d.error = 'Request failed';
  return d;
}

async function dpJobGet(jobId) {
  const d = await api('/api/jobs/get', {job_id: jobId});
  if (d.error) throw new Error(d.error);
  return d.job;
}

async function dpJobList(limit, includeFinished) {
  const d = await api('/api/jobs/list', {
    limit: limit || 50,
    include_finished: includeFinished !== false,
  });
  if (d.error) throw new Error(d.error);
  return d.jobs || [];
}

async function dpJobCancel(jobId) {
  const d = await api('/api/jobs/cancel', {job_id: jobId});
  if (d.error) throw new Error(d.error);
  return d.job;
}

async function dpPollJob(jobId, onUpdate, options) {
  const opts = options || {};
  const intervalMs = Math.max(250, Number(opts.interval_ms || 1000));
  const terminal = new Set(['succeeded', 'failed', 'cancelled']);
  while (true) {
    const job = await dpJobGet(jobId);
    if (typeof onUpdate === 'function') onUpdate(job);
    if (terminal.has(job.status)) return job;
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
}

function dpNormalizeJobData(data) {
  if (!data || typeof data !== 'object') return {};
  if (
    data.data && typeof data.data === 'object' &&
    Object.prototype.hasOwnProperty.call(data, 'ok') &&
    Object.prototype.hasOwnProperty.call(data, 'error')
  ) {
    return Object.assign({}, data.data, data);
  }
  return data;
}

async function dpRunJobEndpoint(endpoint, payload, options) {
  const opts = options || {};
  const started = await api(endpoint, payload || {});
  if (started.error) throw new Error(started.error);
  const jobId = started.job_id || (started.job && started.job.job_id);
  if (!jobId) return dpNormalizeJobData(started);

  const finalJob = await dpPollJob(jobId, opts.on_update, {
    interval_ms: opts.interval_ms || 1000,
  });
  const data = dpNormalizeJobData(finalJob.data || {});
  if ((!Array.isArray(data.outputs) || !data.outputs.length) && Array.isArray(finalJob.outputs) && finalJob.outputs.length) {
    data.outputs = finalJob.outputs;
  }
  if ((!Array.isArray(data.warnings) || !data.warnings.length) && Array.isArray(finalJob.warnings) && finalJob.warnings.length) {
    data.warnings = finalJob.warnings;
  }
  if (finalJob.status === 'failed' || finalJob.status === 'cancelled') {
    throw new Error(finalJob.error || data.error || finalJob.message || 'Background job failed');
  }
  if (data.error) throw new Error(data.error);
  return data;
}

function showLogoutScreen(message) {
  document.body.innerHTML = `
    <div class="logout-screen">
      <div class="logout-card">
        <div class="logout-title">${APP_LABEL} Closed</div>
        <div class="logout-sub">${message || 'The Python service has stopped. You can close this tab now.'}</div>
      </div>
    </div>
  `;
}

async function logoutServer() {
  if (!confirm(`Close ${APP_LABEL} and stop the Python server?`)) return;

  btnBusy('logoutBtn', true, 'Closing...');
  try {
    const d = await api('/api/system/logout', {});
    toast((d && d.message) || `${APP_LABEL} is closing...`);
    setTimeout(() => showLogoutScreen('The Python service has stopped. You can close this tab now.'), 300);
  } catch (e) {
    const msg = (e && e.message) || '';
    if (/failed to fetch|networkerror/i.test(msg)) {
      setTimeout(() => showLogoutScreen('Connection ended. DataProcess Web is closed.'), 150);
      return;
    }
    btnBusy('logoutBtn', false, 'Log Out');
    toast('Log out failed: ' + msg, true);
  }
}
