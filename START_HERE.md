# Start Here

This is the simple entry point for non-developer users.

You do **not** need Git. You do **not** need to manually activate a Python
environment.

## First-Time Setup

### macOS

Double-click:

```text
easy_start/mac_install_and_run.command
```

### Windows

Double-click:

```text
easy_start/windows_install_and_run.bat
```

The script will create a local `.venv` folder, install the required Python
packages, run a quick self-check, and start DataProcess Web.

## Normal Use After Setup

### macOS

Double-click:

```text
easy_start/mac_start.command
```

### Windows

Double-click:

```text
easy_start/windows_start.bat
```

Then use the browser page that opens at:

```text
http://127.0.0.1:7433
```

Leave the terminal/command window open while using the app. Close it, or press
`Ctrl+C`, to stop the local server.

## If Setup Says Python Is Missing

Install Python 3.12 from:

```text
https://www.python.org/downloads/
```

Then run the install script again.

## What Files Matter To Regular Users

- `START_HERE.md`: this guide.
- `easy_start/`: double-click setup/start scripts.
- `examples/`: bundled sample files.
- `web_app.py`: the local Web app entry point, used by the scripts.

You can safely ignore `services/`, `web_api/`, `web_static/`, `web_templates/`,
`tests/`, `docs/`, `dev_scripts/`, `.github/`, and `pipelines/` unless you are
developing the toolkit.
