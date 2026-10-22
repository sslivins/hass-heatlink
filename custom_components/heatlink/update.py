"""Firmware update entity (admin tokens only)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyheatlink import HeatLinkConflictError, HeatLinkError, UpdateInfo

from . import HeatLinkConfigEntry
from .const import DOMAIN, OTA_POLL_INTERVAL
from .entity import HeatLinkEntity, async_call
from .runtime import HeatLinkRuntime

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

INSTALL_TIMEOUT = 600.0
#: The head goes quiet while it flashes and reboots; this many failed polls
#: in a row after the download started means it is restarting.
REBOOT_POLLS = 5
#: How long to wait for the restarted head to answer with its new version.
COME_BACK_TIMEOUT = 180.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if runtime.update is not None:
        async_add_entities([HeatLinkFirmwareUpdate(runtime)])


class HeatLinkFirmwareUpdate(HeatLinkEntity[UpdateInfo], UpdateEntity):
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS

    def __init__(self, runtime: HeatLinkRuntime) -> None:
        assert runtime.update is not None
        super().__init__(runtime, runtime.update, "firmware")

    @property
    def available(self) -> bool:
        # The check result is still meaningful while a single poll fails.
        return self.data is not None

    @property
    def installed_version(self) -> str | None:
        status = self.runtime.status.data
        if status is not None and status.version:
            return status.version
        return self.data.current if self.data is not None else None

    @property
    def latest_version(self) -> str | None:
        info = self.data
        if info is None:
            return None
        if info.update_available and info.latest:
            return info.latest
        return self.installed_version

    @property
    def release_url(self) -> str | None:
        return self.data.release_url or None if self.data is not None else None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        if self.data is None or not self.data.update_available:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_update")
        before = self.installed_version
        target = self.data.latest
        self._attr_in_progress = True
        self._attr_update_percentage = None
        self.async_write_ha_state()
        try:
            try:
                await async_call(self.runtime.client.install_update())
            except HomeAssistantError as err:
                if isinstance(err.__cause__, HeatLinkConflictError):
                    raise HomeAssistantError(
                        translation_domain=DOMAIN, translation_key="update_busy"
                    ) from err
                raise
            await self._async_wait_flash()
            await self._async_wait_back(before, target)
        finally:
            self._attr_in_progress = False
            self._attr_update_percentage = None
            self.async_write_ha_state()

    async def _async_wait_flash(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + INSTALL_TIMEOUT
        quiet = 0
        while loop.time() < deadline:
            await asyncio.sleep(OTA_POLL_INTERVAL.total_seconds())
            try:
                ota = await self.runtime.client.get_ota_status()
            except HeatLinkError:
                quiet += 1
                if quiet >= REBOOT_POLLS:
                    return
                continue
            quiet = 0
            if ota.state == "failed":
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="update_failed",
                    translation_placeholders={"message": ota.message or "unknown error"},
                )
            if ota.state == "success":
                return
            if ota.state == "in_progress":
                self._attr_update_percentage = ota.progress
                self.async_write_ha_state()
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="update_timeout")

    async def _async_wait_back(self, before: str | None, target: str | None) -> None:
        """Confirm the head came back on the new firmware."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + COME_BACK_TIMEOUT
        version: str | None = None
        while loop.time() < deadline:
            await asyncio.sleep(OTA_POLL_INTERVAL.total_seconds() * 2)
            try:
                version = (await self.runtime.client.get_status()).version
            except HeatLinkError:
                continue
            if version != before:
                break
        issue_id = f"update_failed_{self.runtime.uid}"
        if version is None or version == before:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="update_failed",
                translation_placeholders={
                    "name": self.runtime.name,
                    "version": before or "?",
                    "target": target or "?",
                },
            )
            _LOGGER.warning(
                "%s is still on %s after installing %s", self.runtime.name, before, target
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        await self.runtime.status.async_request_refresh()
        await self.coordinator.async_request_refresh()
