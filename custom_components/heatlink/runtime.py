"""Per-head runtime: client, coordinators, devices and background tasks."""

from __future__ import annotations

import logging
import zlib
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from pyheatlink import (
    Feature,
    Group,
    HeatLinkClient,
    HeatLinkError,
    HeatLinkScopeError,
    Info,
    OtaStatus,
    ParkConfig,
    Settings,
    Status,
    UpdateInfo,
)

from .const import (
    CONF_FAST_POLL,
    CONFIG_INTERVAL,
    DOMAIN,
    EVENT_FAULT,
    GROUP_INTERVAL,
    MANUFACTURER,
    SETTINGS_INTERVAL,
    SETTINGS_INTERVAL_FAST,
    SHARED_PARK_FIELDS,
    STATUS_INTERVAL,
    UPDATE_INTERVAL,
)
from .coordinator import HeatLinkCoordinator
from .events import EventPoller
from .remote_temp import RemoteTempBinder

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .outdoor import OutdoorUnits

_LOGGER = logging.getLogger(__name__)


def outdoor_identifier(ghash: str) -> tuple[str, str]:
    return (DOMAIN, f"outdoor_{ghash}")


def _jitter(uid: str) -> timedelta:
    """Spread polls of different heads apart (stable per head)."""
    return timedelta(milliseconds=zlib.crc32(uid.encode()) % 1500)


