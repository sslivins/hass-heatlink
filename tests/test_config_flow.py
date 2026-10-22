"""Config, reauth, reconfigure and options flows."""

from __future__ import annotations

from ipaddress import ip_address
from unittest.mock import MagicMock

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pyheatlink import HeatLinkAuthError, HeatLinkConnectionError, HeatLinkUnsupportedError

from custom_components.heatlink.const import (
    CONF_FAST_POLL,
    CONF_GHASH,
    CONF_TOKEN,
    CONF_UID,
    DOMAIN,
)

from .conftest import FakeHead, Heads, setup_head


def zeroconf(host: str = "192.168.1.151", uid: str = "6dac") -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        hostname=f"heatlink-{uid}.local.",
        name=f"heatlink-{uid}._mmhvac._tcp.local.",
        port=80,
        type="_mmhvac._tcp.local.",
        properties={"uid": uid, "pv": "1"},
    )


async def test_user_flow(hass: HomeAssistant, heads: Heads, client: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: " 192.168.1.151 "}
    )
    assert result["step_id"] == "token"

    parse = client.get_settings.side_effect
    client.get_settings.side_effect = HeatLinkAuthError("nope")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "bad"})
    assert result["errors"] == {"base": "invalid_auth"}

    client.get_settings.side_effect = parse
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "tok"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bedroom 1"
    assert result["data"] == {
        CONF_HOST: "192.168.1.151",
        CONF_TOKEN: "tok",
        CONF_UID: "6dac",
        CONF_GHASH: "bb66e0db",
    }
    assert result["result"].unique_id == "6dac"


async def test_user_flow_errors(hass: HomeAssistant, heads: Heads, client: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    parse = client.get_info.side_effect
    client.get_info.side_effect = HeatLinkUnsupportedError("old")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.151"}
    )
    assert result["errors"] == {"base": "firmware_too_old"}
    client.get_info.side_effect = HeatLinkConnectionError("down")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.151"}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    client.get_info.side_effect = parse


async def test_zeroconf_flow(hass: HomeAssistant, heads: Heads) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=zeroconf()
    )
    assert result["step_id"] == "token"
    assert result["description_placeholders"]["name"] == "Bedroom 1"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "tok"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_zeroconf_rejects_mismatched_uid(hass: HomeAssistant, heads: Heads) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=zeroconf(uid="ffff")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_heatlink"


async def test_zeroconf_updates_host(hass: HomeAssistant, heads: Heads, head: FakeHead) -> None:
    entry = await setup_head(hass, head)
    moved = heads.add(FakeHead(host="192.168.1.99"))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=zeroconf(moved.host)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.99"


async def test_group_peer_adds_in_one_click(
    hass: HomeAssistant, heads: Heads, head: FakeHead, client: MagicMock
) -> None:
    await setup_head(hass, head)
    peer = heads.add(FakeHead(uid="e6ec", name="Bedroom 2", host="192.168.1.143"))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=zeroconf(peer.host, "e6ec")
    )
    assert result["step_id"] == "group_confirm"
    assert result["description_placeholders"]["peer"] == "Bedroom 1"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOKEN] == "peer-token"
    client.create_peer_token.assert_awaited_once_with("192.168.1.143", "e6ec")


async def test_group_peer_falls_back_to_token(
    hass: HomeAssistant, heads: Heads, head: FakeHead, client: MagicMock
) -> None:
    await setup_head(hass, head)
    peer = heads.add(FakeHead(uid="e6ec", name="Bedroom 2", host="192.168.1.143"))
    client.create_peer_token.side_effect = HeatLinkConnectionError("down")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=zeroconf(peer.host, "e6ec")
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "token"


async def test_reauth(hass: HomeAssistant, heads: Heads, head: FakeHead) -> None:
    entry = await setup_head(hass, head)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "new"


async def test_reconfigure_wrong_device(hass: HomeAssistant, heads: Heads, head: FakeHead) -> None:
    entry = await setup_head(hass, head)
    other = heads.add(FakeHead(uid="e6ec", host="192.168.1.143"))
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: other.host}
    )
    assert result["errors"] == {"base": "wrong_device"}
    moved = heads.add(FakeHead(host="192.168.1.77"))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: moved.host}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.168.1.77"


async def test_options(hass: HomeAssistant, heads: Heads, head: FakeHead) -> None:
    entry = await setup_head(hass, head)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_FAST_POLL: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_FAST_POLL] is True
    assert entry.runtime_data.settings.update_interval.total_seconds() < 7
