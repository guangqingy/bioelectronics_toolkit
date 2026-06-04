# Easy Start

This folder is for people who do not want to use Git or manage Python
environments by hand.

## macOS

1. Install Python 3.12 from <https://www.python.org/downloads/> if Python is not
   already installed.
2. Double-click `mac_install_and_run.command`.
3. Keep the terminal window open while using DataProcess Web.

After the first setup, use `mac_start.command`.

If macOS blocks the file because it was downloaded from the internet:

```bash
chmod +x easy_start/*.command
```

Then double-click it again.

## Windows

1. Install Python 3.12 from <https://www.python.org/downloads/> and tick
   "Add Python to PATH" during installation.
2. Double-click `windows_install_and_run.bat`.
3. Keep the command window open while using DataProcess Web.

After the first setup, use `windows_start.bat`.

## What These Scripts Do

- Create a local `.venv` folder inside this project.
- Install DataProcess and its Python dependencies into that folder.
- Run a self-check with the bundled example data.
- Start the local Web app at <http://127.0.0.1:7433>.

They do not change system Python, Conda, or other projects.
