# Telemetry Protocol

Telemetry is an opt-in local usage counter for the WebGUI. It is disabled by
default and only records events when `global.telemetry_enabled` is `true` in
`.dataprocess_cache/web_gui_settings.json`.

The client must fail silently. Telemetry errors should not show the persistent
error banner, toast, or inline status because analytics must never interrupt a
research workflow.

## Privacy Contract

Recorded:

- WebGUI version
- Event type
- View name, such as `abf_viewer` or `fluorescence_gif`
- Export action label/type
- Aggregate counter and update timestamp

Never recorded:

- File paths
- File names
- Parameter values
- Data contents
- User-entered notes

Telemetry is currently stored locally in `.dataprocess_cache/telemetry.json`.
The `remote_url_configured` flag only records whether
`DATAPROCESS_TELEMETRY_URL` is set; no remote upload is performed by the current
WebGUI client.

## Endpoints

### `POST /api/telemetry/page`

Request:

```json
{
  "view": "abf_viewer"
}
```

Records one `page_open:<view>` counter.

Response:

```json
{
  "ok": true,
  "enabled": true,
  "recorded": true
}
```

When telemetry is disabled, the endpoint returns `enabled: false` and
`recorded: false`.

### `POST /api/telemetry/export`

Request:

```json
{
  "view": "rhd_viewer",
  "export_type": "Export SVG"
}
```

Records one `export_click:<view>:<export_type>` counter.

### `POST /api/telemetry/event`

Compatibility endpoint for older clients. Supported `event` values are
`page_open`, `export_click`, and `startup`.

Request:

```json
{
  "event": "export_click",
  "view": "csv_viewer",
  "label": "Export CSV"
}
```

New code should prefer the explicit `/page` and `/export` endpoints because they
show up more clearly in OpenAPI docs.

## Frontend API

`web_static/js/dp_telemetry.js` exposes:

- `DP.telemetry.pageOpen(viewName)`
- `DP.telemetry.exportClick(exportType, viewName)`
- `DP.telemetry.record(event, detail)` for compatibility

All three helpers check saved preferences before sending and swallow network or
server failures.
