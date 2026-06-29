function dpNormalizeApiPayload(payload, responseOk) {
  if (!payload || typeof payload !== 'object') {
    return {
      ok: !!responseOk,
      outputs: [],
      warnings: [],
      error: responseOk ? null : 'Request failed',
      value: payload,
    };
  }

  const isEnvelope = ['ok', 'data', 'outputs', 'warnings', 'error'].every(key =>
    Object.prototype.hasOwnProperty.call(payload, key)
  );
  if (!isEnvelope) {
    const ok = responseOk !== false && !payload.error;
    return Object.assign({}, payload, {
      ok,
      outputs: Array.isArray(payload.outputs) ? payload.outputs : [],
      warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
      error: ok ? null : (payload.error || 'Request failed'),
    });
  }

  const data = payload.data && typeof payload.data === 'object' && !Array.isArray(payload.data)
    ? payload.data
    : {};
  const legacyOk = Object.prototype.hasOwnProperty.call(data, 'ok') ? data.ok : payload.ok;
  return Object.assign({}, data, {
    ok: legacyOk === false ? false : payload.ok !== false,
    outputs: Array.isArray(payload.outputs) ? payload.outputs : [],
    warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
    error: payload.error || null,
  });
}

async function api(url, body, options) {
  const opts = options || {};
  const method = opts.method || 'POST';
  const fetchOptions = {
    method,
    headers: Object.assign({'Content-Type': 'application/json'}, opts.headers || {}),
  };
  if (opts.signal) fetchOptions.signal = opts.signal;
  if (Object.prototype.hasOwnProperty.call(opts, 'keepalive')) {
    fetchOptions.keepalive = !!opts.keepalive;
  }
  if (method.toUpperCase() !== 'GET') {
    fetchOptions.body = JSON.stringify(body || {});
  }
  const r = await fetch(url, fetchOptions);
  const payload = await r.json().catch(() => ({}));
  const d = dpNormalizeApiPayload(payload, r.ok);
  if (!r.ok && !d.error) d.error = 'Request failed';
  if (d.error && typeof showErrorBanner === 'function') {
    showErrorBanner(d.error, d.technical_details, d.error_id || d.id);
  }
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
  const minIntervalMs = Math.max(150, Number(opts.min_interval_ms || 250));
  const maxIntervalMs = Math.max(minIntervalMs, Number(opts.max_interval_ms || opts.interval_ms || 1000));
  const backoffAfterMs = Math.max(0, Number(opts.backoff_after_ms || 3500));
  const terminal = new Set(['succeeded', 'failed', 'cancelled']);
  const startedAt = Date.now();
  let intervalMs = minIntervalMs;
  while (true) {
    const job = await dpJobGet(jobId);
    if (typeof onUpdate === 'function') onUpdate(job);
    if (terminal.has(job.status)) return job;
    await new Promise(resolve => setTimeout(resolve, intervalMs));
    const elapsed = Date.now() - startedAt;
    if (elapsed >= backoffAfterMs) {
      intervalMs = Math.min(maxIntervalMs, Math.round(intervalMs * 1.45));
    }
  }
}

let _dpActiveJobId = '';

