"""Sensors for HeatLink heads and outdoor units."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pyheatlink import Group, Settings, Status

from . import HeatLinkConfigEntry
from .climate import stable_at
from .entity import HeatLinkEntity, HeatLinkOutdoorEntity, HeatLinkUnitEntity
from .runtime import HeatLinkRuntime

PARALLEL_UPDATES = 0

PARK_STATES = ["disabled", "off", "running", "parked", "probing", "held", "checking"]
TANDEM_STATES = [
    "off",
    "starting",
    "idle",
    "heating",
    "cooling",
    "switching_to_heat",
    "switching_to_cool",
]


def park_state(s: Settings) -> str | None:
    park = s.park
    if park is None:
        return None
    if not park.enabled and not park.slicing:
        return "disabled"
    if park.held:
        return "checking" if park.checking else "held"
    return park.state if park.state in PARK_STATES else None


def _minutes(seconds: int | None) -> int | None:
    return None if seconds is None else max(0, math.ceil(seconds / 60))


def fault_code(s: Settings) -> str:
    return (s.error_code or "unknown") if s.fault else "none"


@dataclass(frozen=True, kw_only=True)
class SettingsSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Settings], Any]


@dataclass(frozen=True, kw_only=True)
class StatusSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Status], Any]


SETTINGS_SENSORS: tuple[SettingsSensorDescription, ...] = (
    SettingsSensorDescription(
        key="room_temperature",
        translation_key="room_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda s: (
            round(s.room_sensor.temp, 1)
            if s.room_sensor is not None
            and s.room_sensor.state == "ok"
            and s.room_sensor.temp is not None
            else s.room_temperature
        ),
    ),
    SettingsSensorDescription(
        key="unit_temperature",
        translation_key="unit_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.room_temperature,
    ),
    SettingsSensorDescription(
        key="compressor_frequency",
        translation_key="compressor_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        value_fn=lambda s: s.compressor_frequency,
    ),
    SettingsSensorDescription(
        key="fault_code",
        translation_key="fault_code",
        value_fn=fault_code,
    ),
    SettingsSensorDescription(
        key="park_state",
        translation_key="park_state",
        device_class=SensorDeviceClass.ENUM,
        options=PARK_STATES,
        value_fn=park_state,
    ),
    SettingsSensorDescription(
        key="tandem_turn_in",
        translation_key="tandem_turn_in",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda s: (
            _minutes(s.park.tandem_eta_s) if s.park is not None and s.park.held else None
        ),
    ),
)


STATUS_SENSORS: tuple[StatusSensorDescription, ...] = (
    StatusSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.rssi,
    ),
    StatusSensorDescription(
        key="reset_reason",
        translation_key="reset_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.diag.reset_reason if s.diag is not None else None,
    ),
    StatusSensorDescription(
        key="brownouts",
        translation_key="brownouts",
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.diag.brownout_count if s.diag is not None else None,
    ),
    StatusSensorDescription(
        key="wifi_drops",
        translation_key="wifi_drops",
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.diag.wifi_drop_count if s.diag is not None else None,
    ),
    StatusSensorDescription(
        key="boot_count",
        translation_key="boot_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.diag.boot_count if s.diag is not None else None,
    ),
    StatusSensorDescription(
        key="supply_voltage_min",
        translation_key="supply_voltage_min",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: (
            s.diag.vin_min_mv / 1000 if s.diag is not None and s.diag.vin_min_mv else None
        ),
    ),
    StatusSensorDescription(
        key="free_memory",
        translation_key="free_memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.KILOBYTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.free_heap,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        HeatLinkSettingsSensor(runtime, d)
        for d in SETTINGS_SENSORS
        if runtime.park is not None or d.key not in ("park_state", "tandem_turn_in")
    ]
    entities += [HeatLinkStatusSensor(runtime, d) for d in STATUS_SENSORS]
    entities.append(HeatLinkLastBootSensor(runtime))
    if runtime.owns_outdoor_unit and runtime.tandem_supported:
        entities += [TandemStateSensor(runtime), TandemTurnEndsSensor(runtime)]
    async_add_entities(entities)


class HeatLinkSettingsSensor(HeatLinkUnitEntity[Settings], SensorEntity):
    entity_description: SettingsSensorDescription

    def __init__(self, runtime: HeatLinkRuntime, description: SettingsSensorDescription) -> None:
        super().__init__(runtime, runtime.settings, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.data) if self.data is not None else None


class HeatLinkStatusSensor(HeatLinkEntity[Status], SensorEntity):
    """Head health; follows HTTP reachability only."""

    entity_description: StatusSensorDescription

    def __init__(self, runtime: HeatLinkRuntime, description: StatusSensorDescription) -> None:
        super().__init__(runtime, runtime.status, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.data) if self.data is not None else None


class HeatLinkLastBootSensor(HeatLinkEntity[Status], SensorEntity):
    _attr_translation_key = "last_boot"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: HeatLinkRuntime) -> None:
        super().__init__(runtime, runtime.status, "last_boot")
        self._boot: datetime | None = None

    @property
    def native_value(self) -> datetime | None:
        if self.data is None or not self.data.uptime_s:
            return self._boot
        boot = (dt_util.utcnow() - timedelta(seconds=self.data.uptime_s)).replace(microsecond=0)
        # Polling jitter moves the computed time a little; only a real reboot
        # should change the state.
        if self._boot is None or abs((boot - self._boot).total_seconds()) > 120:
            self._boot = boot
        return self._boot


def tandem_state(group: Group | None) -> str | None:
    tandem = group.tandem if group is not None else None
    if tandem is None:
        return None
    if not tandem.enabled:
        return "off"
    if not tandem.active:
        return "starting"
    if tandem.phase == "changeover":
        return "switching_to_heat" if tandem.mode == "heat" else "switching_to_cool"
    if tandem.phase == "run":
        return "heating" if tandem.mode == "heat" else "cooling"
    return "idle"


class TandemStateSensor(HeatLinkOutdoorEntity, SensorEntity):
    """What the outdoor unit's Tandem turn-taking is doing right now."""

    _attr_translation_key = "tandem_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = TANDEM_STATES
    _unrecorded_attributes = frozenset({"turn_ends_at"})

    def __init__(self, runtime: HeatLinkRuntime) -> None:
        super().__init__(runtime, "tandem_state")
        self._turn_ends: datetime | None = None

    @property
    def native_value(self) -> str | None:
        return tandem_state(self.group_data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        group = self.group_data
        tandem = group.tandem if group is not None else None
        if tandem is None or group is None:
            return {}
        names = {m.uid: m.name for m in group.members}
        if group.self_member is not None:
            names[group.self_member.uid] = group.self_member.name
        waiting = [m for m in tandem.members if m.directive == "hold"]
        remaining = None
        if tandem.phase == "run" and tandem.elapsed_s is not None:
            decide = tandem.decide_s or tandem.max_slice_s or 0
            remaining = max(0, decide - tandem.elapsed_s)
        self._turn_ends = stable_at(self._turn_ends, remaining)
        return {
            "coordinator": names.get(tandem.coordinator, tandem.coordinator),
            "running": [names.get(m.uid, m.uid) for m in tandem.members if m.directive == "run"],
            "waiting": [names.get(m.uid, m.uid) for m in waiting],
            "next": (
                ("cool" if tandem.mode == "heat" else "heat")
                if waiting and tandem.phase == "run"
                else None
            ),
            "turn_ends_at": self._turn_ends.isoformat() if self._turn_ends else None,
            "label": group.label,
        }


class TandemTurnEndsSensor(HeatLinkOutdoorEntity, SensorEntity):
    """Latest the running Tandem turn can end, while another room waits for it."""

    _attr_translation_key = "tandem_turn_ends"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, runtime: HeatLinkRuntime) -> None:
        super().__init__(runtime, "tandem_turn_ends")

    @property
    def native_value(self) -> int | None:
        group = self.group_data
        tandem = group.tandem if group is not None else None
        if tandem is None or tandem.phase != "run" or tandem.elapsed_s is None:
            return None
        if not any(m.directive == "hold" for m in tandem.members):
            return None
        decide = tandem.decide_s or tandem.max_slice_s or 0
        return _minutes(decide - tandem.elapsed_s)
