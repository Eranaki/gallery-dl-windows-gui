# gallery-dl Windows GUI

First working MVP of a desktop GUI wrapper for `gallery-dl` built with `PySide6`.

## Included in this version

- main downloads screen;
- paste one or more URLs;
- choose a download folder;
- task queue;
- `Check` and `Download` actions;
- live execution log;
- right-side advanced settings panel;
- `History` and `Settings` tabs;
- `gallery-dl` launched as an external process.

## Setup

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
py -3 -m pip install -U pip
py -3 -m pip install -e .
```

This installs both the GUI and `gallery-dl` into the local virtual environment.

## Run

```powershell
.\run_gui.cmd
```

Or directly:

```powershell
.\.venv\Scripts\python.exe main.py
```

## Build EXE

```powershell
.\build_exe.cmd
```

The packaged application will be created in:

```text
dist\gallery-dl-windows-gui\
```

Main files after build:

```text
dist\gallery-dl-windows-gui\gallery-dl-windows-gui.exe
dist\gallery-dl-windows-gui\gallery-dl-windows-gui-cli.exe
```

The GUI starts from `gallery-dl-windows-gui.exe`. The companion CLI EXE is
used internally so the packaged app can run bundled `gallery-dl` commands
without requiring a separate system Python installation.

## Current MVP limits

- tasks run one after another;
- progress is currently task-level, not exact file counting;
- many advanced `gallery-dl` options are not yet mapped into dedicated GUI controls.
