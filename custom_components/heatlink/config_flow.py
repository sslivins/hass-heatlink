"""Config flow: add a HeatLink head by address or from zeroconf discovery."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

try:
    import probatio as vol
except ImportError:  # Home Assistant before the move to probatio
    import voluptuous as vol  # type: ignore[no-redef]
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pyheatlink import (
    HeatLinkAuthError,
    HeatLinkClient,
    HeatLinkError,
    HeatLinkScopeError,
    HeatLinkUnsupportedError,
    Info,
)

from .const import (
    CONF_FAST_POLL,
    CONF_GHASH,
    CONF_REMOTE_TEMP_ENTITY,
    CONF_TOKEN,
    CONF_UID,
    DOMAIN,
    MIN_API_VERSION,
)

if TYPE_CHECKING:
    from .runtime import HeatLinkRuntime

_LOGGER = logging.getLogger(__name__)


class HeatLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._info: Info | None = None
        self._peer: HeatLinkRuntime | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HeatLinkOptionsFlow:
        return HeatLinkOptionsFlow()

    # ── helpers ─────────────────────────────────────────────────────────

    def _client(self, host: str, token: str | None = None) -> HeatLinkClient:
        return HeatLinkClient(host, async_get_clientsession(self.hass), token=token)

    async def _async_info(self, host: str) -> tuple[Info | None, str | None]:
        try:
            info = await self._client(host).get_info()
        except HeatLinkUnsupportedError:
            return None, "firmware_too_old"
        except HeatLinkError:
            return None, "cannot_connect"
        if info.api_version < MIN_API_VERSION:
            return None, "firmware_too_old"
        return info, None

    async def _async_check_token(self, host: str, token: str) -> str | None:
        try:
            await self._client(host, token).get_settings()
        except HeatLinkAuthError, HeatLinkScopeError:
            return "invalid_auth"
        except HeatLinkError:
            return "cannot_connect"
        return None

    def _entry_data(self, token: str) -> dict[str, Any]:
        assert self._info is not None and self._host is not None
        return {
            CONF_HOST: self._host,
            CONF_TOKEN: token,
            CONF_UID: self._info.uid,
            CONF_GHASH: self._info.group.ghash if self._info.group else None,
        }

    def _title(self) -> str:
        assert self._info is not None
        return self._info.name or f"HeatLink {self._info.uid}"

    def _loaded_peer(self) -> HeatLinkRuntime | None:
        """A configured admin head on the same outdoor unit that can vouch for this one."""
        info = self._info
        if info is None or info.group is None or not info.group.ghash:
            return None
        units = self.hass.data.get(DOMAIN)
        if units is None:
            return None
        peer: HeatLinkRuntime | None = units.find_entry_for_ghash(info.group.ghash, info.uid)
        return peer if peer is not None and peer.admin else None

    # ── user / discovery ────────────────────────────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            info, error = await self._async_info(host)
            if info is None:
                errors["base"] = error or "cannot_connect"
            else:
                await self.async_set_unique_id(info.uid)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                self._host, self._info = host, info
                return await self._async_step_credentials()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HOST): str}), user_input
            ),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        uid = discovery_info.properties.get("uid")
        if not uid:
            return self.async_abort(reason="not_heatlink")
        return await self._async_discovered(str(discovery_info.ip_address), uid)

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Rediscovered once a peer on the same outdoor unit can vouch for this head."""
        return await self._async_discovered(discovery_info[CONF_HOST], discovery_info[CONF_UID])

    async def _async_discovered(self, host: str, uid: str) -> ConfigFlowResult:
        await self.async_set_unique_id(uid)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        info, error = await self._async_info(host)
        if info is None:
            return self.async_abort(reason=error or "cannot_connect")
        if info.uid != uid:
            return self.async_abort(reason="not_heatlink")
        self._host, self._info = host, info
        self.context["title_placeholders"] = {"name": self._title()}
        # Lets a head that loads later restart this flow as a one-click group confirm.
        self.context[CONF_HOST] = host  # type: ignore[typeddict-unknown-key]
        if info.group is not None and info.group.ghash:
            self.context[CONF_GHASH] = info.group.ghash  # type: ignore[typeddict-unknown-key]
        return await self._async_step_credentials()

    async def _async_step_credentials(self) -> ConfigFlowResult:
        self._peer = self._loaded_peer()
        if self._peer is not None:
            return await self.async_step_group_confirm()
        return await self.async_step_token()

    async def async_step_group_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One click: a configured head on the same outdoor unit mints the token."""
        assert self._info is not None and self._host is not None and self._peer is not None
        if user_input is not None:
            try:
                grant = await self._peer.client.create_peer_token(self._host, self._info.uid)
            except HeatLinkError as err:
                _LOGGER.info("Peer token from %s failed: %s", self._peer.name, err)
                return await self.async_step_token()
            return self.async_create_entry(title=self._title(), data=self._entry_data(grant.token))
        self._set_confirm_only()
        return self.async_show_form(
            step_id="group_confirm",
            description_placeholders={
                "name": self._title(),
                "host": self._host,
                "peer": self._peer.name,
                "label": self._info.group.label if self._info.group else "",
            },
        )

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._info is not None and self._host is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            error = await self._async_check_token(self._host, token)
            if error is None:
                return self.async_create_entry(title=self._title(), data=self._entry_data(token))
            errors["base"] = error
        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "name": self._title(),
                "url": f"http://{self._host}",
            },
        )

    # ── reauth / reconfigure ────────────────────────────────────────────

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        host = entry.data[CONF_HOST]
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            info, error = await self._async_info(host)
            if info is not None and info.uid != entry.unique_id:
                error = "wrong_device"
            if error is None:
                error = await self._async_check_token(host, token)
            if error is None:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title, "url": f"http://{host}"},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            info, error = await self._async_info(host)
            if info is not None and info.uid != entry.unique_id:
                error = "wrong_device"
            if error is None:
                error = await self._async_check_token(host, entry.data[CONF_TOKEN])
            if error is None:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host})
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HOST): str}),
                user_input or {CONF_HOST: entry.data[CONF_HOST]},
            ),
            errors=errors,
        )


class HeatLinkOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
                vol.Optional(CONF_FAST_POLL, default=False): bool,
                vol.Optional(CONF_REMOTE_TEMP_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
        )
