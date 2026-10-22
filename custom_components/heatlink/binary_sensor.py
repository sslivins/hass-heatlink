"""Binary sensors for HeatLink heads and outdoor units."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyheatlink import Settings

from . import HeatLinkConfigEntry
from .entity import HeatLinkEntity, HeatLinkOutdoorEntity, HeatLinkUnitEntity
from .runtime import HeatLinkRuntime

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SettingsBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Settings], bool | None]
    needs_park: bool = False


UNIT_BINARY_SENSORS: tuple[SettingsBinaryDescription, ...] = (
    SettingsBinaryDescription(
        key="parked",
        translation_key="parked",
        needs_park=True,
        value_fn=lambda s: s.park.parked if s.park is not None else None,
    ),
    SettingsBinaryDescription(
        key="held",
        translation_key="held",
        needs_park=True,
        value_fn=lambda s: s.park.held if s.park is not None else None,
    ),
    SettingsBinaryDescription(
        key="defrost",
        translation_key="defrost",
        value_fn=lambda s: (s.sub_mode or "").upper() == "DEFROST",
    ),
    SettingsBinaryDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: s.fault,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        HeatLinkUnitBinarySensor(runtime, d)
        for d in UNIT_BINARY_SENSORS
        if runtime.park is not None or not d.needs_park
    ]
    entities.append(HeatLinkUnitConnected(runtime))
    if runtime.owns_outdoor_unit:
        entities.append(OutdoorConflict(runtime))
    async_add_entities(entities)


class HeatLinkUnitBinarySensor(HeatLinkUnitEntity[Settings], BinarySensorEntity):
    entity_description: SettingsBinaryDescription

    def __init__(self, runtime: HeatLinkRuntime, description: SettingsBinaryDescription) -> None:
        super().__init__(runtime, runtime.settings, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.data) if self.data is not None else None


class HeatLinkUnitConnected(HeatLinkEntity[Settings], BinarySensorEntity):
    """Whether the head can talk to its indoor unit over CN105."""

    _attr_translation_key = "unit_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: HeatLinkRuntime) -> None:
        super().__init__(runtime, runtime.settings, "unit_connected")

    @property
    def is_on(self) -> bool | None:
        return self.data.connected if self.data is not None else None


class OutdoorConflict(HeatLinkOutdoorEntity, BinarySensorEntity):
    """Heads on this outdoor unit are asking for opposite modes."""

    _attr_translation_key = "mode_conflict"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, runtime: HeatLinkRuntime) -> None:
        super().__init__(runtime, "mode_conflict")

    @property
    def is_on(self) -> bool | None:
        group = self.group_data
        if group is None:
            return None
        return group.status == "conflict" or bool(group.conflicts)
