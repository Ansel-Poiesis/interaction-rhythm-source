# Agent guide

This guide describes the public source tree. It contains no machine-specific paths or private project records.

## Project map

- main.py, stats.py, monitor.py: Windows desktop startup, local API, and event collection.
- interaction_rhythm/: collector, database-facing services, metrics, AI/email helpers, and reporting contracts.
- static/: local web interface and bundled fonts.
- tests/: automated checks with synthetic/local fixtures.
- docs/data-and-privacy.md: collection, storage, and outbound-data boundaries.
- docs/building-from-source.md: environment and verification commands.
- docs/user-guide.md and docs/bug-reporting.md: basic workflows and safe public issue reporting.
- docs/reporting/: public report schema, project-authored method rules, and source index.

## Working safely

- Do not commit data/, dist/, build/, .venv/, tmp/, databases, logs, screenshots, mail, settings, exports, keys, or tokens.
- Do not start the desktop app unless the task explicitly requires a manual run. A normal launch begins local activity collection.
- Use synthetic data and temporary directories for tests. Never use a real database to validate changes.
- Public software version v0.1.0 uses the existing epoch 2 update ordering; this source release has no Windows binary.
- The updater is disabled by default for source builds; do not point it at private feeds.
- Check source and license for any external code, data, font, image, research text, or executable added to the repository.

## Verification

Run python -m pytest tests -q, python -m compileall -q over the Python source files, and node scripts/check_inline_js.js. Report commands that were not run. See the build guide for complete commands.
