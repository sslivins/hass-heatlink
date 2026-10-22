"""Number entities: off-at-setpoint and Tandem tuning (admin tokens only)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyheatlink import ParkConfig, Tandem

from . import HeatLinkConfigEntry
from .entity import HeatLinkEntity, HeatLinkOutdoorEntity, async_call
from .runtime import HeatLinkRuntime
from .switch import outdoor_admin

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class HeatLinkNumberDescription(NumberEntityDescription):
    """``field`` is the API field; minute entities scale it from seconds."""

    field: str
    scale: int = 1
    entity_category: EntityCategory | None = EntityCategory.CONFIG
    mode: NumberMode = NumberMode.BOX


def _minutes(key: str, field: str, lo: float, hi: float) -> HeatLinkNumberDescription:
    return HeatLinkNumberDescription(
        key=key,
        translation_key=key,
        field=field,
        scale=60,
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=lo,
        native_max_value=hi,
        native_step=1,
    )


# Bounds match the firmware's park/slice sanitize().
PARK_NUMBERS: tuple[HeatLinkNumberDescription, ...] = (
    HeatLinkNumberDescription(
        key="park_delta",
        translation_key="park_delta",
        field="delta_c",
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0.5,
        native_max_value=3.0,
        native_step=0.1,
    ),
    _minutes("park_min_off", "min_off_s", 5, 60),
    _minutes("probe_interval", "probe_interval_s", 5, 120),
    _minutes("probe_duration", "probe_duration_s", 1, 10),
)

TANDEM_NUMBERS: tuple[HeatLinkNumberDescription, ...] = (
    _minutes("tandem_min_turn", "min_slice_s", 5, 60),
    _minutes("tandem_max_turn", "max_slice_s", 5, 120),
    _minutes("tandem_settle", "settle_s", 3, 10),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[NumberEntity] = []
    shared_writer = runtime.owns_outdoor_unit and outdoor_admin(runtime)
    if runtime.park is not None:
        shared = runtime.park_shared
        for d in PARK_NUMBERS:
            if d.field not in shared:
                if runtime.admin:
                    entities.append(HeadParkNumber(runtime, d))
            elif shared_writer:
                entities.append(OutdoorNumber(runtime, d, lambda e: e.park, "set_park"))
    if shared_writer and runtime.tandem_supported:
        entities += [OutdoorNumber(runtime, d, _tandem_of, "set_tandem") for d in TANDEM_NUMBERS]
    async_add_entities(entities)


def _tandem_of(entity: HeatLinkOutdoorEntity) -> Tandem | None:
    group = entity.group_data
    return group.tandem if group is not None else None


class HeatLinkNumber(NumberEntity):
    """Shows temperature differences in the system unit.

    Home Assistant converts only absolute temperatures to °F by itself.
    """

    entity_description: HeatLinkNumberDescription

    @property
    def _factor(self) -> float:
        if (
            self.entity_description.device_class == NumberDeviceClass.TEMPERATURE_DELTA
            and self.hass is not None
            and self.hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT
        ):
            return 1.8
        return 1.0

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self._factor != 1.0:
            return UnitOfTemperature.FAHRENHEIT
        return self.entity_description.native_unit_of_measurement

    @property
    def native_min_value(self) -> float:
        return round((self.entity_description.native_min_value or 0) * self._factor, 1)

    @property
    def native_max_value(self) -> float:
        return round((self.entity_description.native_max_value or 100) * self._factor, 1)

    def _read(self, obj: Any) -> float | None:
        d = self.entity_description
        raw = getattr(obj, d.field, None) if obj is not None else None
        return None if raw is None else round(raw / d.scale * self._factor, 1)

    def _api_value(self, value: float) -> float | int:
        d = self.entity_description
        return round(value * d.scale) if d.scale != 1 else round(value / self._factor, 1)


class HeadParkNumber(HeatLinkEntity[ParkConfig], HeatLinkNumber):
    entity_description: HeatLinkNumberDescription

    def __init__(self, runtime: HeatLinkRuntime, description: HeatLinkNumberDescription) -> None:
        assert runtime.park is not None
        super().__init__(runtime, runtime.park, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self._read(self.data)

    async def async_set_native_value(self, value: float) -> None:
        d = self.entity_description
        change: dict[str, Any] = {d.field: self._api_value(value)}
        await async_call(self.runtime.client.set_park(**change))
        await self.runtime.async_command_done(park=True)


class OutdoorNumber(HeatLinkOutdoorEntity, HeatLinkNumber):
    """A setting shared by every head on the outdoor unit."""

    entity_description: HeatLinkNumberDescription

    def __init__(
        self,
        runtime: HeatLinkRuntime,
        description: HeatLinkNumberDescription,
        source: Callable[[HeatLinkOutdoorEntity], Any],
        setter: str,
    ) -> None:
        super().__init__(runtime, description.key)
        self.entity_description = description
        self._source = source
        self._setter = setter

    @property
    def available(self) -> bool:
        return self._source(self) is not None

    @property
    def native_value(self) -> float | None:
        return self._read(self._source(self))

    async def async_set_native_value(self, value: float) -> None:
        d = self.entity_description
        setter = getattr(self.writer.client, self._setter)
        await self.async_write_shared(setter(**{d.field: self._api_value(value)}))
