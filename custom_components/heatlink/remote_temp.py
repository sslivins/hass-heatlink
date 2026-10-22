"""Feed a Home Assistant temperature sensor to a head as its room temperature."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, State, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util.unit_conversion import TemperatureConverter
from pyheatlink import HeatLinkConflictError, HeatLinkError

from .const import CONF_REMOTE_TEMP_ENTITY, DOMAIN, REMOTE_TEMP_RENEW, REMOTE_TEMP_TTL

if TYPE_CHECKING:
    from .runtime import HeatLinkRuntime

_LOGGER = logging.getLogger(__name__)

MIN_C = -10.0
MAX_C = 50.0


def temperature_c(state: State | None) -> float | None:
    """A sensor state as °C, or None when it isn't a usable temperature."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit == UnitOfTemperature.FAHRENHEIT:
        value = TemperatureConverter.convert(
            value, UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS
        )
    elif unit not in (UnitOfTemperature.CELSIUS, None):
        return None
    if not MIN_C <= value <= MAX_C:
        return None
    return round(value, 1)


class RemoteTempBinder:
    """Keep the head's remote-temperature lease fed from an HA sensor.

    Sends on every state change and renews the lease well before it expires.
    When the sensor goes unavailable or the binding is removed, the lease is
    cleared so the head falls back to its own reading.
    """

    def __init__(self, runtime: HeatLinkRuntime) -> None:
        self._rt = runtime
        self.entity_id: str | None = runtime.entry.options.get(CONF_REMOTE_TEMP_ENTITY) or None
        self.last_sent: float | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._active = False

    @property
    def issue_id(self) -> str:
        return f"remote_temp_owned_{self._rt.uid}"

    async def async_start(self) -> None:
        if self.entity_id is None:
            ir.async_delete_issue(self._rt.hass, DOMAIN, self.issue_id)
            return
        self._unsubs.append(
            async_track_state_change_event(
                self._rt.hass, [self.entity_id], self._async_state_changed
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self._rt.hass,
                self._async_renew,
                REMOTE_TEMP_RENEW,
                name=f"{DOMAIN} remote temp {self._rt.uid}",
                cancel_on_shutdown=True,
            )
        )
        await self.async_push()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._active:
            await self._async_clear()

    async def _async_state_changed(self, _event: Event[EventStateChangedData]) -> None:
        await self.async_push()

    async def _async_renew(self, _now: datetime) -> None:
        await self.async_push()

    async def async_push(self) -> None:
        if self.entity_id is None:
            return
        temp = temperature_c(self._rt.hass.states.get(self.entity_id))
        if temp is None:
            if self._active:
                await self._async_clear()
            return
        try:
            await self._rt.client.set_remote_temp(temp, ttl_s=REMOTE_TEMP_TTL)
        except HeatLinkConflictError:
            # The head's own BLE room sensor owns the room temperature.
            self._active = False
            ir.async_create_issue(
                self._rt.hass,
                DOMAIN,
                self.issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="remote_temp_owned",
                translation_placeholders={"name": self._rt.name, "entity": self.entity_id},
            )
            return
        except HeatLinkError as err:
            _LOGGER.debug("%s: remote temperature not sent: %s", self._rt.name, err)
            return
        self._active = True
        self.last_sent = temp
        ir.async_delete_issue(self._rt.hass, DOMAIN, self.issue_id)

    async def _async_clear(self) -> None:
        self._active = False
        self.last_sent = None
        try:
            await self._rt.client.clear_remote_temp()
        except HeatLinkError as err:
            _LOGGER.debug("%s: remote temperature not cleared: %s", self._rt.name, err)

    @callback
    def describe(self) -> dict[str, object]:
        return {"entity_id": self.entity_id, "active": self._active, "last_sent": self.last_sent}
