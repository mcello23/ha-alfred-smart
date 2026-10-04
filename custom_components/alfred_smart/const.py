"""Constants for the Alfred Smart integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "alfred_smart"
MANUFACTURER: Final = "Alfred Smart"

API_BASE: Final = "https://services.alfredsmartdata.com"
# The API only answers requests that look like the web app.
APP_ORIGIN: Final = "https://app.alfredsmart.com"

CONF_ASSET_ID: Final = "asset_id"
CONF_ASSET_NAME: Final = "asset_name"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = 5  # minutes
MIN_SCAN_INTERVAL: Final = 1
MAX_SCAN_INTERVAL: Final = 60

# One failed poll is a network hiccup, not an outage: keep the last good data
# for this long before the entities go unavailable. Measured on a real install,
# almost every drop lasted exactly one 5-minute poll.
UPDATE_GRACE_PERIOD: Final = timedelta(minutes=15)

# Gateways and common areas barely change; no need to read them every poll.
SLOW_REFRESH_INTERVAL: Final = timedelta(hours=1)

# `/devices/interact` only answers after the physical gateway acknowledges.
INTERACT_TIMEOUT: Final = 30
READ_TIMEOUT: Final = 25

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_COMMON_AREA_ID: Final = "common_area_id"
ATTR_SENSOR_UUID: Final = "sensor_uuid"
ATTR_START: Final = "start"
ATTR_END: Final = "end"

SERVICE_OPEN_COMMON_AREA: Final = "open_common_area"
SERVICE_BOOK_COMMON_AREA: Final = "book_common_area"
