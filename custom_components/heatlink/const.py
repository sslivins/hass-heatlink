"""Constants for the HeatLink integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "heatlink"
MANUFACTURER: Final = "HeatLink"

CONF_TOKEN: Final = "token"
CONF_UID: Final = "uid"
CONF_GHASH: Final = "ghash"
CONF_FAST_POLL: Final = "fast_poll"
CONF_REMOTE_TEMP_ENTITY: Final = "remote_temp_entity"

#: Lowest /api/info api_version this integration understands.
MIN_API_VERSION: Final = 1

SETTINGS_INTERVAL: Final = timedelta(seconds=10)
SETTINGS_INTERVAL_FAST: Final = timedelta(seconds=5)
GROUP_INTERVAL: Final = timedelta(seconds=15)
STATUS_INTERVAL: Final = timedelta(seconds=90)
CONFIG_INTERVAL: Final = timedelta(minutes=10)
UPDATE_INTERVAL: Final = timedelta(hours=3)
EVENTS_INTERVAL: Final = timedelta(seconds=30)
OTA_POLL_INTERVAL: Final = timedelta(seconds=3)

#: Consecutive failed polls tolerated before entities go unavailable.
FAILURE_THRESHOLD: Final = 3

#: Off-at-setpoint fields the firmware replicates to every head of a group.
SHARED_PARK_FIELDS: Final = frozenset(
    {"enabled", "min_off_s", "probe_enabled", "probe_interval_s", "probe_duration_s"}
)

REMOTE_TEMP_TTL: Final = 600
REMOTE_TEMP_RENEW: Final = timedelta(minutes=4)

EVENT_HEATLINK: Final = "heatlink_event"
EVENT_FAULT: Final = "heatlink_fault"

SERVICE_SET_REMOTE_TEMPERATURE: Final = "set_remote_temperature"
ATTR_TTL: Final = "ttl"

PLATFORMS: Final = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

CARD_URL_BASE: Final = "/heatlink_static"
CARD_FILENAME: Final = "heatlink-cards.js"
CARD_VERSION: Final = "0.1.0"

EVENT_CATEGORY_NAMES: Final = {
    0: "mode",
    1: "power",
    2: "setpoint",
    3: "fan",
    4: "group",
    5: "membership",
    6: "ota",
    7: "network",
    8: "unit",
    9: "fault",
    10: "system",
}
EVENT_ACTOR_NAMES: Final = {
    0: "system",
    1: "web_ui",
    2: "home_assistant",
    3: "group",
    4: "local",
}
