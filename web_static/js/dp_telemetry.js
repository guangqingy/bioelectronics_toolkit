/*
 * Opt-in local telemetry client.
 *
 * Telemetry is disabled unless the saved GUI preferences contain
 * global.telemetry_enabled === true. Failures are intentionally silent because
 * usage counters must never interrupt analysis workflows.
 */
(function () {
  window.DP = window.DP || {};
  window.DP.telemetry = window.DP.telemetry || {};

  function currentViewName(viewName) {
    if (viewName) return String(viewName).slice(0, 80) || 'unknown';
    if (typeof CURRENT_VIEW !== 'undefined' && CURRENT_VIEW) return String(CURRENT_VIEW).slice(0, 80);
    return 'unknown';
  }

  async function telemetryEnabled() {
    try {
      if ((typeof _prefsCache === 'undefined' || !_prefsCache) && typeof loadPreferences === 'function') {
        await loadPreferences();
      }
      const global = (typeof _prefsCache !== 'undefined' && _prefsCache && _prefsCache.global) ? _prefsCache.global : {};
      return global.telemetry_enabled === true;
    } catch (_err) {
      return false;
    }
  }

  async function postTelemetry(endpoint, payload) {
    if (!(await telemetryEnabled())) return {enabled: false, recorded: false};
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload || {}),
        keepalive: true,
      });
      if (!response.ok) return {enabled: true, recorded: false};
      return await response.json();
    } catch (_err) {
      return {enabled: true, recorded: false};
    }
  }

  function pageOpen(viewName) {
    return postTelemetry('/api/telemetry/page', {view: currentViewName(viewName)});
  }

  function exportClick(exportType, viewName) {
    return postTelemetry('/api/telemetry/export', {
      view: currentViewName(viewName),
      export_type: String(exportType || '').slice(0, 80),
    });
  }

  function record(event, detail) {
    const payload = detail && typeof detail === 'object' ? detail : {};
    const eventName = String(event || '').trim();
    if (eventName === 'page_open') return pageOpen(payload.view);
    if (eventName === 'export_click') return exportClick(payload.export_type || payload.label || '', payload.view);
    return postTelemetry('/api/telemetry/event', {
      event: eventName,
      view: currentViewName(payload.view),
      label: String(payload.label || '').slice(0, 80),
    });
  }

  window.dpRecordTelemetry = record;
  Object.assign(window.DP.telemetry, {record, pageOpen, exportClick});
})();
