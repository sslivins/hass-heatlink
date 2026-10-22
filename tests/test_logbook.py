"""Activity-log descriptions."""

from __future__ import annotations

from typing import Any

from homeassistant.core import Event, HomeAssistant

from custom_components.heatlink.const import EVENT_FAULT, EVENT_HEATLINK
from custom_components.heatlink.logbook import async_describe_events

from .conftest import FakeHead, Heads, setup_head


async def test_describe(hass: HomeAssistant, heads: Heads, head: FakeHead) -> None:
    await setup_head(hass, head)
    describers: dict[str, Any] = {}
    async_describe_events(hass, lambda _d, kind, fn: describers.__setitem__(kind, fn))

    entry = describers[EVENT_HEATLINK](
        Event(
            EVENT_HEATLINK,
            {
                "uid": "6dac",
                "name": "Bedroom 1",
                "message": "Mode set to HEAT",
                "actor": "web_ui",
                "user": "admin",
            },
        )
    )
    assert entry["entity_id"] == "climate.bedroom_1"
    assert entry["message"] == "Mode set to HEAT (by admin from the web page)"
    assert (
        describers[EVENT_HEATLINK](Event(EVENT_HEATLINK, {"message": "Booted", "actor": "system"}))[
            "message"
        ]
        == "Booted"
    )

    fault = describers[EVENT_FAULT]
    entry = fault(Event(EVENT_FAULT, {"uid": "6dac", "fault": True, "code": "E6"}))
    assert entry["message"] == "reported fault E6"
    assert entry["entity_id"].startswith("sensor.")
    cleared = fault(Event(EVENT_FAULT, {"uid": "6dac", "fault": False, "previous_code": "E6"}))
    assert cleared["message"] == "fault E6 cleared"
