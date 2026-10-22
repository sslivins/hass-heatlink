"""Switches: off-at-setpoint and Tandem (admin tokens only)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyheatlink import ParkConfig

from . import HeatLinkConfigEntry
from .entity import HeatLinkEntity, HeatLinkOutdoorEntity, async_call
from .runtime import HeatLinkRuntime

PARALLEL_UPDATES = 1

#: (key, ParkConfig/set_park field)
PARK_SWITCHES = (("park", "enabled"), ("probe", "probe_enabled"))


def outdoor_admin(runtime: HeatLinkRuntime) -> bool:
    """Whether any loaded head on this outdoor unit can change shared settings."""
    if runtime.ghash is None:
        return False
    return any(rt.admin for rt in runtime.outdoor.members(runtime.ghash))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[SwitchEntity] = []
    if runtime.park is not None:
        shared = runtime.park_shared
        for key, field in PARK_SWITCHES:
            if field not in shared:
                if runtime.admin:
                    entities.append(HeadParkSwitch(runtime, key, field))
            elif runtime.owns_outdoor_unit and outdoor_admin(runtime):
                entities.append(OutdoorParkSwitch(runtime, key, field))
    if runtime.owns_outdoor_unit and runtime.tandem_supported and outdoor_admin(runtime):
        entities.append(TandemSwitch(runtime))
    async_add_entities(entities)


class HeadParkSwitch(HeatLinkEntity[ParkConfig], SwitchEntity):
    """Off-at-setpoint setting of a head that isn't sharing an outdoor unit."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime: HeatLinkRuntime, key: str, field: str) -> None:
        assert runtime.park is not None
        super().__init__(runtime, runtime.park, key)
        self._attr_translation_key = key
        self._field = field

    @property
    def is_on(self) -> bool | None:
        return bool(getattr(self.data, self._field)) if self.data is not None else None

    async def _async_set(self, value: bool) -> None:
        await async_call(self.runtime.client.set_park(**{self._field: value}))
        await self.runtime.async_command_done(park=True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class OutdoorParkSwitch(HeatLinkOutdoorEntity, SwitchEntity):
    """Off-at-setpoint setting shared by every head on the outdoor unit."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime: HeatLinkRuntime, key: str, field: str) -> None:
        super().__init__(runtime, key)
        self._attr_translation_key = key
        self._field = field

    @property
    def available(self) -> bool:
        return self.park is not None

    @property
    def is_on(self) -> bool | None:
        park = self.park
        return bool(getattr(park, self._field)) if park is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_write_shared(self.writer.client.set_park(**{self._field: True}))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_write_shared(self.writer.client.set_park(**{self._field: False}))


class TandemSwitch(HeatLinkOutdoorEntity, SwitchEntity):
    """Heat/cool turn-taking for the outdoor unit."""

    _attr_translation_key = "tandem"

    def __init__(self, runtime: HeatLinkRuntime) -> None:
        super().__init__(runtime, "tandem")

    @property
    def available(self) -> bool:
        group = self.group_data
        return group is not None and group.tandem is not None

    @property
    def is_on(self) -> bool | None:
        group = self.group_data
        return group.tandem.enabled if group is not None and group.tandem else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_write_shared(self.writer.client.set_tandem(enabled=True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_write_shared(self.writer.client.set_tandem(enabled=False))
