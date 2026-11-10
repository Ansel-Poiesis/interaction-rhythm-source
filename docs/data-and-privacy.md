# Data and privacy

扣舷-数字节律 is a local-first Windows desktop tool. Its source being public does not upload the user's local data.

## Collected on the device

The collector stores timestamped counts of keyboard input events, mouse-button events, and scroll events. It also records the foreground process name, executable path, foreground interval, and collector health state. The current collector does not read typed characters, window titles, browser page content, mouse coordinates, or screen images.

The app's SQLite database, settings, icon cache, and local diagnostic files live under the application data directory. Source runs use data/ under the repository by default. Set INTERACTION_RHYTHM_DATA_DIR to an absolute path to choose a different directory. Do not point tests at a real user data directory.

Legacy databases imported from older versions may contain historical window-title values. The app provides a privacy action to clear those old values. Newly collected foreground sessions store an empty title.

## Optional outbound data

No project-hosted backend receives activity data.

- **AI replay and deep reads:** When a user configures a provider and API key and requests an AI replay or deep read, the app sends an allowlisted summary to that selected provider. It can include per-application display names and counts, foreground durations and segments, hourly distribution, personal comparisons, coverage facts, reviewed theory cards, and user-authored corrections or a custom Agent prompt. It does not send executable paths, raw key events, typed characters, mouse coordinates, screenshots, window titles, or browser page content. Provider terms and retention apply to data sent to that provider.
- **Email reports:** A user must configure SMTP or QQ Agent Mail, verify the recipient, and enable a subscription or request a report. A report contains daily keyboard/mouse counts, click and scroll counts when available, foreground duration, collector coverage, and observed time boundaries. It omits per-app names, executable paths, window titles, and raw event records. The chosen email transport and mailbox provider process the report.
- **Local AI API key:** The optional AI key is stored in the local settings file. Protect the operating-system account and app data directory. Email configuration uses Windows user-scoped DPAPI protection; QQ Agent Mail credentials use the external CLI's own account storage.

The source release includes the email and AI code paths, but source availability alone is not evidence of independent real-recipient acceptance for a particular configuration.

## User controls

Users can disable AI/cloud use, remove the configured API key, change or disable report subscriptions, export or back up local data, clear legacy title values, and fully exit the app from the tray menu. App feedback is stored locally and is not automatically uploaded.
