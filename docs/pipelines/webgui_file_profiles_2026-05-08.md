# Web GUI File Profiles

Date: 2026-05-08

The GUI now separates two kinds of saved state:

- `.dataprocess_cache/web_gui_settings.json`: global and per-interface defaults used when a page opens.
- `.dataprocess_cache/file_profiles.json`: per-project, per-file profiles used when reopening the same data file.

The file profile cache is written under the selected project/data folder:

```text
Project_Folder/
  sample_001.tif
  sample_002.tif
  .dataprocess_cache/
    file_profiles.json
```

This avoids scattering sidecar files next to every TIFF while still making the cache browser-independent. Any browser using the same Web GUI and the same data folder can read the same `.dataprocess_cache/file_profiles.json`.

## Current Coverage

The implementation is connected to:

- TIFF Browser: display settings and per-stack export settings.
- ROI Analysis: analysis settings, ROI geometry, and analysis queue when manually loading a profile.
- GIF Builder: GIF settings, queue, ROI polygons, and crop rectangles when manually loading a profile.
- CSV Viewer: columns, plot window, downsample, merge queue.
- ABF Viewer: sweep/channel/baseline/window settings and export queue.
- EChem Photocurrent / Photovoltage: detection parameters and detected/removed events.
- RHD Viewer: channel/window/export settings and batch queue.
- EMG Peaks: peak detection settings and grouped/removed peak edits.

When a file is opened and no profile exists, the GUI creates a `default` profile automatically. Use `Save` to update it, or `Save As` to keep multiple named parameter sets for the same file.

## Cache Shape

```json
{
  "version": 1,
  "project_root": "/path/to/project",
  "updated_at": "2026-05-08T20:00:00+00:00",
  "files": {
    "sample_001.tif": {
      "path": "/path/to/project/sample_001.tif",
      "fingerprint": {
        "exists": true,
        "size": 17824512,
        "mtime_ns": 1776472920000000000
      },
      "views": {
        "fluorescence_roi": {
          "last_profile": "default",
          "profiles": {
            "default": {
              "settings": {},
              "payload": {},
              "updated_at": "2026-05-08T20:00:00+00:00"
            }
          }
        }
      }
    }
  }
}
```

If a file changes on disk, the API marks the profile response as `stale` by comparing size and modified time.
