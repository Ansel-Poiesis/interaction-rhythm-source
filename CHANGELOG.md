# Changelog

## v0.2.0 — 2026-11-27

- **Date-switching & large-database query optimization**:
  - Added three idempotent SQLite indexes (`idx_app_usage_end_start`, `idx_fg_ended_started`, `idx_obs_comp_ended_started`) and `PRAGMA busy_timeout=5000` on main and worker connections while preserving `schema_version = 2`.
  - Rewrote point-timestamp lookups in `db.py` (`_hourly_table`, `app_chart`, `hour_detail`, `daily_summary`, `app_detail`) to index-friendly range predicates, merged redundant scans in `day_stats` and `day_metrics`, split open-session (`end IS NULL`) queries with O(1) index selection and deterministic ordering in `_app_usage_for_day`, and replaced correlated `NOT EXISTS` / monthly loops in `month_heatmap`, `year_heatmap`, and `daily_keystrokes` with single-range scans plus in-memory archive merge.
  - Added `tests/test_date_query_optimization.py` covering `EXPLAIN QUERY PLAN` regression assertions, `busy_timeout`, open-session boundaries, and archive deduplication.
- **Progressive date switching on the frontend**:
  - Split primary dashboard sections (day summary, 6-day heatmap, app list) to render independently without waiting on `/api/agent/observations`.
  - Added a 12-date in-memory cache, adjacent-date prefetching, `AbortController` request cancellation, and date/sequence stale-response guards (`scripts/test_frontend_loading.js` and `tests/test_frontend_loading.py`).
  - Removed a stray closing `</script>` tag in `static/index.html` and extended `scripts/check_inline_js.js` to validate balanced `<script>` tags.
- **Collector & runtime reliability**:
  - Added bounded startup port-conflict retry in background mode (`main.py`), exception isolation with `degraded` state recording in `WindowMonitor` and `SystemStateMonitor` (`monitor.py`), read-only `DB.path` access, and `date` parameter validation for `/api/app-analysis`.
  - Included user guide, privacy-safe bug-reporting guide, structured GitHub issue template, and automated-test policy clarifications.
- This source release does not include a Windows installer or portable binary.

## v0.1.0 — 2026-11-10

- First public software source snapshot; application version metadata is v0.1.0 (epoch 2).
- Added a persistent Ansel / keeppoiesis.com watermark to the desktop interface.
- Published under MIT, with separate notices for third-party fonts and optional components.
- Removed private machine identity and default private update-feed references from source builds.
- Replaced internal research-context cards in the weekly report with project-authored interpretation rules.
- This source release does not include a Windows installer or portable binary.
