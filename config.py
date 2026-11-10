"""扣舷-数字节律全局配置。"""
import os

APP_NAME = "扣舷-数字节律"
APP_ICON_TEXT = "律"
APP_VERSION = "0.12.1"
APP_VERSION_EPOCH = 2
DATA_SCHEMA_VERSION = 2
APP_MUTEX_NAME = os.getenv("INTERACTION_RHYTHM_MUTEX_NAME", r"Local\Kouxian.DigitalRhythm.SingleInstance")

PORT = int(os.getenv("INTERACTION_RHYTHM_PORT", "19023"))
POLL_INTERVAL = 0.5
BATCH_INTERVAL = 1
DB_RETENTION_DAYS = int(os.getenv("INTERACTION_RHYTHM_RETENTION_DAYS", "3650"))
HEATMAP_DAYS = 6
HOURLY_RETENTION_DAYS = int(os.getenv("INTERACTION_RHYTHM_HOURLY_RETENTION_DAYS", "180"))
COLLECTOR_STATES_RETENTION_DAYS = int(
    os.getenv("INTERACTION_RHYTHM_COLLECTOR_STATES_RETENTION_DAYS", "90")
)

# Frameless mode uses the custom titlebar drag bridge. Flip this to False
# if a Windows/WebView2 update ever regresses dragging behavior.
BORDERLESS_WINDOW = True
BORDERLESS_DRAG_SELECTOR = ".window-drag"

# Source builds have no public binary update feed. Updates stay disabled unless
# a distributor supplies an authenticated, compatible manifest explicitly.
UPDATE_MANIFEST_URL = os.getenv("INTERACTION_RHYTHM_UPDATE_URL", "").strip()
UPDATE_CHANNEL = os.getenv("INTERACTION_RHYTHM_UPDATE_CHANNEL", "stable")
UPDATE_TIMEOUT = 20

# GitHub Releases are private. A packaged client therefore must not attempt an
# anonymous manifest request that it cannot authenticate. Internal deployments
# may opt in only when they also provide an authenticated distribution path.
UPDATE_CHECK_ENABLED = os.getenv("INTERACTION_RHYTHM_UPDATE_CHECK_ENABLED", "").strip().lower() in {
    "1", "true", "yes", "on"
}
