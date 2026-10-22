"""Base entities for HeatLink heads and outdoor units."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyheatlink import (
    Group,
    HeatLinkConflictError,
    HeatLinkError,
    HeatLinkResponseError,
    HeatLinkScopeError,
    ParkConfig,
)

from .const import DOMAIN
from .coordinator import HeatLinkCoordinator
from .outdoor import outdoor_signal

if TYPE_CHECKING:
    from .runtime import HeatLinkRuntime


async def async_call[T](coro: Awaitable[T]) -> T:
    """Run a client call, turning library errors into readable HA errors."""
    try:
        return await coro
    except HeatLinkScopeError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="scope") from err
    except HeatLinkConflictError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="conflict",
            translation_placeholders={"message": err.message or str(err)},
        ) from err
    except HeatLinkResponseError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="rejected",
            translation_placeholders={"message": err.message or str(err)},
        ) from err
    except HeatLinkError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="unreachable",
            translation_placeholders={"error": str(err) or type(err).__name__},
        ) from err


class HeatLinkEntity[T](CoordinatorEntity[HeatLinkCoordinator[T]]):
    """An entity of one head, fed by one of its coordinators."""

    _attr_has_entity_name = True

    def __init__(
        self, runtime: HeatLinkRuntime, coordinator: HeatLinkCoordinator[T], key: str
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.uid}_{key}"
        self._attr_device_info = runtime.device_info

    @property
    def data(self) -> T:
        return self.coordinator.data


class HeatLinkUnitEntity[T](HeatLinkEntity[T]):
    """Unit telemetry: also unavailable while the head can't talk to the unit."""

    @property
    def available(self) -> bool:
        settings = self.runtime.settings
        return (
            super().available
            and settings.last_update_success
            and settings.data is not None
            and settings.data.connected
        )


class HeatLinkOutdoorEntity(Entity):
    """An entity of the outdoor unit, read from whichever member is reachable."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: HeatLinkRuntime, key: str) -> None:
        assert runtime.ghash is not None
        self.ghash = runtime.ghash
        self.runtime = runtime
        self._attr_unique_id = f"outdoor_{self.ghash}_{key}"
        # The outdoor device may belong to another head's config entry, so
        # attach to it directly rather than through device info.
        if runtime.outdoor_device_id is not None:
            self.device_entry = dr.async_get(runtime.hass).async_get(runtime.outdoor_device_id)

    @property
    def source(self) -> HeatLinkRuntime | None:
        return self.runtime.outdoor.best(self.ghash)

    @property
    def group_data(self) -> Group | None:
        rt = self.source
        return rt.group.data if rt is not None and rt.group is not None else None

    @property
    def park(self) -> ParkConfig | None:
        for rt in self.runtime.outdoor.members(self.ghash):
            if rt.park is not None and rt.park.last_update_success and rt.park.data:
                return rt.park.data
        return None

    @property
    def writer(self) -> HeatLinkRuntime:
        """A reachable admin member to send shared-setting writes to."""
        for rt in self.runtime.outdoor.members(self.ghash):
            if rt.admin and rt.settings.last_update_success:
                return rt
        return self.runtime

    @property
    def available(self) -> bool:
        return self.group_data is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, outdoor_signal(self.ghash), self._async_updated)
        )

    @callback
    def _async_updated(self) -> None:
        self.async_write_ha_state()

    async def async_write_shared(self, coro: Awaitable[Any]) -> None:
        await async_call(coro)
        await self.runtime.outdoor.async_refresh(self.ghash, park=True, group=True)