function dpEnsureJobCancelButton() {
  const status = document.getElementById('status');
  if (!status || document.getElementById('dpActiveJobCancel')) return document.getElementById('dpActiveJobCancel');
  const btn = document.createElement('button');
  btn.id = 'dpActiveJobCancel';
  btn.type = 'button';
  btn.className = 'btn-secondary dp-job-cancel';
  btn.textContent = 'Cancel';
  btn.hidden = true;
  btn.addEventListener('click', async () => {
    if (!_dpActiveJobId) return;
    btn.disabled = true;
    try {
      await dpJobCancel(_dpActiveJobId);
      setStatus('status', 'Cancel requested', 'warning');
    } catch (e) {
      setStatus('status', 'Cancel failed: ' + e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  });
  status.insertAdjacentElement('afterend', btn);
  return btn;
}

function dpShowJobCancel(jobId) {
  _dpActiveJobId = jobId || '';
  const btn = dpEnsureJobCancelButton();
  if (btn) btn.hidden = !jobId;
}

function dpHideJobCancel(jobId) {
  if (jobId && _dpActiveJobId && jobId !== _dpActiveJobId) return;
  _dpActiveJobId = '';
  const btn = document.getElementById('dpActiveJobCancel');
  if (btn) btn.hidden = true;
}

function dpNormalizeJobData(data) {
  if (!data || typeof data !== 'object') return {};
  if (
    data.data && typeof data.data === 'object' &&
    Object.prototype.hasOwnProperty.call(data, 'ok') &&
    Object.prototype.hasOwnProperty.call(data, 'error')
  ) {
    return Object.assign({}, data.data, {
      ok: data.ok !== false,
      outputs: Array.isArray(data.outputs) ? data.outputs : [],
      warnings: Array.isArray(data.warnings) ? data.warnings : [],
      error: data.error || null,
    });
  }
  return data;
}

async function dpRunJobEndpoint(endpoint, payload, options) {
  const opts = options || {};
  const started = await api(endpoint, payload || {});
  if (started.error) throw new Error(started.error);
  const jobId = started.job_id || (started.job && started.job.job_id);
  if (!jobId) return dpNormalizeJobData(started);
  dpShowJobCancel(jobId);

  try {
    const finalJob = await dpPollJob(jobId, opts.on_update, {
      min_interval_ms: opts.min_interval_ms || 250,
      max_interval_ms: opts.interval_ms || opts.max_interval_ms || 1000,
      backoff_after_ms: opts.backoff_after_ms || 3500,
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
  } finally {
    dpHideJobCancel(jobId);
  }
}

function showLogoutScreen(message) {
  const screen = document.createElement('div');
  screen.className = 'logout-screen';
  const card = document.createElement('div');
  card.className = 'logout-card';
  const title = document.createElement('div');
  title.className = 'logout-title';
  title.textContent = `${APP_LABEL} Closed`;
  const sub = document.createElement('div');
  sub.className = 'logout-sub';
  sub.textContent = message || 'The Python service has stopped. You can close this tab now.';
  card.append(title, sub);
  screen.appendChild(card);
  document.body.replaceChildren(screen);
}

function isLogoutDisconnectMessage(message) {
  const networkClosePattern = [
    'failed to fetch',
    'load failed',
    'networkerror',
    'network request failed',
    'connection (ended|closed|reset|lost)',
    'server disconnected',
    'cancelled',
    'aborted',
  ].join('|');
  return new RegExp(networkClosePattern, 'i').test(message || '');
}

async function logoutServer() {
  const confirmed = typeof dpConfirmAction === 'function'
    ? await dpConfirmAction({
      title: `Close ${APP_LABEL}?`,
      message: 'This will stop the local Python server. You can close this browser tab afterward.',
      confirmText: 'Close Server',
      danger: true,
    })
    : true;
  if (!confirmed) return;

  btnBusy('logoutBtn', true, 'Closing...');
  let screenShown = false;
  const showClosed = (message, delay) => {
    setTimeout(() => {
      screenShown = true;
      showLogoutScreen(message);
    }, delay);
  };
  const fallbackTimer = setTimeout(() => {
    screenShown = true;
    showLogoutScreen('Shutdown requested. DataProcess Web should be closed now.');
  }, 2000);
  try {
    const d = await api('/api/system/logout', {}, {keepalive: true});
    clearTimeout(fallbackTimer);
    if (!screenShown) {
      toast((d && d.message) || `${APP_LABEL} is closing...`);
      showClosed('The Python service has stopped. You can close this tab now.', 300);
    }
  } catch (e) {
    clearTimeout(fallbackTimer);
    const msg = (e && e.message) || '';
    if (isLogoutDisconnectMessage(msg)) {
      if (!screenShown) showClosed('Connection ended. DataProcess Web is closed.', 150);
      return;
    }
    btnBusy('logoutBtn', false, 'Log Out');
    toast('Log out failed: ' + msg, true);
  }
}
