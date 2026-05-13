function dpRecordTelemetry(event, detail) {
  const payload = {
    event,
    view: CURRENT_VIEW || 'unknown',
    ...(detail || {}),
  };
  fetch('/api/telemetry/event', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
    keepalive: true,
  }).catch(() => {});
}
