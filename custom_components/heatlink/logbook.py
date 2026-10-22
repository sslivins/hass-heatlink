"""Describe HeatLink events in the Home Assistant activity log."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, EVENT_FAULT, EVENT_HEATLINK

ACTORS = {
    "web_ui": "from the web page",
    "home_assistant": "from Home Assistant",
    "group": "by the outdoor-unit group",
    "local": "from the remote or unit",
}


def heatlink_message(data: Mapping[str, Any]) -> str:
    message = str(data.get("message") or "reported an event")
    actor = ACTORS.get(str(data.get("actor")))
    user = data.get("user")
    if actor and user and data.get("actor") == "web_ui":
        actor = f"by {user} {actor}"
    return f"{message} ({actor})" if actor else message


def fault_message(data: Mapping[str, Any]) -> str:
    if not data.get("fault"):
        previous = data.get("previous_code")
        return f"fault {previous} cleared" if previous else "fault cleared"
    return f"reported fault {data.get('code') or 'unknown'}"


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    def _entry(data: Mapping[str, Any], platform: str, key: str) -> dict[str, str]:
        entry = {LOGBOOK_ENTRY_NAME: str(data.get("name") or "HeatLink")}
        uid = data.get("uid")
        if uid:
            entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{uid}_{key}")
            if entity_id is not None:
                entry[LOGBOOK_ENTRY_ENTITY_ID] = entity_id
        return entry

    @callback
    def describe_event(event: Event) -> dict[str, str]:
        entry = _entry(event.data, "climate", "climate")
        entry[LOGBOOK_ENTRY_MESSAGE] = heatlink_message(event.data)
        return entry

    @callback
    def describe_fault(event: Event) -> dict[str, str]:
        entry = _entry(event.data, "sensor", "fault_code")
        entry[LOGBOOK_ENTRY_MESSAGE] = fault_message(event.data)
        return entry

    async_describe_event(DOMAIN, EVENT_HEATLINK, describe_event)
    async_describe_event(DOMAIN, EVENT_FAULT, describe_fault)
