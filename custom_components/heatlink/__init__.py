"""The HeatLink integration: local control of Mitsubishi mini-split heads."""

from __future__ import annotations

import logging
from pathlib import Path

try:
    import probatio as vol
except ImportError:  # Home Assistant before the move to probatio
    import voluptuous as vol  # type: ignore[no-redef]
from homeassistant.components.climate import ATTR_TEMPERATURE
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import (
    SOURCE_INTEGRATION_DISCOVERY,
    SOURCE_ZEROCONF,
    ConfigEntry,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import discovery_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from pyheatlink import HeatLinkClient, HeatLinkError, HeatLinkUnsupportedError

from .const import (
    ATTR_TTL,
    CARD_FILENAME,
    CARD_URL_BASE,
    CARD_VERSION,
    CONF_GHASH,
    CONF_TOKEN,
    CONF_UID,
    DOMAIN,
    MIN_API_VERSION,
    PLATFORMS,
    REMOTE_TEMP_TTL,
    SERVICE_SET_REMOTE_TEMPERATURE,
)
from .entity import async_call
from .outdoor import OutdoorUnits
from .runtime import HeatLinkRuntime

_LOGGER = logging.getLogger(__name__)

type HeatLinkConfigEntry = ConfigEntry[HeatLinkRuntime]

FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_REMOTE_TEMPERATURE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(ATTR_TEMPERATURE): vol.Any(None, vol.Coerce(float)),
        vol.Optional(ATTR_TTL, default=REMOTE_TEMP_TTL): vol.All(
            vol.Coerce(int), vol.Range(min=60, max=3600)
        ),
    }
)


def outdoor_units(hass: HomeAssistant) -> OutdoorUnits:
    units: OutdoorUnits | None = hass.data.get(DOMAIN)
    if units is None:
        units = hass.data[DOMAIN] = OutdoorUnits(hass)
    return units


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    outdoor_units(hass)

    async def set_remote_temperature(call: ServiceCall) -> None:
        registry = er.async_get(hass)
        temp: float | None = call.data.get(ATTR_TEMPERATURE)
        for entity_id in call.data[ATTR_ENTITY_ID]:
            reg_entry = registry.async_get(entity_id)
            entry = (
                hass.config_entries.async_get_entry(reg_entry.config_entry_id)
                if reg_entry is not None and reg_entry.config_entry_id
                else None
            )
            if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="not_a_head",
                    translation_placeholders={"entity_id": entity_id},
                )
            runtime: HeatLinkRuntime = entry.runtime_data
            if temp is None:
                await async_call(runtime.client.clear_remote_temp())
            else:
                await async_call(runtime.client.set_remote_temp(temp, ttl_s=call.data[ATTR_TTL]))
            await runtime.settings.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_REMOTE_TEMPERATURE,
        set_remote_temperature,
        schema=SET_REMOTE_TEMPERATURE_SCHEMA,
    )
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled dashboard cards once per Home Assistant instance."""
    if hass.data.get(FRONTEND_REGISTERED) or "frontend" not in hass.config.components:
        return
    hass.data[FRONTEND_REGISTERED] = True
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_BASE, str(Path(__file__).parent / "www"), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{CARD_URL_BASE}/{CARD_FILENAME}?v={CARD_VERSION}")


async def async_setup_entry(hass: HomeAssistant, entry: HeatLinkConfigEntry) -> bool:
    try:
        await _async_register_card(hass)
    except Exception:  # noqa: BLE001 - the cards must never block setup
        _LOGGER.warning("Could not register the HeatLink dashboard cards", exc_info=True)

    client = HeatLinkClient(
        entry.data[CONF_HOST], async_get_clientsession(hass), token=entry.data[CONF_TOKEN]
    )
    try:
        info = await client.get_info()
    except HeatLinkUnsupportedError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="firmware_too_old"
        ) from err
    except HeatLinkError as err:
        raise ConfigEntryNotReady(str(err)) from err
    if info.uid != entry.unique_id:
        # The address now belongs to another head (DHCP); wait for zeroconf
        # to report where this one went.
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="wrong_device",
            translation_placeholders={"host": client.host, "uid": info.uid},
        )
    if info.api_version < MIN_API_VERSION:
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="firmware_too_old")

    outdoor = outdoor_units(hass)
    runtime = HeatLinkRuntime(hass, entry, client, info, outdoor)
    try:
        await runtime.async_setup()
    except Exception:
        await runtime.async_shutdown()
        raise

    runtime.owns_outdoor_unit = outdoor.add(runtime)
    entry.runtime_data = runtime
    try:
        runtime.async_register_devices()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await runtime.async_start()
    except Exception:
        _async_forget(hass, runtime)
        await runtime.async_shutdown()
        raise
    _async_rediscover_peers(hass, runtime)
    return True


@callback
def _async_rediscover_peers(hass: HomeAssistant, runtime: HeatLinkRuntime) -> None:
    """Restart discovered heads waiting for a token so they offer one-click setup."""
    ghash = runtime.info.group.ghash if runtime.info.group else None
    if not ghash or not runtime.admin:
        return
    configured = {e.unique_id for e in hass.config_entries.async_entries(DOMAIN)}
    for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN):
        context = flow["context"]
        if (
            flow.get("step_id") != "token"
            or context.get("unique_id") in configured
            or context.get("source") not in (SOURCE_ZEROCONF, SOURCE_INTEGRATION_DISCOVERY)
            or context.get(CONF_GHASH) != ghash
        ):
            continue
        hass.config_entries.flow.async_abort(flow["flow_id"])
        discovery_flow.async_create_flow(
            hass,
            DOMAIN,
            {"source": SOURCE_INTEGRATION_DISCOVERY},
            {CONF_HOST: context[CONF_HOST], CONF_UID: context["unique_id"]},  # type: ignore[typeddict-item]
        )


def _async_forget(hass: HomeAssistant, runtime: HeatLinkRuntime) -> None:
    heir = outdoor_units(hass).remove(runtime)
    if heir is not None and not hass.is_stopping:
        # Another head on the same outdoor unit takes over its entities.
        hass.config_entries.async_schedule_reload(heir)


async def async_unload_entry(hass: HomeAssistant, entry: HeatLinkConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = entry.runtime_data
        _async_forget(hass, runtime)
        await runtime.async_shutdown()
    return unloaded


async def async_migrate_entry(hass: HomeAssistant, entry: HeatLinkConfigEntry) -> bool:
    """Config entry migrations. Version 1 is the first schema."""
    if entry.version > 1:
        return False
    return True
