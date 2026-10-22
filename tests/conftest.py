"""Fixtures for the HeatLink integration tests."""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pyheatlink import (
    EventLog,
    Group,
    Info,
    OtaStatus,
    ParkConfig,
    Settings,
    Status,
    UpdateInfo,
)
from pyheatlink.models import RemoteTempLease, TokenGrant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heatlink.const import CONF_GHASH, CONF_TOKEN, CONF_UID, DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Enable custom integration loading."""


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeHead:
    """Raw JSON for one head; tests edit the dicts, the client parses them."""

    def __init__(self, uid: str = "6dac", name: str = "Bedroom 1", host: str = "192.168.1.151"):
        self.host = host
        self.info = load("info")
        self.settings = load("settings")
        self.status = load("status")
        self.park = load("park")
        self.group = load("group")
        self.update = load("update")
        self.events = load("events")
        self.ota = load("ota_status")
        for d in (self.info, self.status):
            d["name"] = name
        self.info["uid"] = uid
        self.group["self"]["uid"] = uid
        self.group["self"]["name"] = name
        self.info["mqtt"]["discovery"] = False


def make_client(head: FakeHead) -> MagicMock:
    client = MagicMock()
    client.host = head.host
    client.token = "tok"
    client.get_info = AsyncMock(side_effect=lambda: Info.from_dict(copy.deepcopy(head.info)))
    client.get_settings = AsyncMock(side_effect=lambda: Settings.from_dict(head.settings))
    client.get_status = AsyncMock(side_effect=lambda: Status.from_dict(head.status))
    client.get_park = AsyncMock(side_effect=lambda: ParkConfig.from_dict(head.park))
    client.get_group = AsyncMock(side_effect=lambda: Group.from_dict(head.group))
    client.get_update = AsyncMock(side_effect=lambda: UpdateInfo.from_dict(head.update))
    client.get_ota_status = AsyncMock(side_effect=lambda: OtaStatus.from_dict(head.ota))
    client.get_events = AsyncMock(
        side_effect=lambda since=0, limit=100: EventLog.from_dict(head.events)
    )
    client.set_climate = AsyncMock(return_value=None)
    client.turn_on = AsyncMock(return_value=None)
    client.turn_off = AsyncMock(return_value=None)
    client.set_park = AsyncMock(side_effect=lambda **kw: ParkConfig.from_dict(head.park))
    client.set_tandem = AsyncMock(return_value=None)
    client.set_remote_temp = AsyncMock(
        return_value=RemoteTempLease.from_dict({"active": True, "temp": 20, "expires_in_s": 600})
    )
    client.clear_remote_temp = AsyncMock(return_value=RemoteTempLease.from_dict({"active": False}))
    client.install_update = AsyncMock(return_value=None)
    client.restart = AsyncMock(return_value=None)
    client.reset_diag = AsyncMock(return_value=None)
    client.create_peer_token = AsyncMock(
        return_value=TokenGrant.from_dict(
            {"token": "peer-token", "id": "t1", "label": "Home Assistant", "scope": "admin"}
        )
    )
    return client


class Heads:
    """Fake heads by host; ``HeatLinkClient(host, ...)`` returns that head's client."""

    def __init__(self) -> None:
        self.heads: dict[str, FakeHead] = {}
        self.clients: dict[str, MagicMock] = {}

    def add(self, head: FakeHead) -> FakeHead:
        self.heads[head.host] = head
        self.clients[head.host] = make_client(head)
        return head

    def client(self, host: str, *_args: Any, **_kwargs: Any) -> MagicMock:
        return self.clients[host]


@pytest.fixture
async def heads(hass: HomeAssistant) -> AsyncGenerator[Heads]:
    fake = Heads()
    fake.add(FakeHead())
    with (
        patch("custom_components.heatlink.HeatLinkClient", side_effect=fake.client),
        patch("custom_components.heatlink.config_flow.HeatLinkClient", side_effect=fake.client),
    ):
        yield fake
        # Unload while the fakes are still patched in: unloading a head can
        # reload the one that takes over its outdoor unit.
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.fixture
def head(heads: Heads) -> FakeHead:
    return heads.heads["192.168.1.151"]


@pytest.fixture
def client(heads: Heads) -> MagicMock:
    return heads.clients["192.168.1.151"]


def make_entry(head: FakeHead, **options: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=head.info["uid"],
        title=head.info["name"],
        data={
            CONF_HOST: head.host,
            CONF_TOKEN: "tok",
            CONF_UID: head.info["uid"],
            CONF_GHASH: head.info["group"]["ghash"],
        },
        options=options,
    )


async def setup_head(hass: HomeAssistant, head: FakeHead, **options: Any) -> MockConfigEntry:
    entry = make_entry(head, **options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
