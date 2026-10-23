"""Setup, entities, commands and background behaviour."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_SWING_HORIZONTAL_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_SWING_HORIZONTAL_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    EVENT_STATE_CHANGED,
    STATE_OFF,
    STATE_ON,
    UnitOfTemperature,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pyheatlink import HeatLinkConflictError, HeatLinkConnectionError, HeatLinkScopeError
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.heatlink.const import (
    CONF_REMOTE_TEMP_ENTITY,
    DOMAIN,
    EVENT_FAULT,
    EVENT_HEATLINK,
)

from .conftest import FakeHead, Heads, setup_head

CLIMATE = "climate.bedroom_1"


def entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    eid = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert eid is not None, f"no {platform} entity {unique_id}"
    return eid


def capture(hass: HomeAssistant, event_type: str) -> list[Event[Any]]:
    events: list[Event[Any]] = []
    hass.bus.async_listen(event_type, events.append)
    return events


async def test_setup_and_unload(hass: HomeAssistant, heads: Heads, head: FakeHead) -> None:
    entry = await setup_head(hass, head)
    assert entry.state is ConfigEntryState.LOADED

    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, "6dac"), entry.entry_id)
    assert device is not None
    assert device.name == "Bedroom 1"
    assert device.sw_version == "1.5.2"
    assert device.via_device_id is not None
    outdoor = devices.async_get(device.via_device_id)
    assert outdoor is not None and outdoor.name == "Bedrooms"

    state = hass.states.get(CLIMATE)
    assert state is not None
    assert state.state == HVACMode.OFF
    assert state.attributes["current_temperature"] == 17.4
    assert state.attributes[ATTR_TEMPERATURE] == 16
    assert state.attributes["outdoor_unit"] == "bb66e0db"
    # Tandem is on, so the unit's own AUTO is hidden.
    assert HVACMode.HEAT_COOL not in state.attributes["hvac_modes"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_not_ready(hass: HomeAssistant, heads: Heads, head: FakeHead, client) -> None:
    client.get_info.side_effect = HeatLinkConnectionError("down")
    from .conftest import make_entry

    entry = make_entry(head)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_entities(hass: HomeAssistant, heads: Heads, head: FakeHead) -> None:
    await setup_head(hass, head)
    states = {
        "sensor": {
            "6dac_room_temperature": "17.4",
            "6dac_compressor_frequency": "0",
            "6dac_fault_code": "none",
            "6dac_park_state": "off",
            "6dac_reset_reason": "sw",
            "outdoor_bb66e0db_tandem_state": "heating",
        },
        "binary_sensor": {
            "6dac_parked": STATE_OFF,
            "6dac_fault": STATE_OFF,
            "6dac_unit_connected": STATE_ON,
            "outdoor_bb66e0db_mode_conflict": STATE_OFF,
        },
        "switch": {
            "outdoor_bb66e0db_park": STATE_ON,
            "outdoor_bb66e0db_probe": STATE_OFF,
            "outdoor_bb66e0db_tandem": STATE_ON,
        },
        "number": {
            "6dac_park_delta": "0.5",
            "outdoor_bb66e0db_park_min_off": "5.0",
            "outdoor_bb66e0db_tandem_max_turn": "30.0",
        },
        "update": {"6dac_firmware": STATE_OFF},
    }
    for platform, expected in states.items():
        for unique_id, value in expected.items():
            state = hass.states.get(entity_id(hass, platform, unique_id))
            assert state is not None
            assert state.state == value, unique_id

    # Shared park fields live on the outdoor unit, not on each head.
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("switch", DOMAIN, "6dac_park") is None
    assert registry.async_get_entity_id("number", DOMAIN, "6dac_park_min_off") is None


async def test_read_only_token(
    hass: HomeAssistant, heads: Heads, head: FakeHead, client: MagicMock
) -> None:
    client.get_update.side_effect = HeatLinkScopeError("read")
    entry = await setup_head(hass, head)
    assert entry.runtime_data.admin is False
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("update", DOMAIN, "6dac_firmware") is None
    assert registry.async_get_entity_id("switch", DOMAIN, "outdoor_bb66e0db_park") is None
    assert registry.async_get_entity_id("number", DOMAIN, "6dac_park_delta") is None
    assert hass.states.get(CLIMATE) is not None


async def test_climate_commands(hass: HomeAssistant, heads: Heads, head: FakeHead, client) -> None:
    await setup_head(hass, head)
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.HEAT},
        blocking=True,
    )
    client.set_climate.assert_awaited_with(mode="HEAT")
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 21.5},
        blocking=True,
    )
    client.set_climate.assert_awaited_with(temperature=21.5)
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    client.set_climate.assert_awaited_with(power=False)

    head.settings["caps"]["wideVane"]["show"] = True
    head.settings["wideVane"] = "<<"
    await hass.config_entries.async_reload(hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(CLIMATE)
    assert state is not None
    assert state.attributes["swing_horizontal_mode"] == "far_left"
    assert "split" in state.attributes["swing_horizontal_modes"]
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_SWING_HORIZONTAL_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_SWING_HORIZONTAL_MODE: "split"},
        blocking=True,
    )
    client.set_climate.assert_awaited_with(wide_vane="<>")
    client.set_climate.side_effect = HeatLinkConflictError("Tandem is on", message="Tandem is on")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.HEAT_COOL},
            blocking=True,
        )


async def test_hvac_action_held(hass: HomeAssistant, heads: Heads, head: FakeHead) -> None:
    head.settings.update(power="ON", mode="HEAT")
    head.settings["park"].update(state="held", held=True, unit_on=False, tandem_eta_s=600)
    await setup_head(hass, head)
    state = hass.states.get(CLIMATE)
    assert state is not None
    assert state.state == HVACMode.HEAT
    assert state.attributes["hvac_action"] == HVACAction.IDLE
    assert state.attributes["tandem_at"] is not None


async def test_outdoor_writes(hass: HomeAssistant, heads: Heads, head: FakeHead, client) -> None:
    await setup_head(hass, head)
    await hass.services.async_call(
        "switch",
        "turn_on",
        {ATTR_ENTITY_ID: entity_id(hass, "switch", "outdoor_bb66e0db_probe")},
        blocking=True,
    )
    client.set_park.assert_awaited_with(probe_enabled=True)
    await hass.services.async_call(
        "number",
        "set_value",
        {
            ATTR_ENTITY_ID: entity_id(hass, "number", "outdoor_bb66e0db_tandem_max_turn"),
            "value": 45,
        },
        blocking=True,
    )
    client.set_tandem.assert_awaited_with(max_slice_s=2700)
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: entity_id(hass, "number", "6dac_park_delta"), "value": 1.2},
        blocking=True,
    )
    client.set_park.assert_awaited_with(delta_c=1.2)


async def test_park_delta_in_fahrenheit(
    hass: HomeAssistant, heads: Heads, head: FakeHead, client
) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_head(hass, head)
    eid = entity_id(hass, "number", "6dac_park_delta")
    state = hass.states.get(eid)
    assert state is not None
    assert state.state == "0.9"
    assert state.attributes["unit_of_measurement"] == UnitOfTemperature.FAHRENHEIT
    assert (state.attributes["min"], state.attributes["max"]) == (0.9, 5.4)
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: eid, "value": 1.8}, blocking=True
    )
    client.set_park.assert_awaited_with(delta_c=1.0)
    climate = hass.states.get(CLIMATE)
    assert climate is not None
    assert climate.attributes["target_temp_step"] == 1.0


async def test_buttons(hass: HomeAssistant, heads: Heads, head: FakeHead, client) -> None:
    await setup_head(hass, head)
    registry = er.async_get(hass)
    eid = entity_id(hass, "button", "6dac_reset_wifi_drops")
    registry.async_update_entity(eid, disabled_by=None)
    eid2 = entity_id(hass, "button", "6dac_restart")
    registry.async_update_entity(eid2, disabled_by=None)
    await hass.config_entries.async_reload(head_entry(hass).entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: eid}, blocking=True)
    client.reset_diag.assert_awaited_with("wifi-drops")
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: eid2}, blocking=True)
    client.restart.assert_awaited_once()


def head_entry(hass: HomeAssistant) -> Any:
    return hass.config_entries.async_entries(DOMAIN)[0]


async def test_update_install(
    hass: HomeAssistant, heads: Heads, head: FakeHead, client: MagicMock
) -> None:
    head.update.update(latest="1.5.3", update_available=True)
    await setup_head(hass, head)
    eid = entity_id(hass, "update", "6dac_firmware")
    state = hass.states.get(eid)
    assert state is not None and state.state == STATE_ON
    assert state.attributes["latest_version"] == "1.5.3"

    head.ota.update(state="success", progress=100)

    async def install() -> None:
        head.status["version"] = "1.5.3"

    client.install_update.side_effect = install
    await hass.services.async_call("update", "install", {ATTR_ENTITY_ID: eid}, blocking=True)
    client.install_update.assert_awaited_once()
    assert not ir.async_get(hass).async_get_issue(DOMAIN, "update_failed_6dac")


async def test_update_install_failed(
    hass: HomeAssistant, heads: Heads, head: FakeHead, client: MagicMock
) -> None:
    head.update.update(latest="1.5.3", update_available=True)
    await setup_head(hass, head)
    head.ota.update(state="failed", error="bad image")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "update",
            "install",
            {ATTR_ENTITY_ID: entity_id(hass, "update", "6dac_firmware")},
            blocking=True,
        )


async def test_activity_events(
    hass: HomeAssistant, heads: Heads, head: FakeHead, freezer: FrozenDateTimeFactory
) -> None:
    events = capture(hass, EVENT_HEATLINK)
    await setup_head(hass, head)
    # The first poll only records where the log is.
    assert events == []
    head.events["events"].insert(
        0,
        {
            "seq": 642,
            "ts": 1792695300,
            "up": 5,
            "cat": 0,
            "act": 1,
            "msg": "Mode set to HEAT",
            "name": "admin",
            "ip": "192.168.1.20",
        },
    )
    head.events["seq"] = 642
    freezer.tick(31)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["category"] == "mode"
    assert events[0].data["actor"] == "web_ui"
    assert events[0].data["user"] == "admin"


async def test_fault_event(
    hass: HomeAssistant, heads: Heads, head: FakeHead, freezer: FrozenDateTimeFactory
) -> None:
    faults = capture(hass, EVENT_FAULT)
    await setup_head(hass, head)
    head.settings.update(fault=True, errorCode="E6")
    freezer.tick(11)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(faults) == 1
    assert faults[0].data["code"] == "E6"
    state = hass.states.get(entity_id(hass, "sensor", "6dac_fault_code"))
    assert state is not None and state.state == "E6"


async def test_unavailable_after_failures(
    hass: HomeAssistant,
    heads: Heads,
    head: FakeHead,
    client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_head(hass, head)
    client.get_settings.side_effect = HeatLinkConnectionError("down")
    for _ in range(2):
        freezer.tick(11)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state == HVACMode.OFF
    freezer.tick(11)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state == "unavailable"


async def test_steady_state_writes_nothing(
    hass: HomeAssistant, heads: Heads, head: FakeHead, freezer: FrozenDateTimeFactory
) -> None:
    """Unchanged head data must not create state changes (recorder/logbook noise)."""
    await setup_head(hass, head)
    changes: list[str] = []

    def _changed(event: Event[Any]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None or (old.state, old.attributes) != (new.state, new.attributes):
            changes.append(event.data["entity_id"])

    hass.bus.async_listen(EVENT_STATE_CHANGED, _changed)
    for _ in range(12):
        freezer.tick(11)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert changes == []


async def test_mqtt_discovery_issue(hass: HomeAssistant, heads: Heads, head: FakeHead) -> None:
    head.info["mqtt"]["discovery"] = True
    await setup_head(hass, head)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "mqtt_discovery_6dac")


async def test_mqtt_issue_clears_after_reboot(
    hass: HomeAssistant, heads: Heads, head: FakeHead
) -> None:
    head.info["mqtt"]["discovery"] = True
    entry = await setup_head(hass, head)
    head.info["mqtt"]["discovery"] = False
    head.status["uptime_s"] = 30
    await entry.runtime_data.status.async_refresh()
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "mqtt_discovery_6dac") is None


async def test_remote_temperature_binding(
    hass: HomeAssistant, heads: Heads, head: FakeHead, client: MagicMock
) -> None:
    hass.states.async_set("sensor.lounge", "21.3", {"unit_of_measurement": "°C"})
    entry = await setup_head(hass, head, **{CONF_REMOTE_TEMP_ENTITY: "sensor.lounge"})
    client.set_remote_temp.assert_awaited_with(21.3, ttl_s=600)
    hass.states.async_set("sensor.lounge", "70.0", {"unit_of_measurement": "°F"})
    await hass.async_block_till_done()
    client.set_remote_temp.assert_awaited_with(21.1, ttl_s=600)

    client.set_remote_temp.side_effect = HeatLinkConflictError("BLE sensor", message="owned")
    hass.states.async_set("sensor.lounge", "22", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "remote_temp_owned_6dac")

    client.set_remote_temp.side_effect = None
    hass.states.async_set("sensor.lounge", "unavailable")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_set_remote_temperature_service(
    hass: HomeAssistant, heads: Heads, head: FakeHead, client: MagicMock
) -> None:
    await setup_head(hass, head)
    await hass.services.async_call(
        DOMAIN,
        "set_remote_temperature",
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 19.5, "ttl": 300},
        blocking=True,
    )
    client.set_remote_temp.assert_awaited_with(19.5, ttl_s=300)
    await hass.services.async_call(
        DOMAIN, "set_remote_temperature", {ATTR_ENTITY_ID: CLIMATE}, blocking=True
    )
    client.clear_remote_temp.assert_awaited_once()


async def test_two_heads_share_outdoor_unit(hass: HomeAssistant, heads: Heads, head) -> None:
    await setup_head(hass, head)
    peer = heads.add(FakeHead(uid="e6ec", name="Bedroom 2", host="192.168.1.143"))
    entry2 = await setup_head(hass, peer)
    assert entry2.runtime_data.owns_outdoor_unit is False
    registry = er.async_get(hass)
    switch = registry.async_get(entity_id(hass, "switch", "outdoor_bb66e0db_tandem"))
    assert switch is not None
    first = head_entry(hass)
    assert switch.config_entry_id == first.entry_id

    # One outdoor device, shared by both heads and the outdoor entities.
    devices = dr.async_get(hass)
    outdoor = devices.async_get_devices(identifiers={(DOMAIN, "outdoor_bb66e0db")})
    assert len(outdoor) == 1
    assert switch.device_id == outdoor[0].id
    for entry in (first, entry2):
        assert devices.async_get(entry.runtime_data.device_id).via_device_id == outdoor[0].id

    # Unloading the owner hands the outdoor unit to the other head.
    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()
    assert entry2.runtime_data.owns_outdoor_unit is True
    switch = er.async_get(hass).async_get(switch.entity_id)
    assert switch.config_entry_id == entry2.entry_id
    assert switch.device_id == outdoor[0].id
    assert hass.states.get(switch.entity_id).state == STATE_ON


async def test_diagnostics(hass: HomeAssistant, heads: Heads, head: FakeHead) -> None:
    from custom_components.heatlink.diagnostics import async_get_config_entry_diagnostics

    entry = await setup_head(hass, head)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = repr(diag)
    assert "tok" not in text.replace("token", "")
    assert "192.168.1.151" not in text
    assert diag["status"]["version"] == "1.5.2"
