# Building from source

## Requirements

- Windows 10 or 11.
- Python 3.10 or later; the CI job currently uses Python 3.11.
- Microsoft Edge WebView2 Runtime for the desktop window.
- Node.js only for the inline JavaScript syntax check.

## Create an environment

    py -3.11 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt

## Run checks

    .\.venv\Scripts\python.exe -m pytest tests -q
    .\.venv\Scripts\python.exe -m compileall -q config.py db.py diagnostics.py interaction_rhythm main.py mimo_client.py monitor.py settings.py startup_guard.py stats.py tray.py update_manager.py weekly_report.py window.py
    node scripts/check_inline_js.js

Tests use temporary databases and synthetic fixtures. They do not require starting the desktop application.

## Run the desktop app

Read [data and privacy](data-and-privacy.md) first. Starting the app activates local activity collection. For a development run, use an isolated data directory:

    $env:INTERACTION_RHYTHM_DATA_DIR = Join-Path $env:TEMP 'digital-rhythm-dev-data'
    .\.venv\Scripts\python.exe main.py

The source build has no update feed and automatic updates are disabled by default. This source release does not provide an installer or portable executable.

## Optional QQ Agent Mail transport

The QQ Agent Mail transport needs npm and downloads the separately licensed Tencent CLI. To prepare its local executable, run:

    .\scripts\prepare_agently_cli.ps1

This downloads the pinned 1.0.18 packages into ignored local build output. Review [third-party notices](../THIRD_PARTY_NOTICES.md) before using this optional bridge. It is not required to run the app or tests.
