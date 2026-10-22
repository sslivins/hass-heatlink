"""Diagnostics download for a HeatLink head."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import HeatLinkConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {
    CONF_TOKEN,
    CONF_HOST,
    "ip",
    "ssid",
    "mac",
    "addr",
    "id",
    "group_id",
    "hostname",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HeatLinkConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data

    def raw(coordinator: Any) -> Any:
        data = coordinator.data if coordinator is not None else None
        return getattr(data, "raw", None)

    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "admin": runtime.admin,
            "owns_outdoor_unit": runtime.owns_outdoor_unit,
            "info": runtime.info.raw,
            "settings": raw(runtime.settings),
            "status": raw(runtime.status),
            "park": raw(runtime.park),
            "group": raw(runtime.group),
            "update": raw(runtime.update),
            "remote_temp": (
                runtime.remote_temp.describe() if runtime.remote_temp is not None else None
            ),
        },
        TO_REDACT,
    )