class HeatLinkRuntime:
    """Everything one config entry (one head) needs at runtime."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: HeatLinkClient,
        info: Info,
        outdoor: OutdoorUnits,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.info = info
        self.outdoor = outdoor
        self.uid = info.uid
        self.ghash: str | None = (
            info.group.ghash if info.group is not None and info.has(Feature.GROUP) else None
        )
        self.owns_outdoor_unit = False
        self.ota: OtaStatus | None = None
        self.device_id: str | None = None
        self.outdoor_device_id: str | None = None
        self._fault: tuple[bool, str | None] | None = None
        self._uptime: int | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

        jitter = _jitter(self.uid)
        fast = entry.options.get(CONF_FAST_POLL, False)
        self.settings: HeatLinkCoordinator[Settings] = HeatLinkCoordinator(
            hass,
            entry,
            "settings",
            (SETTINGS_INTERVAL_FAST if fast else SETTINGS_INTERVAL) + jitter,
            client.get_settings,
        )
        self.status: HeatLinkCoordinator[Status] = HeatLinkCoordinator(
            hass, entry, "status", STATUS_INTERVAL + jitter, client.get_status
        )
        self.park: HeatLinkCoordinator[ParkConfig] | None = (
            HeatLinkCoordinator(hass, entry, "park", CONFIG_INTERVAL + jitter, client.get_park)
            if info.has(Feature.PARK)
            else None
        )
        self.group: HeatLinkCoordinator[Group] | None = (
            HeatLinkCoordinator(hass, entry, "group", GROUP_INTERVAL + jitter, client.get_group)
            if self.ghash is not None
            else None
        )
        #: Whether the token has admin scope (settings writes, update, events).
        self.admin = False
        self.update: HeatLinkCoordinator[UpdateInfo] | None = None
        self.events: EventPoller | None = None
        self.remote_temp = RemoteTempBinder(self) if info.has(Feature.REMOTE_TEMP) else None

    # ── identity ────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.info.name or f"HeatLink {self.uid}"

    @property
    def host(self) -> str:
        return self.client.host

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self.uid)})

    @property
    def tandem_supported(self) -> bool:
        return self.ghash is not None and self.info.has(Feature.TANDEM)

    @property
    def park_shared(self) -> frozenset[str]:
        """Off-at-setpoint settings owned by the outdoor unit rather than this head.

        Decides which device the entities live on, so it must not flip when the
        park config simply hasn't loaded yet.
        """
        if self.park is None or self.ghash is None:
            return frozenset()
        if self.park.data is None:
            return SHARED_PARK_FIELDS
        return self.park.data.shared_fields if self.park.data.shared else frozenset()

    # ── lifecycle ───────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        """First refresh. Raises ConfigEntryNotReady/AuthFailed for the climate data."""
        await self.settings.async_config_entry_first_refresh()
        await self._async_probe_admin()
        for coordinator in (self.status, self.park, self.group):
            if coordinator is not None:
                await coordinator.async_refresh()
        self._fault = self._fault_key(self.settings.data)
        self._unsubs.append(self.settings.async_add_listener(self._async_settings_updated))
        for coordinator in (self.park, self.group):
            if coordinator is not None:
                self._unsubs.append(
                    coordinator.async_add_listener(lambda: self.outdoor.notify(self.ghash))
                )
        if self.status.data is not None:
            self._uptime = self.status.data.uptime_s
        self._unsubs.append(self.status.async_add_listener(self._async_status_updated))

    async def _async_probe_admin(self) -> None:
        try:
            update = await self.client.get_update()
        except HeatLinkScopeError:
            _LOGGER.info(
                "%s: token is not admin; update, events and settings controls are disabled",
                self.name,
            )
            return
        except HeatLinkError:
            # Unreachable right now; assume admin so the entities exist and
            # simply stay unavailable until the head answers.
            update = None
        self.admin = True
        self.update = HeatLinkCoordinator(
            self.hass, self.entry, "update", UPDATE_INTERVAL, self._async_fetch_update
        )
        if update is not None:
            self.update.async_set_updated_data(update)
        if self.info.has(Feature.EVENTS):
            self.events = EventPoller(self)

    @callback
    def async_register_devices(self) -> None:
        registry = dr.async_get(self.hass)
        self.outdoor_device_id = None
        if self.ghash is not None:
            self.outdoor_device_id = self._async_outdoor_device(registry).id
        device = registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, self.uid)},
            manufacturer=MANUFACTURER,
            model="HeatLink",
            model_id=self.info.model or None,
            name=self.name,
            sw_version=self.info.fw,
            serial_number=self.uid,
            configuration_url=f"http://{self.host}",
            via_device_id=self.outdoor_device_id,
        )
        self.device_id = device.id
        self._async_check_mqtt_issue()

    def _async_outdoor_device(self, registry: dr.DeviceRegistry) -> dr.DeviceEntry:
        """The one outdoor-unit device all heads on it share.

        Devices belong to a single config entry, so reuse the outdoor device
        whichever head created it rather than giving every head its own.
        """
        assert self.ghash is not None
        identifier = outdoor_identifier(self.ghash)
        get_devices = getattr(registry, "async_get_devices", None)
        if get_devices is not None:
            existing = get_devices(identifiers={identifier})
            if existing:
                return existing[0]
        else:  # Home Assistant before per-entry device identifiers
            device = registry.async_get_device(identifiers={identifier})
            if device is not None:
                return device
        label = self.info.group.label if self.info.group else ""
        return registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={identifier},
            manufacturer=MANUFACTURER,
            model="Outdoor unit",
            name=label or "Outdoor unit",
        )

    async def async_start(self) -> None:
        if self.events is not None:
            await self.events.async_start()
        if self.remote_temp is not None:
            await self.remote_temp.async_start()

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self.events is not None:
            self.events.async_stop()
        if self.remote_temp is not None:
            await self.remote_temp.async_stop()
        for coordinator in (self.settings, self.status, self.park, self.group, self.update):
            if coordinator is not None:
                await coordinator.async_shutdown()

    async def async_command_done(self, *, park: bool = False, group: bool = False) -> None:
        """Pick up the head's new state right after a command (never optimistic)."""
        if self.ghash is not None and (park or group):
            await self.outdoor.async_refresh(self.ghash, park=park, group=group)
            return
        if park and self.park is not None:
            await self.park.async_request_refresh()
        await self.settings.async_request_refresh()

    # ── listeners ───────────────────────────────────────────────────────

    async def _async_fetch_update(self) -> UpdateInfo:
        await self._async_refresh_info()
        return await self.client.get_update()

    async def _async_refresh_info(self) -> None:
        info = await self.client.get_info()
        if info.uid == self.uid:
            self.info = info
            self._async_check_mqtt_issue()

    async def _async_refresh_info_quietly(self) -> None:
        try:
            await self._async_refresh_info()
        except HeatLinkError as err:
            _LOGGER.debug("%s: could not re-read /api/info after a reboot: %s", self.name, err)

    @callback
    def _async_status_updated(self) -> None:
        """Keep the device name and firmware version in step with the head."""
        status = self.status.data
        if status is None:
            return
        # Settings such as MQTT discovery change only with a reboot.
        if self._uptime is not None and status.uptime_s < self._uptime:
            self.entry.async_create_background_task(
                self.hass, self._async_refresh_info_quietly(), f"heatlink {self.uid} info"
            )
        self._uptime = status.uptime_s
        if self.device_id is None:
            return
        registry = dr.async_get(self.hass)
        device = registry.async_get(self.device_id)
        if not isinstance(device, dr.DeviceEntry):
            return
        changes: dict[str, str] = {}
        if status.name and status.name != device.name:
            changes["name"] = status.name
        if status.version and status.version != device.sw_version:
            changes["sw_version"] = status.version
        if changes:
            registry.async_update_device(self.device_id, **changes)  # type: ignore[arg-type]

    @staticmethod
    def _fault_key(settings: Settings | None) -> tuple[bool, str | None] | None:
        if settings is None or not settings.connected:
            return None
        return (settings.fault, settings.error_code if settings.fault else None)

    @callback
    def _async_settings_updated(self) -> None:
        key = self._fault_key(self.settings.data)
        if key is None or key == self._fault:
            return
        previous, self._fault = self._fault, key
        if previous is None:
            return
        self.hass.bus.async_fire(
            EVENT_FAULT,
            {
                "device_id": self.device_id,
                "uid": self.uid,
                "name": self.name,
                "fault": key[0],
                "code": key[1],
                "previous_code": previous[1],
            },
        )

    @callback
    def _async_check_mqtt_issue(self) -> None:
        issue_id = f"mqtt_discovery_{self.uid}"
        if self.info.mqtt is not None and self.info.mqtt.discovery:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="mqtt_discovery",
                translation_placeholders={"name": self.name, "url": f"http://{self.host}"},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
