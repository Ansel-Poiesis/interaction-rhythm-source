# Contributing

Thanks for helping improve 扣舷-数字节律. Contributions from people using coding agents are welcome.

## Before opening a pull request

1. Describe the user problem and the change.
2. For each major behavior change, add or update tests in the automated test suite. If a test is not practical, explain why in the pull request. For smaller changes, list the relevant checks you ran.
3. Run the checks listed in [AGENTS.md](AGENTS.md) and report which ones ran.
4. Use synthetic fixtures. Do not attach databases, screenshots, logs, exports, email addresses, API keys, OAuth data, or other personal information.
5. List any copied or generated code, fonts, images, research text, or other third-party materials and their license/source.

## Local checks

    python -m pytest tests -q
    python -m compileall -q config.py db.py diagnostics.py interaction_rhythm main.py mimo_client.py monitor.py settings.py startup_guard.py stats.py tray.py update_manager.py weekly_report.py window.py
    node scripts/check_inline_js.js

Do not start the application just to run tests: a normal app launch starts local activity collection. See [data and privacy](docs/data-and-privacy.md) before manual runs.

## Licensing

The project uses MIT for project code and original documentation. Existing third-party files retain their own licenses. Please submit only material you have the right to contribute, and do not represent an AI system as a human copyright holder or signer.
