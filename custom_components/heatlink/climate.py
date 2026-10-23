"""Climate entity for a HeatLink head."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pyheatlink import Settings

from . import HeatLinkConfigEntry
from .entity import HeatLinkUnitEntity, async_call
from .runtime import HeatLinkRuntime

PARALLEL_UPDATES = 1

MODE_TO_HVAC = {
    "HEAT": HVACMode.HEAT,
    "COOL": HVACMode.COOL,
    "DRY": HVACMode.DRY,
    "FAN": HVACMode.FAN_ONLY,
    "AUTO": HVACMode.HEAT_COOL,
}
HVAC_TO_MODE = {v: k for k, v in MODE_TO_HVAC.items()}

FAN_MODES = ["auto", "quiet", "1", "2", "3", "4"]
SWING_MODES = ["auto", "1", "2", "3", "4", "5", "swing"]
# The head's wide-vane values, keyed by the HA mode (translation keys must be slugs).
WIDE_VANE = {
    "far_left": "<<",
    "left": "<",
    "center": "|",
    "right": ">",
    "far_right": ">>",
    "split": "<>",
    "swing": "swing",
}
WIDE_VANE_MODES = list(WIDE_VANE)
WIDE_VANE_FROM_HEAD = {v: k for k, v in WIDE_VANE.items()}

MIN_TEMP = 16.0
MAX_TEMP = 31.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HeatLinkClimate(entry.runtime_data)])


#: Countdowns become "at" times that move only when the head's estimate does,
#: so the climate state isn't rewritten on every poll.
STABLE_SLACK_S = 90


def stable_at(previous: datetime | None, seconds: int | None) -> datetime | None:
    if seconds is None:
        return None
    at = (dt_util.utcnow() + timedelta(seconds=seconds)).replace(microsecond=0)
    if previous is not None and abs((at - previous).total_seconds()) <= STABLE_SLACK_S:
        return previous
    return at


def tandem_attributes(
    settings: Settings | None, times: dict[str, datetime | None]
) -> dict[str, Any]:
    """Park/Tandem state for the dashboard card (the climate card shows only Idle).

    ``times`` keeps the previous "at" values between calls.
    """
    park = settings.park if settings is not None else None
    if park is None:
        return {}
    tandem_at = stable_at(times.get("tandem_at"), park.tandem_eta_s if park.held else None)
    probe_at = stable_at(times.get("probe_at"), park.next_probe_s if park.parked else None)
    times["tandem_at"], times["probe_at"] = tandem_at, probe_at
    return {
        "park_enabled": park.enabled,
        "park_state": park.state,
        "parked": park.parked,
        "held": park.held,
        "tandem": park.tandem,
        "tandem_at": tandem_at.isoformat() if tandem_at else None,
        "tandem_next": park.tandem_next,
        "need": park.need,
        "checking": park.checking,
        "room_sensor": park.room_sensor,
        "next_probe_at": probe_at.isoformat() if probe_at else None,
    }


class HeatLinkClimate(HeatLinkUnitEntity[Settings], ClimateEntity):
    """One indoor head."""

    _attr_name = None
    _attr_translation_key = "heatlink"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_fan_modes = FAN_MODES
    _attr_swing_modes = SWING_MODES
    _unrecorded_attributes = frozenset({"tandem_at", "next_probe_at", "checking", "need"})

    def __init__(self, runtime: HeatLinkRuntime) -> None:
        super().__init__(runtime, runtime.settings, "climate")
        self._times: dict[str, datetime | None] = {}

    @property
    def target_temperature_step(self) -> float:
        # The head works in 0.5 °C steps; whole degrees suit °F displays.
        if (
            self.hass is not None
            and self.hass.config.units.temperature_unit != self.temperature_unit
        ):
            return 1.0
        return 0.5

    # ── state ───────────────────────────────────────────────────────────

    @property
    def _tandem_on(self) -> bool:
        park = self.data.park if self.data is not None else None
        if park is not None and park.slicing:
            return True
        group = self.runtime.group
        tandem = group.data.tandem if group is not None and group.data is not None else None
        return tandem is not None and tandem.enabled

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.DRY, HVACMode.FAN_ONLY]
        # Tandem takes turns between heat and cool itself; the unit's AUTO
        # would fight it, and the head rejects it.
        if not self._tandem_on or self.hvac_mode == HVACMode.HEAT_COOL:
            modes.insert(1, HVACMode.HEAT_COOL)
        return modes

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self.data is not None and self.data.wide_vane_supported:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        return features

    @property
    def swing_horizontal_modes(self) -> list[str] | None:
        return WIDE_VANE_MODES if self.data is not None and self.data.wide_vane_supported else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        s = self.data
        if s is None:
            return None
        if not s.is_on:
            return HVACMode.OFF
        return MODE_TO_HVAC.get(s.mode.upper())

    @property
    def hvac_action(self) -> HVACAction | None:
        s = self.data
        if s is None:
            return None
        if not s.is_on:
            return HVACAction.OFF
        park = s.park
        if park is not None and (park.held or park.parked or park.state == "probing"):
            # Parked at setpoint or waiting for a Tandem turn; an air check
            # runs the fan to read the room.
            return HVACAction.FAN if park.checking or park.state == "probing" else HVACAction.IDLE
        sub = (s.sub_mode or "").upper()
        if sub == "DEFROST":
            return HVACAction.DEFROSTING
        if sub in ("PREHEAT", "WARMUP"):
            return HVACAction.PREHEATING
        mode = s.mode.upper()
        if mode == "FAN":
            return HVACAction.FAN
        if not s.operating:
            return HVACAction.IDLE
        if mode == "HEAT":
            return HVACAction.HEATING
        if mode == "COOL":
            return HVACAction.COOLING
        if mode == "DRY":
            return HVACAction.DRYING
        current, target = self.current_temperature, s.temperature
        if current is not None and target is not None and current > target:
            return HVACAction.COOLING
        return HVACAction.HEATING

    @property
    def current_temperature(self) -> float | None:
        s = self.data
        if s is None:
            return None
        rs = s.room_sensor
        if rs is not None and rs.state == "ok" and rs.temp is not None:
            return round(rs.temp, 1)
        return s.room_temperature

    @property
    def target_temperature(self) -> float | None:
        return self.data.temperature if self.data is not None else None

    @property
    def fan_mode(self) -> str | None:
        return self.data.fan.lower() if self.data is not None and self.data.fan else None

    @property
    def swing_mode(self) -> str | None:
        return self.data.vane.lower() if self.data is not None and self.data.vane else None

    @property
    def swing_horizontal_mode(self) -> str | None:
        if self.data is None or not self.data.wide_vane:
            return None
        return WIDE_VANE_FROM_HEAD.get(self.data.wide_vane.lower())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "heatlink_uid": self.runtime.uid,
            "outdoor_unit": self.runtime.ghash,
        }
        attrs.update(tandem_attributes(self.data, self._times))
        return attrs

    # ── commands ────────────────────────────────────────────────────────

    async def _async_send(self, **kwargs: Any) -> None:
        await async_call(self.runtime.client.set_climate(**kwargs))
        await self.runtime.async_command_done()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._async_send(power=False)
        else:
            await self._async_send(mode=HVAC_TO_MODE[hvac_mode])

    async def async_turn_on(self) -> None:
        await self._async_send(power=True)

    async def async_turn_off(self) -> None:
        await self._async_send(power=False)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        send: dict[str, Any] = {}
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            send["temperature"] = float(temp)
        if (mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            if mode == HVACMode.OFF:
                send["power"] = False
            else:
                send["mode"] = HVAC_TO_MODE[mode]
        if send:
            await self._async_send(**send)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self._async_send(fan=fan_mode)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self._async_send(vane=swing_mode)

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        await self._async_send(
            wide_vane=WIDE_VANE.get(swing_horizontal_mode, swing_horizontal_mode)
        )
