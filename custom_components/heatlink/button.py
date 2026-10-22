"""Buttons: restart and diagnostic counter resets (admin tokens only)."""

from __future__ import annotations

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyheatlink import Status

from . import HeatLinkConfigEntry
from .entity import HeatLinkEntity, async_call
from .runtime import HeatLinkRuntime

PARALLEL_UPDATES = 1

RESTART = ButtonEntityDescription(
    key="restart",
    device_class=ButtonDeviceClass.RESTART,
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
)

#: key -> reset_diag() counter
RESETS = {
    "reset_brownouts": "brownout",
    "reset_wifi_drops": "wifi-drops",
    "reset_supply_voltage_min": "vin-min",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if not runtime.admin:
        return
    entities = [HeatLinkButton(runtime, RESTART, None)]
    entities += [
        HeatLinkButton(
            runtime,
            ButtonEntityDescription(
                key=key,
                translation_key=key,
                entity_category=EntityCategory.DIAGNOSTIC,
                entity_registry_enabled_default=False,
            ),
            counter,
        )
        for key, counter in RESETS.items()
    ]
    async_add_entities(entities)


class HeatLinkButton(HeatLinkEntity[Status], ButtonEntity):
    def __init__(
        self,
        runtime: HeatLinkRuntime,
        description: ButtonEntityDescription,
        counter: str | None,
    ) -> None:
        super().__init__(runtime, runtime.status, description.key)
        self.entity_description = description
        self._counter = counter

    async def async_press(self) -> None:
        if self._counter is None:
            await async_call(self.runtime.client.restart())
            return
        await async_call(self.runtime.client.reset_diag(self._counter))
        await self.coordinator.async_request_refresh()
